# -*- coding: utf-8 -*-
"""
Vinted Sniper Engine v4.2 (Boti Arbitrage & Homelab Suite).
- Embudo en 3 fases: Pre-Filtro Determinista -> Visión Multimodal Gemini 2.5 Flash (3 fotos) -> Suelo de Amortización CeX España.
- Sticky Sessions realistas (rotación cada 120 rondas o tras cooldown/0 resultados) para evasión DataDome.
- Pausa orgánica previa a descarga de ficha individual + desescapado HTML/RSC completo de descripciones.
- Detector de Rebajas de Precio (>=15% y >=10€) + Bloqueo Anti-Repost.
"""

import os
import re
import html
import time
import random
import urllib.parse
from curl_cffi import requests

from antiban import BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL, AI_ANALYST_MODEL, PROXY_URL,
    init_db, load_config,
    check_item_seen_or_dropped, is_duplicate_recent_deal, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, download_item_images,
    analyze_and_generate_kit, evaluate_deal_math_and_score,
    build_kit_folder_name, send_discord_alert
)

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "45"))

rotator = BrowserProfileRotator(rotate_every_rounds=120)
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
health_monitor = AntiBanHealthMonitor("VINTED SNIPER")


class VintedClient:
    def __init__(self, proxy_url="", rotator=rotator, health_monitor=health_monitor):
        self.proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        self.rotator = rotator
        self.health_monitor = health_monitor
        self.session = None
        self.session_epoch = -1
        self.consecutive_empty = 0
        self._init_session()

    def _init_session(self):
        profile = self.rotator.get_chrome_profile()
        self.session = requests.Session(impersonate=profile["impersonate"], proxies=self.proxies)
        headers = {
            "User-Agent": profile["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
        }
        if profile.get("sec_ch_ua"):
            headers["sec-ch-ua"] = profile["sec_ch_ua"]
            headers["sec-ch-ua-platform"] = profile["sec_ch_ua_platform"]
            headers["sec-ch-ua-mobile"] = profile["sec_ch_ua_mobile"]
        self.session.headers.update(headers)
        self.session_epoch = self.rotator.rotation_epoch
        self.consecutive_empty = 0
        try:
            self.session.get("https://www.vinted.es/", timeout=10)
        except Exception:
            pass

    def ensure_fresh_session(self):
        if self.session is None or self.session_epoch != self.rotator.rotation_epoch or self.consecutive_empty >= 4:
            self._init_session()

    def search_items(self, query_text, min_price, max_price):
        self.ensure_fresh_session()
        params = {
            "search_text": query_text,
            "price_from": str(min_price),
            "price_to": str(max_price),
            "order": "newest_first"
        }
        url = f"https://www.vinted.es/catalog?{urllib.parse.urlencode(params)}"
        try:
            r = self.session.get(url, timeout=15)

            if r.status_code in (401, 403, 429):
                should_cd, cd_secs = self.health_monitor.handle_http_response(r.status_code, url=url, query=query_text)
                if should_cd:
                    print(f"[VINTED COOLDOWN] HTTP {r.status_code}. Pausando {cd_secs // 60} min para enfriar IP...")
                    time.sleep(cd_secs)
                    self.rotator.rotate()
                    self._init_session()
                    return []
                else:
                    self._init_session()
                    r = self.session.get(url, timeout=15)

            if r.status_code == 200:
                self.health_monitor.handle_http_response(200, query=query_text)
            else:
                print(f"[WARN] Vinted HTTP {r.status_code} buscando '{query_text}'")
                return []

            items_by_id = {}

            # 1. Parser HTML de tarjetas de catálogo
            pattern = re.compile(
                r'data-testid="product-item-id-(?P<id>\d+)"[^>]*>'
                r'.*?<img[^>]+src="(?P<img>https://images\d*\.vinted\.net/[^"]+)"[^>]*data-testid="product-item-id-(?P=id)--image--img"'
                r'.*?<a\s+href="(?P<link>(?:https://www\.vinted\.es)?/items/(?P=id)-[^"]*)"[^>]*title="(?P<title_attr>[^"]*)"',
                re.DOTALL
            )
            for m in pattern.finditer(r.text):
                item_id = m.group("id")
                img_url = m.group("img")
                link = m.group("link")
                title_attr = html.unescape(m.group("title_attr"))

                prices = re.findall(r"(\d+[\.,]\d{2})\s*€", title_attr)
                price = float(prices[0].replace(",", ".")) if prices else 0.0
                total_with_fee = float(prices[1].replace(",", ".")) if len(prices) > 1 else (price * 1.05 + 0.70)

                title = title_attr
                brand = ""
                status_cond = ""
                if ", Marca:" in title_attr:
                    parts = title_attr.split(", Marca:")
                    title = parts[0].strip()
                    rest = parts[1]
                    if ", Estado:" in rest:
                        brand_p, state_p = rest.split(", Estado:", 1)
                        brand = brand_p.strip()
                        status_cond = state_p.split(",")[0].strip()
                    else:
                        brand = rest.split(",")[0].strip()
                elif "," in title_attr:
                    title = title_attr.split(",")[0].strip()

                full_url = link if link.startswith("http") else f"https://www.vinted.es{link}"
                full_url = full_url.split("?")[0]
                checkout_url = full_url

                items_by_id[item_id] = {
                    "id": item_id,
                    "title": title,
                    "brand_title": brand,
                    "status": status_cond,
                    "price": price,
                    "total_item_price": total_with_fee,
                    "url": full_url,
                    "checkout_url": checkout_url,
                    "photo": img_url,
                    "raw_title": title_attr
                }

            # 2. Fallback / Enriquecimiento desde payload RSC de Next.js si el DOM cambiara
            if not items_by_id:
                rsc_pattern = re.compile(
                    r'\\"productItem\\":\{\\"id\\":(?P<id>\d+),\\"title\\":\\"(?P<title>[^\\"]+)\\",'
                    r'\\"url\\":\\"(?P<url>/items/\d+-[^\\"]*)\\".*?'
                    r'\\"price\\":\{\\"amount\\":\\"(?P<price>[\d\.]+)\\".*?'
                    r'\\"thumbnailUrl\\":\\"(?P<img>https://images\d*\.vinted\.net/[^\\"]+)\\"',
                    re.DOTALL
                )
                for m in rsc_pattern.finditer(r.text):
                    item_id = m.group("id")
                    price = float(m.group("price") or 0)
                    link = m.group("url")
                    items_by_id[item_id] = {
                        "id": item_id,
                        "title": html.unescape(m.group("title")),
                        "brand_title": "",
                        "status": "Muy bueno",
                        "price": price,
                        "total_item_price": round(price * 1.05 + 0.70, 2),
                        "url": f"https://www.vinted.es{link}",
                        "checkout_url": f"https://www.vinted.es{link}",
                        "photo": m.group("img"),
                        "raw_title": html.unescape(m.group("title"))
                    }

            items = list(items_by_id.values())
            if not items:
                self.consecutive_empty += 1
            else:
                self.consecutive_empty = 0
            return items
        except Exception as e:
            print(f"[ERROR] Error consultando Vinted '{query_text}': {e}")
            self.consecutive_empty += 1
            return []

    def fetch_item_details(self, item_url):
        try:
            r = self.session.get(item_url, timeout=12)
            if r.status_code in (403, 429):
                self.health_monitor.handle_http_response(r.status_code, url=item_url)
                return "", [], ""
            if r.status_code != 200:
                return "", [], ""

            m_desc = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', r.text)
            desc = html.unescape(m_desc.group(1)).strip() if m_desc else ""

            # Intentar extraer descripción completa desde el payload RSC de la ficha si existe
            m_rsc_desc = re.search(r'\\"description\\":\\"((?:\\\\.|[^"\\]){10,2000})\\"', r.text)
            if m_rsc_desc:
                try:
                    rsc_desc = html.unescape(m_rsc_desc.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")).strip()
                    if len(rsc_desc) > len(desc):
                        desc = rsc_desc
                except Exception:
                    pass

            photos = re.findall(r'href="(https://images\d*\.vinted\.net/t/[^"]+/f800/[^"]+)"', r.text)
            if not photos:
                photos = re.findall(r'"(https://images\d*\.vinted\.net/t/[^"]+/f800/[^"]+)"', r.text)
            clean_photos = [html.unescape(p) for p in list(dict.fromkeys(photos))[:4]]

            m_date = re.search(r'itemProp="upload_date"[^>]*>.*?<span[^>]*>([^<]+)</span>', r.text, re.DOTALL)
            upload_date = html.unescape(m_date.group(1)).strip() if m_date else ""
            return desc, clean_photos, upload_date
        except Exception:
            return "", [], ""


def is_vinted_item_fresh(upload_date):
    """
    Filtra cuentas inactivas / abandonadas en Vinted.
    Un chollo real en Vinted se vende en menos de 15-30 minutos.
    Cualquier anuncio con semanas, meses o años es un vendedor inactivo que no enviará el producto.
    """
    if not upload_date:
        return True
    ud = upload_date.lower().strip()
    stale_tokens = [
        "año", "an ", "ans", "anno", "anni", "year",
        "mes", "mois", "mese", "mesi", "month",
        "semana", "semaine", "settimana", "week"
    ]
    if any(st in ud for st in stale_tokens):
        return False
    day_match = re.search(r'(\d+)\s*(?:días|dias|jours|giorni|days)', ud)
    if day_match:
        try:
            return int(day_match.group(1)) <= 2
        except Exception:
            return False
    return True


def get_vinted_queries_for_target(target):
    if "vinted_keywords" in target:
        vk = target["vinted_keywords"]
        return vk if isinstance(vk, list) else [vk]
    return [target.get("keywords", "").strip()]


def parse_vinted_price(item):
    base_price = float(item.get("price", 0.0))
    buyer_fee = round((base_price * 0.05) + 0.70, 2)
    shipping_est = 3.29
    landed_cost = round(base_price + buyer_fee + shipping_est, 2)
    return base_price, landed_cost, round(buyer_fee + shipping_est, 2)


def send_startup_ping(total_targets, min_profit, min_score):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        return
    engine = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal 3-Fotos)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    msg = {
        "content": (
            f"💎 **Boti Arbitrage Sniper v4.2 (VINTED ENGINE • MULTIMODAL + RESPALDO CEX) ONLINE**\n"
            f"🧠 Motor IA: **`{engine}`** | Radares activos: **{total_targets}**\n"
            f"🔒 **Regla de Oro 99%:** Inspección visual de hasta 3 fotos reales + Suelo de Amortización CeX España + Rating Persistente."
        )
    }
    try:
        requests.post(WEBHOOK_URL, json=msg, timeout=10)
    except Exception:
        pass


def main():
    conn = init_db()
    vinted = VintedClient(proxy_url=PROXY_URL, rotator=rotator, health_monitor=health_monitor)
    config = load_config()

    targets = [t for t in config.get("targets", []) if t.get("enabled", True) and "vinted" in t.get("platforms", ["vinted"])]
    min_profit = float(config.get("min_net_profit_eur", 35))
    min_score = int(config.get("min_deal_score", 8))

    seen_cnt = count_platform_seen(conn, "VINTED")
    warmup = seen_cnt == 0
    item_eval_fails = {}
    round_num = 0

    engine_name = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    print(f"[STARTUP] Vinted Sniper v4.2 activo | {len(targets)} radares | {seen_cnt} en BD | IA: {engine_name}")
    send_startup_ping(len(targets), min_profit, min_score)

    while True:
        config = load_config()
        all_targets = config.get("targets", [])
        targets = [t for t in all_targets if t.get("enabled", True) and "vinted" in t.get("platforms", ["vinted"])]
        global_blacklist = config.get("global_blacklist", [])
        min_profit = float(config.get("min_net_profit_eur", 35))
        min_personal_savings = float(config.get("min_personal_savings_eur", 25))
        min_score = int(config.get("min_deal_score", 8))
        drop_pct = float(config.get("price_drop_threshold_pct", 15))
        drop_eur = float(config.get("price_drop_min_eur", 10))

        round_start = time.time()
        round_new = 0
        round_evaluated = 0
        round_total_fetched = 0

        for target in targets:
            if not timing.should_scan_target(target, round_num, warmup=warmup):
                continue

            queries = get_vinted_queries_for_target(target)
            target_items_count = 0
            target_new_count = 0
            mode = target.get("mode", "resale")

            for q in queries:
                items = vinted.search_items(q, target["min_price"], target["max_price"])
                target_items_count += len(items)
                round_total_fetched += len(items)

                for item in items:
                    item_id = item.get("id")
                    if not item_id:
                        continue

                    title = item.get("title", "")
                    brand = item.get("brand_title", "")
                    status_cond = item.get("status", "")
                    base_price, landed_cost, fees = parse_vinted_price(item)

                    already_seen, price_drop_info = check_item_seen_or_dropped(
                        conn, "VINTED", item_id, base_price, min_drop_pct=drop_pct, min_drop_eur=drop_eur
                    )
                    if already_seen:
                        continue

                    target_new_count += 1
                    round_new += 1

                    if warmup:
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    extra_meta_str = f"{brand} {status_cond}"
                    # FASE 1A: Pre-Filtro sobre título/marca antes de abrir la ficha
                    if not passes_filters(title, "", base_price, target, global_blacklist, extra_text=extra_meta_str):
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    if is_duplicate_recent_deal(conn, "VINTED", title, base_price):
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    time.sleep(random.uniform(1.2, 2.3))
                    desc, extra_photos, upload_date = vinted.fetch_item_details(item["url"])
                    if not is_vinted_item_fresh(upload_date):
                        print(f"[VINTED DESCARTADO - ANUNCIO ANTIGUO ({upload_date})] {title} ({base_price:.0f}€)")
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    # FASE 1B: Pre-Filtro sobre descripción completa desescapada
                    if not passes_filters(title, desc, base_price, target, global_blacklist, extra_text=extra_meta_str):
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    # FASE 2 ("El Punto Dulce"): Descargar hasta 3 fotos reales de alta resolución
                    photo_urls = extra_photos if extra_photos else ([item["photo"]] if item.get("photo") else [])
                    cover_url = photo_urls[0] if photo_urls else ""
                    raw_photos = download_item_images(photo_urls, rotator=rotator, max_images=3)

                    round_evaluated += 1
                    drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                    print(f"[EVALUANDO VINTED IA + {len(raw_photos)} FOTOS]{drop_tag} {title} ({base_price:.0f}€ | Casa: {landed_cost:.0f}€)...")
                    ai_data = analyze_and_generate_kit(
                        "VINTED", title, desc, base_price, landed_cost, False, target=target,
                        conn=conn, health_monitor=health_monitor,
                        extra_meta=f"(Marca: {brand} | Estado: {status_cond})",
                        raw_images=raw_photos
                    )

                    if not ai_data:
                        fail_cnt = item_eval_fails.get(item_id, 0) + 1
                        item_eval_fails[item_id] = fail_cnt
                        if fail_cnt >= 2:
                            mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    mark_item_seen(conn, "VINTED", item_id, title, base_price)

                    if not ai_data.get("is_valid_deal", False) or ai_data.get("is_pro_seller", False) or ai_data.get("has_defect_or_missing_parts", False):
                        reason = ai_data.get("reject_reason") or "Descartado en inspección visual IA"
                        print(f"[VINTED DESCARTADO IA] {title} ({base_price:.0f}€) -> {reason}")
                        continue

                    # FASE 3: Verificación CeX España en vivo + Regla de Amortización + Rating Determinista
                    cex_data = None
                    if mode == "resale":
                        cex_query = ai_data.get("cex_search_query") or ai_data.get("identified_model") or q
                        cex_data = fetch_cex_live(
                            conn, cex_query, reference_price=base_price, target=target,
                            title_context=title, desc_context=desc,
                            includes_box=bool(ai_data.get("includes_original_box", False)),
                            includes_lens=bool(ai_data.get("includes_kit_lens", True))
                        )

                    approved, score, optimal_price, best_net_profit, charger_penalty, reject_msg = evaluate_deal_math_and_score(
                        price=base_price, landed_cost=landed_cost, target=target, ai_data=ai_data, cex_data=cex_data,
                        min_profit=min_profit, min_personal_savings=min_personal_savings, min_score=min_score,
                        filter_pro_seller=True
                    )
                    if not approved:
                        print(f"[VINTED DESCARTADO - {score}/10] {title} ({base_price:.0f}€ | Casa: {landed_cost:.0f}€) -> {reject_msg}")
                        continue

                    kit_folder = build_kit_folder_name("VINTED", item_id, ai_data)
                    print(f"[🔥 CHOLLO VINTED ({score}/10) +{best_net_profit:.0f}€] {title} ({landed_cost:.0f}€ en casa) -> Alertando Discord!")
                    send_discord_alert(
                        conn=conn,
                        platform="VINTED",
                        item_id=item_id,
                        title=title,
                        description=desc or f"Marca: {brand} | Estado: {status_cond}",
                        price=base_price,
                        landed_cost=landed_cost,
                        fees=fees,
                        target=target,
                        ai_data=ai_data,
                        cex_data=cex_data,
                        enhanced_cover_bytes=None,
                        kit_folder=kit_folder,
                        optimal_price=optimal_price,
                        charger_penalty=charger_penalty,
                        net_profit=best_net_profit,
                        item_url=item["url"],
                        checkout_url=item.get("checkout_url", item["url"]),
                        image_url=cover_url,
                        seller_city=f"Vinted ({brand or 'EU'})",
                        is_local=False,
                        country_tag="EU",
                        shipping_label="📦 Punto Pack + Tasa",
                        price_drop_info=price_drop_info,
                        image_urls=photo_urls
                    )

                    timing.item_processing_sleep()

                timing.inter_query_sleep(2.0, 3.8)

            print(f"[RADAR VINTED] {target['name']}: {target_items_count} items ({target_new_count} nuevos)")

        if round_total_fetched == 0:
            print("[VINTED SESSION] Renovando cookies de sesión preventivamente...")
            rotator.rotate()
            vinted._init_session()
        elif rotator.step_round():
            vinted._init_session()

        if warmup:
            print(f"[INFO] Warm-up Vinted completado ({count_platform_seen(conn, 'VINTED')} items). Escaneo activo.")
            warmup = False

        round_num += 1
        duration = round(time.time() - round_start, 1)
        sleep_secs, is_night, transition, reason = timing.calculate_round_sleep(SCAN_INTERVAL, "VINTED")
        if transition:
            health_monitor.notify_transition(transition)

        print(f"[VINTED] Ronda #{round_num} en {duration}s ({round_new} nuevos, {round_evaluated} IA). {reason}. Próximo en {sleep_secs}s.\n")
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()
