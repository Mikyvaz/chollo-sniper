# -*- coding: utf-8 -*-
"""
Wallapop Sniper Engine v4.2 (Boti Arbitrage & Homelab Suite).
- Embudo en 3 fases: Pre-Filtro Determinista (0 tokens) -> Visión Multimodal Gemini 2.5 Flash (3 fotos) -> Suelo de Amortización CeX España.
- Escaneo prioritario por Tiers (Hot en cada ronda, Standard alternado).
- Detector de Rebajas de Precio (>=15% y >=10€) + Bloqueo Anti-Repost.
- Alertas interactivas en Discord con Rating Persistente (8/10, 9/10, 10/10 Compra a Ciegas).
"""

import os
import copy
import json
import time
import threading
import urllib.parse
from curl_cffi import requests

from antiban import BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor, ParserHealthTracker, default_rate_limiter
from logger import setup_logger, log_metric
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL, AI_ANALYST_MODEL, PROXY_URL, LOCAL_KEYWORDS,
    init_db, load_config, purge_old_kits, run_db_maintenance, sync_cex_catalog_mirror,
    check_item_seen_or_dropped, is_duplicate_recent_deal, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, download_item_images,
    analyze_and_generate_kit, evaluate_deal_math_and_score,
    build_kit_folder_name, send_flash_discord_alert, resolve_flash_discord_alert, send_discord_alert,
    send_one_tap_ntfy_alert,
    touch_heartbeat, sleep_with_heartbeat,
    prescreen_item_text, AI_PRESCREEN_SHADOW_MODE,
    evaluate_fraud_risk, log_fraud_event, calculate_dhash, check_and_record_image_hash,
    enrich_seller_profile, evaluate_seller_risk,
    find_semantic_duplicate, save_item_embedding, is_ai_budget_paused,
    CONSOLE_RADAR_IDS, evaluate_bundle_cex_bonus, pre_gate_cex_offer_margin
)

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "8"))

ITALIAN_OR_FOREIGN_HINTS = [
    "azzurra", "spedizione", "perfettamente", "funzionante", "scatola",
    "caricatore", "ottimo stato", "buone condizioni", "senza scatola",
    "giochi", "schermo", "graffi", "vendo per inutilizzo"
]

rotator = BrowserProfileRotator(rotate_every_rounds=10)
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
health_monitor = AntiBanHealthMonitor("WALLAPOP SNIPER")
parser_tracker = ParserHealthTracker("WALLAPOP SNIPER", default_threshold=3)
logger = setup_logger("WALLAPOP-SNIPER")
default_rate_limiter.register_platform("WALLAPOP", SCAN_INTERVAL)


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


class LandedCost(float):
    fees: float = 0.0

    def __new__(cls, landed_cost, fees=0.0):
        obj = super().__new__(cls, round(float(landed_cost), 2))
        obj.fees = round(float(fees), 2)
        return obj

    def __iter__(self):
        yield float(self)
        yield self.fees


def estimate_landed_cost(price, *args, is_local=False, is_foreign=False, is_italy=False, shipping_tier="standard", **kwargs):
    """
    Calcula el coste puesto en casa (landed cost) con Wallapop Envíos y comisión de protección.
    Soporta firmas flexibles:
      - estimate_landed_cost(price, is_local, is_foreign, shipping_tier="standard")
      - estimate_landed_cost(price, is_italy, shipping_tier="standard")
    Devuelve LandedCost(float) que es numéricamente igual al coste total y a la vez iterable (landed_cost, fees).
    """
    if len(args) == 1:
        if isinstance(args[0], str):
            shipping_tier = args[0]
        else:
            is_foreign = bool(args[0])
            is_italy = is_foreign
    elif len(args) == 2:
        if isinstance(args[1], str):
            # Formato: estimate_landed_cost(price, is_italy, "heavy")
            is_foreign = bool(args[0])
            is_italy = is_foreign
            shipping_tier = args[1]
        else:
            # Formato: estimate_landed_cost(price, is_local, is_foreign)
            is_local = bool(args[0])
            is_foreign = bool(args[1])
            is_italy = is_foreign
    elif len(args) >= 3:
        # Formato: estimate_landed_cost(price, is_local, is_foreign, shipping_tier)
        is_local = bool(args[0])
        is_foreign = bool(args[1])
        is_italy = is_foreign
        shipping_tier = str(args[2])

    if is_local:
        return LandedCost(price, 0.0)

    is_foreign_dest = is_foreign or is_italy
    tier = str(shipping_tier or "standard").lower()

    if tier == "heavy":
        shipping = 9.95 if is_foreign_dest else 6.95
    else:
        shipping = 6.95 if is_foreign_dest else 3.49

    insurance = max(1.50, float(price) * 0.05)
    total_fees = round(shipping + insurance, 2)
    total_cost = round(float(price) + total_fees, 2)

    return LandedCost(total_cost, total_fees)


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
    if health_monitor.is_in_cooldown():
        return []

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

    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
        default_rate_limiter.record_request("WALLAPOP", time.time() - t0, r.status_code)
        if r.status_code != 200:
            url_fallback = f"https://api.wallapop.com/api/v3/general/search?{urllib.parse.urlencode(params)}"
            t0_fb = time.time()
            r = requests.get(url_fallback, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
            default_rate_limiter.record_request("WALLAPOP", time.time() - t0_fb, r.status_code)

        if r.status_code in (403, 429):
            should_cd, cd_secs = health_monitor.handle_http_response(r.status_code, url=url, query=keywords)
            if should_cd:
                print(f"[COOLDOWN ANTIBAN] Wallapop HTTP {r.status_code}. Registrado cooldown de {cd_secs // 60} min (estado no bloqueante)...")
                rotator.rotate()
            return []
        elif r.status_code == 200:
            health_monitor.handle_http_response(200, query=keywords)
        else:
            print(f"[WARN] Wallapop HTTP {r.status_code} buscando '{keywords}'")
            parser_tracker.record_empty("WALLAPOP", keywords, health_monitor, reason=f"HTTP {r.status_code}")
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

        if items:
            parser_tracker.record_success("WALLAPOP", keywords, len(items), health_monitor)

        for it in items:
            if isinstance(it, dict):
                raw_p = it.get("price")
                if isinstance(raw_p, dict):
                    it["raw_price_dict"] = raw_p
                    try:
                        it["price"] = float(raw_p.get("amount") or 0.0)
                    except (ValueError, TypeError):
                        it["price"] = 0.0
                elif raw_p is not None:
                    try:
                        it["price"] = float(raw_p)
                    except (ValueError, TypeError):
                        it["price"] = 0.0

            if isinstance(it, dict) and "seller" not in it:
                u_id = str(it.get("user_id") or "")
                u_obj = it.get("user") or {}
                u_name = u_obj.get("micro_name") or u_obj.get("name") or ""
                stats = u_obj.get("stats") or {}
                reviews = stats.get("reviews") or {}
                r_val = u_obj.get("scoring") or stats.get("rating")
                s_cnt = reviews.get("total") if isinstance(reviews, dict) else None
                it["seller"] = {
                    "name": u_name or u_id or "Usuario Wallapop",
                    "id": u_id,
                    "age_days": None,
                    "listings_count": None,
                    "rating": float(r_val) if r_val is not None else None,
                    "sales": int(s_cnt) if s_cnt is not None else None
                }

        return items
    except Exception as e:
        print(f"[ERROR] Fallo consultando Wallapop '{keywords}': {e}")
        parser_tracker.record_parser_error("WALLAPOP", keywords, str(e), health_monitor)
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
    budget_deferred = set()  # Anuncios aplazados por pausa de presupuesto IA (A-001)
    round_num = 0

    engine_name = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    print(f"[STARTUP] Wallapop Sniper v4.2 activo | {len(targets)} radares | {seen_cnt} en BD | IA: {engine_name}")
    send_startup_ping(len(targets), min_profit, min_score)

    last_kit_purge = time.time()
    touch_heartbeat()

    while True:
        touch_heartbeat()
        if time.time() - last_kit_purge > 21600:
            purge_old_kits(max_days=30)
            run_db_maintenance(conn)
            threading.Thread(target=sync_cex_catalog_mirror, daemon=True).start()
            item_eval_fails.clear()
            last_kit_purge = time.time()

        try:
            config = load_config()
        except Exception as e:
            print(f"[WARN] Error transitorio leyendo config.json ({e}). Reteniendo configuración en memoria.")
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
        round_total_items = 0

        # Cooldown no bloqueante (P3-3): Si Wallapop está en cooldown, saltar escaneo manteniendo vivo el contenedor
        if health_monitor.is_in_cooldown():
            rem = health_monitor.get_remaining_cooldown()
            print(f"[WALLAPOP EN COOLDOWN] Restan {rem // 60}m {rem % 60}s. Saltando ronda sin bloquear proceso...")
            sleep_with_heartbeat(min(SCAN_INTERVAL, max(5, rem)))
            round_num += 1
            continue

        for target in targets:
            touch_heartbeat()
            if not timing.should_scan_target(target, round_num, warmup=warmup):
                continue

            queries = get_wallapop_queries_for_target(target)
            target_items_count = 0
            target_new_count = 0
            mode = target.get("mode", "resale")

            raw_items = []
            seen_target_ids = set()
            for q in queries:
                items = fetch_wallapop(q, target["min_price"], target["max_price"])
                target_items_count += len(items)
                round_total_items += len(items)
                for itm in items:
                    iid = itm.get("id")
                    if iid and iid not in seen_target_ids:
                        seen_target_ids.add(iid)
                        raw_items.append(itm)
                timing.inter_query_sleep()

            tgt_max = max(1.0, float(target.get("max_price", 9999)))
            direct_max = float(target.get("direct_buy_max_price") or tgt_max)

            def _get_price(it):
                p = it.get("price", 0)
                if isinstance(p, dict):
                    return float(p.get("amount", 0) or 0)
                try:
                    return float(p or 0)
                except (ValueError, TypeError):
                    return 0.0

            fast_lane_key = lambda it: (0 if _get_price(it) <= direct_max else 1, _get_price(it) / tgt_max)
            raw_items.sort(key=fast_lane_key)

            for item in raw_items:
                    item_id = item.get("id")
                    if not item_id:
                        continue

                    raw_price = item.get("price", 0)
                    price = float(raw_price.get("amount", 0) if isinstance(raw_price, dict) else (raw_price or 0))
                    item["price"] = price
                    title = item.get("title", "")
                    desc = item.get("description", "")

                    already_seen, price_drop_info = check_item_seen_or_dropped(
                        conn, "WALLAPOP", item_id, price, min_drop_pct=drop_pct, min_drop_eur=drop_eur
                    )
                    if already_seen:
                        continue

                    # Aplazado por presupuesto: esperar a que se reanude la IA sin repetir la alerta flash
                    was_budget_deferred = item_id in budget_deferred
                    if was_budget_deferred:
                        if is_ai_budget_paused(conn):
                            continue
                        budget_deferred.discard(item_id)

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

                    created_at_ms = item.get("created_at") or item.get("creation_date") or item.get("modified_at")
                    age_days = None
                    if created_at_ms:
                        try:
                            ts_val = float(created_at_ms)
                            if ts_val > 1e11:
                                ts_val = ts_val / 1000.0
                            age_days = max(0.0, (time.time() - ts_val) / 86400.0)
                            if not price_drop_info and not was_budget_deferred and age_days > 1.0:
                                mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                                continue
                        except Exception:
                            pass

                    is_foreign, country_tag = is_foreign_listing(item, title, desc)
                    shipping_tier = target.get("shipping_tier", "standard")
                    landed_cost, fees = estimate_landed_cost(price, is_local, is_foreign, shipping_tier=shipping_tier)
                    slug = item.get("web_slug", "")
                    item_url = f"https://es.wallapop.com/item/{slug}" if slug else f"https://es.wallapop.com/item/{item_id}"
                    city = item.get("location", {}).get("city") or "España"
                    img_urls = extract_image_urls(item)
                    cover_url = img_urls[0] if img_urls else ""
                    flash_msg = None

                    # EVALUACIÓN DE FRAUDE DETERMINISTA EN CAPAS (P2)
                    item["platform"] = "WALLAPOP"
                    enrich_seller_profile(item, http_session=None)
                    seller = item.get("seller")
                    current_seller_id = ""
                    if isinstance(seller, dict):
                        current_seller_id = str(seller.get("id") or seller.get("user_id") or "").strip()
                    elif seller:
                        current_seller_id = str(seller).strip()

                    fraud_eval = evaluate_fraud_risk(
                        title, desc, price, radar_id=target.get("id", ""),
                        conn=conn, platform="WALLAPOP", item_id=item_id,
                        seller=seller
                    )

                    if fraud_eval.get("is_fraud") or fraud_eval.get("should_block") or fraud_eval.get("risk", 0) >= 4:
                        log_fraud_event(conn, "WALLAPOP", item_id, title, price, fraud_eval["risk"], fraud_eval["hits"])
                        print(f"[WALLAPOP FRAUDE BLOQUEADO] {title} ({price:.0f}€) -> {[h['label'] for h in fraud_eval['hits']]}")
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    # --- FASE 1.5: ALERTA FLASH (<80ms, SIN IA) ---
                    # Disparo inmediato pre-IA únicamente si el vendedor es fiable (reviews >= 2 y rating >= 4.0) y reciente (<24h)
                    seller_reviews = int(item.get("seller_reviews_count", 0) or 0)
                    seller_rating = item.get("seller_rating")
                    can_flash = (seller_reviews >= 2 and (seller_rating is not None and seller_rating >= 4.0))

                    direct_max = float(target.get("direct_buy_max_price") or target.get("max_price", 9999))
                    if fraud_eval["risk"] < 2 and can_flash and not was_budget_deferred and price <= direct_max and (age_days is None or age_days <= 1.0):
                        print(f"[⚡ FLASH WALLAPOP <80ms] {title} ({price:.0f}€) -> Pre-validado por Puerta 0 y Antifraude (Vendedor: {seller_reviews} revs / {seller_rating}⭐). Enviando alerta instantánea...")
                        flash_msg = send_flash_discord_alert(
                            platform="WALLAPOP",
                            item_id=item_id,
                            title=title,
                            price=price,
                            landed_cost=landed_cost,
                            item_url=item_url,
                            image_url=cover_url,
                            seller_city=city,
                            is_local=is_local,
                            target=target
                        )
                        item["url"] = item_url
                        send_one_tap_ntfy_alert(item, {"cex_cash": 0, "p2p_net_profit": 0}, is_flash=True)

                    # Pre-gating matemático CeX antes de IA (TASK-03)
                    ok_pregate, pregate_reason = pre_gate_cex_offer_margin(conn, item, target, config)
                    if not ok_pregate:
                        print(f"[WALLAPOP PRE-GATE CEX DESCARTADO] {title} ({price:.0f}€) -> {pregate_reason}")
                        if flash_msg:
                            resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Pre-gate CeX: {pregate_reason}", title=title, price=price)
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    # PUERTA 1: Criba ultra-ligera por texto (0 imágenes, 0 thinking tokens)
                    ps_decision, ps_reason, ps_conf, from_cache = prescreen_item_text(
                        "WALLAPOP", title, desc, price, landed_cost, target, conn=conn, health_monitor=health_monitor
                    )

                    if ps_decision == "reject":
                        if AI_PRESCREEN_SHADOW_MODE:
                            print(f"[WALLAPOP SHADOW PRESCREEN RECHAZARÍA] {title} ({price:.0f}€) -> {ps_reason} | Pasando a visión por shadow mode...")
                        else:
                            print(f"[WALLAPOP DESCARTADO TEXTO PUERTA 1] {title} ({price:.0f}€) -> {ps_reason}")
                            if flash_msg:
                                resolve_flash_discord_alert(
                                    flash_msg, approved=False, reason=f"Descartado texto Puerta 1: {ps_reason}",
                                    title=title, price=price, item_url=item_url
                                )
                            mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                            continue

                    # Si hay riesgo moderado (2-3) y Puerta 1 no está segura ('unsure'), descartar y registrar evento
                    if fraud_eval["risk"] in (2, 3) and ps_decision == "unsure":
                        log_fraud_event(conn, "WALLAPOP", item_id, title, price, fraud_eval["risk"], fraud_eval["hits"])
                        print(f"[WALLAPOP RIESGO MODERADO DESCARTADO] {title} ({price:.0f}€) -> {fraud_eval['warning_banner']}")
                        if flash_msg:
                            resolve_flash_discord_alert(
                                flash_msg, approved=False, reason=f"Riesgo moderado antifraude: {fraud_eval.get('warning_banner', '')[:60]}",
                                title=title, price=price, item_url=item_url
                            )
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    # FASE 5: Comprobación de duplicado semántico (P1-6)
                    text_for_embed = f"{title}\n{desc}".strip()
                    sem_dup, item_vec = find_semantic_duplicate(
                        conn, text_for_embed, radar_id=target.get("id", ""), threshold=0.92, hours=720, price=price,
                        exclude_item_key=f"WALLAPOP:{item_id}"
                    )

                    ai_data = None
                    if sem_dup and sem_dup.get("verdict_json"):
                        try:
                            cached_verdict = json.loads(sem_dup["verdict_json"])
                            if isinstance(cached_verdict, dict):
                                sim_score = float(sem_dup.get("similarity", sem_dup.get("score", 0.0)))
                                cached_seller_id = str(sem_dup.get("seller_id", "")).strip()
                                cached_price = float(sem_dup.get("price", 999999.0))
                                is_cached_reject = (not cached_verdict.get("is_valid_deal", False)) or cached_verdict.get("has_defect_or_missing_parts", False)

                                if is_cached_reject:
                                    if sim_score >= 0.96:
                                        rej = cached_verdict.get("reject_reason") or "Descartado previamente por IA"
                                        print(f"[WALLAPOP DEDUP SEMÁNTICA RECHAZO] '{title}' duplicado de '{sem_dup['title']}' (sim={sim_score:.2f} >= 0.96) -> {rej}")
                                        if flash_msg:
                                            resolve_flash_discord_alert(
                                                flash_msg, approved=False, reason=f"Duplicado semántico rechazado: {rej}",
                                                title=title, price=price, item_url=item_url
                                            )
                                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                                        continue
                                    else:
                                        print(f"[WALLAPOP DEDUP SEMÁNTICA] Match rechazado previo pero sim={sim_score:.2f} < 0.96 -> Inspección visual obligatoria")
                                        ai_data = None
                                else:
                                    if current_seller_id and current_seller_id == cached_seller_id and price <= cached_price:
                                        print(f"[WALLAPOP DEDUP SEMÁNTICA REUTILIZADO] '{title}' duplicado aprobado de '{sem_dup['title']}' (mismo vendedor {current_seller_id}, {price:.0f}€ <= {cached_price:.0f}€, sim={sim_score:.2f}) -> Reutilizando kit sin visión")
                                        ai_data = copy.deepcopy(cached_verdict)
                                    else:
                                        print(f"[WALLAPOP DEDUP SEMÁNTICA] Match aprobado pero vendedor ({current_seller_id} vs {cached_seller_id}) o precio ({price:.0f}€ vs {cached_price:.0f}€) no apto para bypass -> Forzando visión")
                                        ai_data = None
                        except Exception as e:
                            print(f"[WARN] Error parseando veredicto de dedup: {e}")

                    if ai_data is None:
                        # PUERTA 2: Visión adaptativa (1 foto primero, hasta 3 si la confianza visual es baja)
                        raw_photos = download_item_images(img_urls, rotator=rotator, max_images=1)

                        # Comprobación de dHash para fotos reutilizadas (P2-5)
                        if raw_photos and len(raw_photos) > 0 and raw_photos[0]:
                            img_h = calculate_dhash(raw_photos[0])
                            if img_h:
                                is_reused, r_info = check_and_record_image_hash(conn, img_h, "WALLAPOP", item_id, target.get("id", ""))
                                if is_reused and r_info:
                                    re_banner = f"⚠️ **FOTO REUTILIZADA:** Ya vista en {r_info['platform']} (anuncio {r_info['item_id']})"
                                    existing_w = fraud_eval.get("warning_banner", "")
                                    fraud_eval["warning_banner"] = f"{re_banner}\n{existing_w}".strip()
                                    fraud_eval["risk"] = min(5, fraud_eval.get("risk", 0) + 3)
                                    fraud_eval.setdefault("hits", []).append({
                                        "code": "REUSED_IMAGE",
                                        "category": "image",
                                        "label": f"Foto reutilizada de {r_info['platform']} ({r_info['item_id']})",
                                        "weight": 3
                                    })
                                    if fraud_eval["risk"] >= 4:
                                        fraud_eval["is_fraud"] = True
                                        log_fraud_event(conn, "WALLAPOP", item_id, title, price, fraud_eval["risk"], fraud_eval["hits"])
                                        print(f"[WALLAPOP FRAUDE POR FOTO REUTILIZADA] Bloqueado anuncio {item_id} (riesgo={fraud_eval['risk']})")
                                        if flash_msg:
                                            resolve_flash_discord_alert(
                                                flash_msg, approved=False, reason=f"Foto reutilizada de {r_info['platform']} ({r_info['item_id']})",
                                                title=title, price=price, item_url=item_url
                                            )
                                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                                        continue

                        round_evaluated += 1
                        drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                        print(f"[EVALUANDO WALLAPOP IA + {len(raw_photos)} FOTOS]{drop_tag} {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€)...")
                        ai_data = analyze_and_generate_kit(
                            "WALLAPOP", title, desc, price, landed_cost, is_foreign, target=target,
                            conn=conn, health_monitor=health_monitor, raw_images=raw_photos, call_type="vision"
                        )

                        # Reintento con 3 fotos si la IA aprueba pero duda visualmente y hay más fotos disponibles
                        if ai_data and ai_data.get("is_valid_deal", False) and ai_data.get("visual_confidence") == "baja" and len(img_urls) > 1:
                            print(f"[WALLAPOP REINTENTO VISUAL] Confianza baja con 1 foto para '{title}'. Descargando hasta 3 fotos...")
                            more_photos = download_item_images(img_urls, rotator=rotator, max_images=3)
                            if len(more_photos) > 1:
                                ai_data_retry = analyze_and_generate_kit(
                                    "WALLAPOP", title, desc, price, landed_cost, is_foreign, target=target,
                                    conn=conn, health_monitor=health_monitor, raw_images=more_photos, call_type="vision_retry"
                                )
                                if ai_data_retry:
                                    ai_data = ai_data_retry

                        if ai_data and not ai_data.get("_api_error"):
                            save_item_embedding(
                                conn, f"WALLAPOP:{item_id}", target.get("id", ""),
                                vector_or_text=item_vec or text_for_embed,
                                model="models/gemini-embedding-2",
                                verdict_json=json.dumps(ai_data, ensure_ascii=False),
                                title=title, price=price,
                                seller_id=current_seller_id,
                                precomputed_vec=item_vec
                            )

                    if ai_data and ai_data.get("_budget_paused"):
                        budget_deferred.add(item_id)
                        print(f"[WALLAPOP BUDGET] '{title}' ({price:.0f}€) aplazado hasta que se reanude el presupuesto IA.")
                        continue

                    if not ai_data or ai_data.get("_api_error"):
                        print(f"[WALLAPOP WARN] Fallo temporal IA en {title} ({price:.0f}€). Fail-open.")
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
                        if flash_msg:
                            resolve_flash_discord_alert(
                                flash_msg, approved=False, reason=reason,
                                title=title, price=price, item_url=item_url
                            )
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

                        # BUNDLE SUM ENGINE (Task 4.2): Valoración acumulativa de juegos/mandos extra en CeX
                        bundled_q = ai_data.get("bundled_games_queries", [])
                        radar_id = (target or {}).get("id", "")
                        is_console = (radar_id in CONSOLE_RADAR_IDS) or any(c_id in radar_id for c_id in CONSOLE_RADAR_IDS)
                        if is_console and bundled_q and cex_data and isinstance(cex_data, dict):
                            bundle_bonus = evaluate_bundle_cex_bonus(conn, bundled_q)
                            extra_cash = bundle_bonus.get("extra_cash", 0.0)
                            extra_voucher = bundle_bonus.get("extra_voucher", 0.0)
                            matched_extras = bundle_bonus.get("matched_extras", [])
                            if matched_extras:
                                cex_data["cash"] = round(float(cex_data.get("cash", 0.0) or 0.0) + extra_cash, 2)
                                cex_data["voucher"] = round(float(cex_data.get("voucher", 0.0) or 0.0) + extra_voucher, 2)
                                cex_data["box_name"] = f"{cex_data.get('box_name', cex_query)} + {len(matched_extras)} extra(s)"
                                ai_data["bundled_matches"] = matched_extras
                                print(f"[BUNDLE SUM ENGINE] Añadido bonus de {len(matched_extras)} extra(s) (+{extra_cash:.1f}€ cash / +{extra_voucher:.1f}€ vale) a '{cex_query}'")

                    ai_data["fraud_risk_score"] = int(fraud_eval.get("risk", 0) or 0)
                    approved, score, optimal_price, best_net_profit, charger_penalty, reject_msg = evaluate_deal_math_and_score(
                        price=price, landed_cost=landed_cost, target=target, ai_data=ai_data, cex_data=cex_data,
                        min_profit=min_profit, min_personal_savings=min_personal_savings, min_score=min_score,
                        filter_pro_seller=True
                    )
                    if not approved:
                        print(f"[WALLAPOP DESCARTADO - {score}/10] {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€) -> {reject_msg}")
                        if flash_msg:
                            resolve_flash_discord_alert(
                                flash_msg, approved=False, reason=reject_msg,
                                title=title, price=price, item_url=item_url
                            )
                        continue

                    kit_folder = build_kit_folder_name("WALLAPOP", item_id, ai_data)
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
                        enhanced_cover_bytes=None,
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
                        price_drop_info=price_drop_info,
                        image_urls=img_urls,
                        flash_message=flash_msg,
                        fraud_banner=fraud_eval.get("warning_banner", ""),
                        seller=item.get("seller"),
                        item_vec=item_vec
                    )
                    item["url"] = item_url
                    send_one_tap_ntfy_alert(
                        item,
                        {
                            "cex_cash": float((cex_data or {}).get("cash", 0.0) or 0.0),
                            "p2p_net_profit": float(best_net_profit or 0.0),
                            "deal_tier": ai_data.get("deal_tier", 1)
                        },
                        is_flash=False
                    )

                    timing.item_processing_sleep()

            print(f"[RADAR WALLAPOP] {target['name']}: {target_items_count} items ({target_new_count} nuevos)")

        if round_total_items > 0:
            parser_tracker.record_platform_success("WALLAPOP", round_total_items, health_monitor)
        elif not warmup and targets:
            parser_tracker.record_platform_empty("WALLAPOP", health_monitor, threshold=6, reason="0 anuncios en toda la ronda de Wallapop")

        if warmup:
            print(f"[INFO] Warm-up Wallapop completado ({count_platform_seen(conn, 'WALLAPOP')} items). Escaneo activo.")
            warmup = False

        round_num += 1
        duration = round(time.time() - round_start, 1)
        rotator.step_round()
        sleep_secs, is_night, transition, reason = timing.calculate_round_sleep(SCAN_INTERVAL, "WALLAPOP")
        if transition:
            health_monitor.notify_transition(transition)

        log_metric(
            event="round_completed",
            service="wallapop",
            platform="WALLAPOP",
            round=round_num,
            duration_s=duration,
            items_new=round_new,
            items_evaluated=round_evaluated,
            interval_s=sleep_secs,
            reason=reason
        )

        print(f"[WALLAPOP] Ronda #{round_num} en {duration}s ({round_new} nuevos, {round_evaluated} IA). {reason}. Próximo en {sleep_secs}s.\n")
        sleep_with_heartbeat(sleep_secs)


if __name__ == "__main__":
    main()
