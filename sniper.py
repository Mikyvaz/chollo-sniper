# -*- coding: utf-8 -*-
"""
Wallapop Sniper Engine v4.2 (Boti Arbitrage & Homelab Suite).
- Embudo en 3 fases: Pre-Filtro Determinista (0 tokens) -> Visión Multimodal Gemini 2.5 Flash (3 fotos) -> Suelo de Amortización CeX España.
- Escaneo prioritario por Tiers (Hot en cada ronda, Standard alternado).
- Detector de Rebajas de Precio (>=15% y >=10€) + Bloqueo Anti-Repost.
- Alertas interactivas en Discord con Rating Persistente (8/10, 9/10, 10/10 Compra a Ciegas).
"""

import os
import time
import urllib.parse
from curl_cffi import requests

from antiban import BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL, AI_ANALYST_MODEL, PROXY_URL, LOCAL_KEYWORDS,
    init_db, load_config, purge_old_kits,
    check_item_seen_or_dropped, is_duplicate_recent_deal, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, download_item_images, enhance_and_clean_image,
    analyze_and_generate_kit, evaluate_deal_math_and_score,
    build_kit_folder_name, save_resale_kit, send_discord_alert
)

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "38"))

ITALIAN_OR_FOREIGN_HINTS = [
    "azzurra", "spedizione", "perfettamente", "funzionante", "scatola",
    "caricatore", "ottimo stato", "buone condizioni", "senza scatola",
    "giochi", "schermo", "graffi", "vendo per inutilizzo"
]

rotator = BrowserProfileRotator(rotate_every_rounds=10)
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
health_monitor = AntiBanHealthMonitor("WALLAPOP SNIPER")


def get_wallapop_queries_for_target(target):
    if "wallapop_keywords" in target:
        wk = target["wallapop_keywords"]
        return wk if isinstance(wk, list) else [wk]
    return [target.get("keywords", "").strip()]


def is_foreign_listing(item, title, desc):
    loc = item.get("location", {})
    country = str(loc.get("country_code", "") or "").upper()
    city = str(loc.get("city", "") or "")
    if country and country not in ("ES", "ESP"):
        return True, country
    full_text = f"{title} {desc} {city}".lower()
    if any(h in full_text for h in ITALIAN_OR_FOREIGN_HINTS):
        return True, "IT/EU"
    return False, "ES"


def is_item_local(item):
    loc = item.get("location", {})
    city = str(loc.get("city", "") or "").lower()
    postal = str(loc.get("postal_code", "") or "").strip()
    return postal.startswith("41") or any(k in city for k in LOCAL_KEYWORDS)


def estimate_landed_cost(price, is_local, is_foreign):
    if is_local:
        return price, 0.0
    if price <= 25:
        base_fee = 1.95
    elif price <= 50:
        base_fee = 2.95
    elif price <= 100:
        base_fee = 4.95
    elif price <= 200:
        base_fee = 7.95
    else:
        base_fee = 9.95
    shipping = 5.95 if is_foreign else 3.49
    total_fees = round(base_fee + shipping, 2)
    return round(price + total_fees, 2), total_fees


def extract_image_urls(item):
    urls = []
    images = item.get("images", [])
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict):
                urls_dict = img.get("urls", {})
                u = (
                    img.get("original")
                    or img.get("medium")
                    or img.get("small")
                    or urls_dict.get("big")
                    or urls_dict.get("medium")
                )
                if u:
                    urls.append(u)
            elif isinstance(img, str):
                urls.append(img)
    elif isinstance(images, str):
        urls.append(images)
    return urls


def fetch_wallapop(keywords, min_price, max_price, fetch_second_page_if_sparse=True):
    params = {
        "source": "search_box",
        "keywords": keywords,
        "min_sale_price": min_price,
        "max_sale_price": max_price,
        "order_by": "newest",
        "latitude": "37.3891",
        "longitude": "-5.9845"
    }
    url = f"https://api.wallapop.com/api/v3/search?{urllib.parse.urlencode(params)}"
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    profile = rotator.get_chrome_profile()
    headers = {
        "Host": "api.wallapop.com",
        "X-DeviceOS": "0",
        "User-Agent": profile["user_agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Origin": "https://es.wallapop.com",
        "Referer": "https://es.wallapop.com/",
        "sec-ch-ua": profile["sec_ch_ua"],
        "sec-ch-ua-platform": profile["sec_ch_ua_platform"],
        "sec-ch-ua-mobile": profile["sec_ch_ua_mobile"]
    }

    try:
        r = requests.get(url, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
        if r.status_code != 200:
            url_fallback = f"https://api.wallapop.com/api/v3/general/search?{urllib.parse.urlencode(params)}"
            r = requests.get(url_fallback, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)

        if r.status_code in (403, 429):
            should_cd, cd_secs = health_monitor.handle_http_response(r.status_code, url=url, query=keywords)
            if should_cd:
                print(f"[COOLDOWN ANTIBAN] Wallapop HTTP {r.status_code}. Pausando {cd_secs // 60} min...")
                time.sleep(cd_secs)
                rotator.rotate()
            return []
        elif r.status_code == 200:
            health_monitor.handle_http_response(200, query=keywords)
        else:
            print(f"[WARN] Wallapop HTTP {r.status_code} buscando '{keywords}'")
            return []

        data = r.json()
        items = (
            data.get("data", {}).get("section", {}).get("payload", {}).get("items")
            or data.get("search_objects")
            or []
        )

        next_token = data.get("meta", {}).get("next_page")
        if fetch_second_page_if_sparse and len(items) < 8 and next_token:
            try:
                url_p2 = f"https://api.wallapop.com/api/v3/search?next_page={urllib.parse.quote(next_token)}"
                r2 = requests.get(url_p2, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=12)
                if r2.status_code == 200:
                    d2 = r2.json()
                    items_p2 = d2.get("data", {}).get("section", {}).get("payload", {}).get("items") or []
                    items.extend(items_p2)
            except Exception:
                pass

        return items
    except Exception as e:
        print(f"[ERROR] Fallo consultando Wallapop '{keywords}': {e}")
        return []


def send_startup_ping(total_targets, min_profit, min_score):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        return
    engine = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal 3-Fotos)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    msg = {
        "content": (
            f"🛡️ **Boti Arbitrage Sniper v4.2 (WALLAPOP ENGINE • MULTIMODAL + RESPALDO CEX) ONLINE**\n"
            f"🧠 Motor IA: **`{engine}`** | Radares activos: **{total_targets}**\n"
            f"🔒 **Regla de Oro 99%:** Amortización CeX garantizada + Beneficio neto **>={min_profit:.0f}€** + Rating Persistente (**10/10 = Compra a ciegas**)."
        )
    }
    try:
        requests.post(WEBHOOK_URL, json=msg, timeout=10)
    except Exception:
        pass


def main():
    conn = init_db()
    config = load_config()

    targets = [t for t in config.get("targets", []) if t.get("enabled", True) and "wallapop" in t.get("platforms", ["wallapop"])]
    min_profit = float(config.get("min_net_profit_eur", 35))
    min_score = int(config.get("min_deal_score", 8))

    seen_cnt = count_platform_seen(conn, "WALLAPOP")
    warmup = seen_cnt == 0
    item_eval_fails = {}
    round_num = 0

    engine_name = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    print(f"[STARTUP] Wallapop Sniper v4.2 activo | {len(targets)} radares | {seen_cnt} en BD | IA: {engine_name}")
    send_startup_ping(len(targets), min_profit, min_score)

    last_kit_purge = time.time()

    while True:
        if time.time() - last_kit_purge > 21600:
            purge_old_kits(max_days=30)
            last_kit_purge = time.time()

        config = load_config()
        all_targets = config.get("targets", [])
        targets = [t for t in all_targets if t.get("enabled", True) and "wallapop" in t.get("platforms", ["wallapop"])]
        global_blacklist = config.get("global_blacklist", [])
        min_profit = float(config.get("min_net_profit_eur", 35))
        min_personal_savings = float(config.get("min_personal_savings_eur", 25))
        min_score = int(config.get("min_deal_score", 8))
        drop_pct = float(config.get("price_drop_threshold_pct", 15))
        drop_eur = float(config.get("price_drop_min_eur", 10))

        round_start = time.time()
        round_new = 0
        round_evaluated = 0

        for target in targets:
            if not timing.should_scan_target(target, round_num, warmup=warmup):
                continue

            queries = get_wallapop_queries_for_target(target)
            target_items_count = 0
            target_new_count = 0
            mode = target.get("mode", "resale")

            for q in queries:
                items = fetch_wallapop(q, target["min_price"], target["max_price"])
                target_items_count += len(items)

                for item in items:
                    item_id = item.get("id")
                    if not item_id:
                        continue

                    raw_price = item.get("price", 0)
                    price = float(raw_price.get("amount", 0) if isinstance(raw_price, dict) else (raw_price or 0))
                    title = item.get("title", "")
                    desc = item.get("description", "")

                    already_seen, price_drop_info = check_item_seen_or_dropped(
                        conn, "WALLAPOP", item_id, price, min_drop_pct=drop_pct, min_drop_eur=drop_eur
                    )
                    if already_seen:
                        continue

                    target_new_count += 1
                    round_new += 1

                    if item.get("reserved", {}).get("flag", False):
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    if warmup:
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    # FASE 1: Pre-Filtro Determinista (0 tokens, 0 descargas de fotos)
                    if not passes_filters(title, desc, price, target, global_blacklist):
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    if is_duplicate_recent_deal(conn, "WALLAPOP", title, price):
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    is_local = is_item_local(item)
                    shippable = item.get("shipping", {}).get("item_is_shippable", False)
                    if not is_local and not shippable:
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    created_at_ms = item.get("created_at")
                    if created_at_ms and not price_drop_info:
                        try:
                            age_days = (time.time() - (float(created_at_ms) / 1000.0)) / 86400.0
                            if age_days > 4.0:
                                mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                                continue
                        except Exception:
                            pass

                    is_foreign, country_tag = is_foreign_listing(item, title, desc)
                    landed_cost, fees = estimate_landed_cost(price, is_local, is_foreign)

                    # FASE 2 ("El Punto Dulce"): Descargar hasta 3 fotos clave SOLO para candidatos filtrados
                    img_urls = extract_image_urls(item)
                    cover_url = img_urls[0] if img_urls else ""
                    raw_photos = download_item_images(img_urls, rotator=rotator, max_images=3)

                    round_evaluated += 1
                    drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                    print(f"[EVALUANDO WALLAPOP IA + {len(raw_photos)} FOTOS]{drop_tag} {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€)...")
                    ai_data = analyze_and_generate_kit(
                        "WALLAPOP", title, desc, price, landed_cost, is_foreign, target=target,
                        conn=conn, health_monitor=health_monitor, raw_images=raw_photos
                    )

                    if not ai_data:
                        fail_cnt = item_eval_fails.get(item_id, 0) + 1
                        item_eval_fails[item_id] = fail_cnt
                        if fail_cnt >= 2:
                            mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    mark_item_seen(conn, "WALLAPOP", item_id, title, price)

                    # Descarte rápido antes de consultar CeX si la IA ya detectó que no es válido
                    if not ai_data.get("is_valid_deal", False) or ai_data.get("is_pro_seller", False) or ai_data.get("has_defect_or_missing_parts", False):
                        reason = ai_data.get("reject_reason") or "Descartado en inspección visual IA"
                        print(f"[WALLAPOP DESCARTADO IA] {title} ({price:.0f}€) -> {reason}")
                        continue

                    # FASE 3: Verificación CeX España en vivo + Regla de Amortización + Rating Determinista
                    cex_data = None
                    if mode == "resale":
                        cex_query = ai_data.get("cex_search_query") or ai_data.get("identified_model") or q
                        cex_data = fetch_cex_live(
                            conn, cex_query, reference_price=price, target=target,
                            title_context=title, desc_context=desc,
                            includes_box=bool(ai_data.get("includes_original_box", False)),
                            includes_lens=bool(ai_data.get("includes_kit_lens", True))
                        )

                    approved, score, optimal_price, best_net_profit, charger_penalty, reject_msg = evaluate_deal_math_and_score(
                        price=price, landed_cost=landed_cost, target=target, ai_data=ai_data, cex_data=cex_data,
                        min_profit=min_profit, min_personal_savings=min_personal_savings, min_score=min_score,
                        filter_pro_seller=True
                    )
                    if not approved:
                        print(f"[WALLAPOP DESCARTADO - {score}/10] {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€) -> {reject_msg}")
                        continue

                    slug = item.get("web_slug", "")
                    item_url = f"https://es.wallapop.com/item/{slug}" if slug else f"https://es.wallapop.com/item/{item_id}"
                    city = item.get("location", {}).get("city") or "España"
                    kit_folder = build_kit_folder_name("WALLAPOP", item_id, ai_data)

                    enhanced_photos = []
                    try:
                        for p_bytes in raw_photos:
                            try:
                                enhanced_photos.append(enhance_and_clean_image(p_bytes))
                            except Exception:
                                pass
                        save_resale_kit(
                            kit_folder, "WALLAPOP", item_id, title, item_url,
                            ai_data, optimal_price, landed_cost, enhanced_photos
                        )
                    except Exception as e_kit:
                        print(f"[WARN] Error guardando kit Wallapop: {e_kit}")

                    enhanced_cover = enhanced_photos[0] if enhanced_photos else (raw_photos[0] if raw_photos else None)
                    print(f"[🔥 CHOLLO WALLAPOP ({score}/10) +{best_net_profit:.0f}€] {title} ({landed_cost:.0f}€ puesto en casa) -> Alertando Discord!")
                    send_discord_alert(
                        conn=conn,
                        platform="WALLAPOP",
                        item_id=item_id,
                        title=title,
                        description=desc,
                        price=price,
                        landed_cost=landed_cost,
                        fees=fees,
                        target=target,
                        ai_data=ai_data,
                        cex_data=cex_data,
                        enhanced_cover_bytes=enhanced_cover,
                        kit_folder=kit_folder,
                        optimal_price=optimal_price,
                        charger_penalty=charger_penalty,
                        net_profit=best_net_profit,
                        item_url=item_url,
                        checkout_url=item_url,
                        image_url=cover_url,
                        seller_city=city,
                        is_local=is_local,
                        country_tag=country_tag,
                        shipping_label="📦 Wallapop Envíos",
                        price_drop_info=price_drop_info
                    )

                    timing.item_processing_sleep()

                timing.inter_query_sleep()

            print(f"[RADAR WALLAPOP] {target['name']}: {target_items_count} items ({target_new_count} nuevos)")

        if warmup:
            print(f"[INFO] Warm-up Wallapop completado ({count_platform_seen(conn, 'WALLAPOP')} items). Escaneo activo.")
            warmup = False

        round_num += 1
        duration = round(time.time() - round_start, 1)
        rotator.step_round()
        sleep_secs, is_night, transition, reason = timing.calculate_round_sleep(SCAN_INTERVAL, "WALLAPOP")
        if transition:
            health_monitor.notify_transition(transition)

        print(f"[WALLAPOP] Ronda #{round_num} en {duration}s ({round_new} nuevos, {round_evaluated} IA). {reason}. Próximo en {sleep_secs}s.\n")
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()
