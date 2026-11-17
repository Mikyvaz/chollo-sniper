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

from antiban import (
    BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor, ParserHealthTracker,
    default_rate_limiter, TokenBucket, QueryScheduler, set_platform_bucket, get_platform_bucket
)
from http_client import wallapop_client
from logger import setup_logger, log_metric, compute_pipeline_ms
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL, AI_ANALYST_MODEL, PROXY_URL, LOCAL_KEYWORDS, CONFIG_PATH,
    init_db, load_config, purge_old_kits, run_db_maintenance, sync_cex_catalog_mirror,
    check_item_seen_or_dropped, is_duplicate_recent_deal, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, download_item_images,
    analyze_and_generate_kit, evaluate_deal_math_and_score,
    build_kit_folder_name, send_flash_discord_alert, resolve_flash_discord_alert, send_discord_alert,
    send_one_tap_ntfy_alert, build_ntfy_analysis_payload,
    touch_heartbeat, sleep_with_heartbeat,
    prescreen_item_text, AI_PRESCREEN_SHADOW_MODE,
    evaluate_fraud_risk, log_fraud_event, calculate_dhash, check_and_record_image_hash,
    record_fraud_sample,
    enrich_seller_profile, evaluate_seller_risk,
    find_semantic_duplicate, save_item_embedding, evaluate_semantic_duplicate, make_image_hash_prefetcher, is_ai_budget_paused,
    CONSOLE_RADAR_IDS, evaluate_bundle_cex_bonus, pre_gate_cex_offer_margin, estimate_cex_from_title, DB_PATH
)

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "8"))
SCHEDULER_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "scheduler_state.wallapop.json")

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


def _created_ms(item):
    try:
        v = float((item or {}).get("created_at") or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def count_newer_second_page_items(page1, page2):
    """Anuncios de la 2.ª página que podrían ser nuevos: no están en la 1.ª y no son más antiguos que
    el más antiguo de la 1.ª. Con `order_by=newest` la 2.ª página solo continúa la lista hacia atrás
    (comprobado el 17/11/2026 en 4 consultas), así que normalmente devuelve 0. Sin fecha o con la 1.ª
    vacía se cuentan, por prudencia."""
    p1 = [it for it in (page1 or []) if isinstance(it, dict)]
    p1_ids = {it.get("id") for it in p1}
    p1_ts = [t for t in (_created_ms(it) for it in p1) if t is not None]
    oldest_p1 = min(p1_ts) if p1_ts else None
    count = 0
    for it in page2 or []:
        if not isinstance(it, dict) or it.get("id") in p1_ids:
            continue
        ts = _created_ms(it)
        if oldest_p1 is None or ts is None or ts > oldest_p1:
            count += 1
    return count


class SecondPagePolicy:
    """Decide si merece pedir la 2.ª página de una búsqueda que devolvió pocos resultados.

    Con `order_by=newest` los anuncios nuevos entran por la 1.ª página; la 2.ª solo trae anuncios más
    antiguos, ya vistos, y cada petición gasta un token del cubo (retrasa el siguiente sondeo). Tras
    `empty_streak` segundas páginas sin anuncios más recientes que la 1.ª se deja de pedir durante
    `skip_polls` sondeos y luego se vuelve a comprobar (así también se ven, con retraso, las rebajas
    de anuncios antiguos que quedan en la 2.ª página).
    """

    def __init__(self, empty_streak=2, skip_polls=40):
        self.empty_streak = max(1, int(empty_streak))
        self.skip_polls = max(0, int(skip_polls))
        self._lock = threading.Lock()
        self._state = {}

    def should_fetch(self, query) -> bool:
        with self._lock:
            st = self._state.get(str(query))
            if not st or st["skip"] <= 0:
                return True
            st["skip"] -= 1
            return False

    def record(self, query, added: int):
        with self._lock:
            st = self._state.setdefault(str(query), {"empty": 0, "skip": 0})
            if int(added or 0) > 0:
                st["empty"] = 0
                st["skip"] = 0
            else:
                st["empty"] += 1
                if st["empty"] >= self.empty_streak:
                    st["skip"] = self.skip_polls


second_page_policy = SecondPagePolicy()


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


def listing_age_seconds(item, now=None):
    """Segundos desde la publicación (`created_at`) del anuncio, o None si no trae fecha válida."""
    raw = (item or {}).get("created_at") or (item or {}).get("creation_date")
    if not raw:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if ts > 1e11:
        ts /= 1000.0
    age = (time.time() if now is None else float(now)) - ts
    return age if age >= 0 else None


def summarize_ages(ages):
    """(n, mínimo, mediana) en segundos de una lista de edades; (0, None, None) si está vacía."""
    vals = sorted(float(a) for a in ages or [])
    if not vals:
        return 0, None, None
    return len(vals), round(vals[0], 1), round(vals[len(vals) // 2], 1)


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


_ORIG_REQUESTS_GET = requests.get


def _wallapop_http_get(url, headers, proxies, impersonate, timeout):
    if requests.get is not _ORIG_REQUESTS_GET:
        return requests.get(url, headers=headers, proxies=proxies, impersonate=impersonate, timeout=timeout)
    return wallapop_client.get(
        url,
        kind="xhr",
        headers={"Host": "api.wallapop.com"},
        proxies=proxies,
        timeout=timeout,
        record_metrics=False,
    )


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
        r = _wallapop_http_get(url, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
        default_rate_limiter.record_request("WALLAPOP", time.time() - t0, r.status_code)
        if r.status_code in (400, 404, 410):
            url_fallback = f"https://api.wallapop.com/api/v3/general/search?{urllib.parse.urlencode(params)}"
            t0_fb = time.time()
            r = _wallapop_http_get(url_fallback, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
            default_rate_limiter.record_request("WALLAPOP", time.time() - t0_fb, r.status_code)

        if r.status_code in (401, 403, 429):
            bkt = get_platform_bucket("WALLAPOP")
            if bkt is not None:
                bkt.penalize()
            retry_after = (getattr(r, "headers", None) or {}).get("Retry-After")
            should_cd, cd_secs = health_monitor.handle_http_response(
                r.status_code, url=url, query=keywords, retry_after=retry_after)
            if should_cd:
                print(f"[COOLDOWN ANTIBAN] Wallapop HTTP {r.status_code}. Registrado cooldown de {cd_secs // 60} min (estado no bloqueante)...")
                rotator.rotate()
                wallapop_client.rotate()
            return []
        elif r.status_code == 200:
            health_monitor.handle_http_response(200, query=keywords)
            bkt = get_platform_bucket("WALLAPOP")
            if bkt is not None:
                bkt.recover()
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
        if (fetch_second_page_if_sparse and len(items) < 8 and next_token
                and second_page_policy.should_fetch(keywords)):
            try:
                bkt = get_platform_bucket("WALLAPOP")
                if bkt is not None:
                    bkt.acquire()
                url_p2 = f"https://api.wallapop.com/api/v3/search?next_page={urllib.parse.quote(next_token)}"
                r2 = _wallapop_http_get(url_p2, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=12)
                if r2.status_code == 200:
                    d2 = r2.json()
                    items_p2 = d2.get("data", {}).get("section", {}).get("payload", {}).get("items") or []
                    second_page_policy.record(keywords, count_newer_second_page_items(items, items_p2))
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


def handle_seen_and_reserved(conn, item_id, title, price, is_reserved_now, drop_pct=15, drop_eur=10):
    """Gestiona visto/rebaja y estado reservado en Wallapop; devuelve (should_skip, price_drop_info)."""
    already_seen, price_drop_info = check_item_seen_or_dropped(
        conn, "WALLAPOP", item_id, price,
        min_drop_pct=drop_pct, min_drop_eur=drop_eur,
        is_reserved_now=bool(is_reserved_now),
    )
    if already_seen:
        return True, None
    if is_reserved_now:
        mark_item_seen(conn, "WALLAPOP", item_id, title, price, state="reserved")
        return True, None
    return False, price_drop_info


def _is_item_reserved_in_db(conn, item_id, platform="WALLAPOP"):
    if conn is None or not item_id:
        return False
    try:
        row = conn.execute(
            "SELECT state FROM market_seen_items WHERE platform = ? AND item_id = ?",
            (platform.upper(), str(item_id)),
        ).fetchone()
        return bool(row and str(row[0] or "").strip().lower() == "reserved")
    except Exception:
        return False


def should_skip_stale_listing(conn, item_id, age_days, price_drop_info=None, was_budget_deferred=False):
    """No descarta por antigüedad (>1 día) si hay rebaja, aplazamiento por presupuesto o caída de reserva."""
    if age_days is None or age_days <= 1.0:
        return False
    if price_drop_info or was_budget_deferred:
        return False
    return not _is_item_reserved_in_db(conn, item_id, platform="WALLAPOP")


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

    scheduler = QueryScheduler.from_config(
        config, platform="wallapop", config_path=CONFIG_PATH, heartbeat=touch_heartbeat,
        state_path=SCHEDULER_STATE_PATH
    )
    bucket = scheduler.bucket
    set_platform_bucket("WALLAPOP", bucket)
    print(f"[PLANIFICADOR WALLAPOP] {scheduler.describe()}")

    global_blacklist = config.get("global_blacklist", [])
    min_personal_savings = float(config.get("min_personal_savings_eur", 25))
    drop_pct = float(config.get("price_drop_threshold_pct", 15))
    drop_eur = float(config.get("price_drop_min_eur", 10))

    last_kit_purge = time.time()
    round_start = time.time()
    round_new = 0
    round_evaluated = 0
    round_total_items = 0
    queries_in_round = 0
    last_is_night = None
    touch_heartbeat()

    while True:
        touch_heartbeat()
        if time.time() - last_kit_purge > 21600:
            purge_old_kits(max_days=30)
            run_db_maintenance(conn)
            threading.Thread(target=sync_cex_catalog_mirror, daemon=True).start()
            item_eval_fails.clear()
            last_kit_purge = time.time()

        if scheduler.reload_if_changed():
            config = scheduler.config
            all_targets = config.get("targets", [])
            targets = [t for t in all_targets if t.get("enabled", True) and "wallapop" in t.get("platforms", ["wallapop"])]
            global_blacklist = config.get("global_blacklist", [])
            min_profit = float(config.get("min_net_profit_eur", 35))
            min_personal_savings = float(config.get("min_personal_savings_eur", 25))
            min_score = int(config.get("min_deal_score", 8))
            drop_pct = float(config.get("price_drop_threshold_pct", 15))
            drop_eur = float(config.get("price_drop_min_eur", 10))

        # Cooldown no bloqueante (P3-3): Si Wallapop está en cooldown, saltar escaneo manteniendo vivo el contenedor
        if health_monitor.is_in_cooldown():
            rem = health_monitor.get_remaining_cooldown()
            print(f"[WALLAPOP EN COOLDOWN] Restan {rem // 60}m {rem % 60}s. Saltando consulta sin bloquear proceso...")
            sleep_with_heartbeat(min(SCAN_INTERVAL, max(5, rem)))
            continue

        is_night_now = scheduler.is_night_time()
        if last_is_night is not None and last_is_night != is_night_now:
            health_monitor.notify_transition("ENTER_NIGHT" if is_night_now else "ENTER_DAY")
        last_is_night = is_night_now

        task = scheduler.next()
        target = task.target or {}
        q = task.query
        mode = target.get("mode", "resale")
        q_start = time.time()
        target_items_count = 0
        target_new_count = 0
        target_candidates = 0   # nuevos que superan los filtros locales: alimenta el reparto adaptativo
        target_alerts = 0       # alertas enviadas en este sondeo (pesan más que un candidato)
        fresh_ages = []         # edad (s) de los anuncios nuevos al verlos por primera vez
        eval_before = round_evaluated

        for _ in (0,):
            raw_items = []
            seen_target_ids = set()
            items = fetch_wallapop(q, task.min_price, task.max_price)
            _t_fetch = time.perf_counter()
            target_items_count += len(items)
            round_total_items += len(items)
            for itm in items:
                iid = itm.get("id")
                if iid and iid not in seen_target_ids:
                    if isinstance(itm, dict):
                        itm.setdefault("_t_fetch", _t_fetch)
                    seen_target_ids.add(iid)
                    raw_items.append(itm)

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

                    t = {"fetch": float(item.get("_t_fetch") or time.perf_counter())}
                    radar_id = target.get("id", "")

                    def _emit_pipeline(outcome):
                        ms = compute_pipeline_ms(t)
                        log_metric("item_pipeline", service="wallapop", platform="WALLAPOP", radar_id=radar_id, outcome=outcome, **ms)
                        return ms

                    raw_price = item.get("price", 0)
                    price = float(raw_price.get("amount", 0) if isinstance(raw_price, dict) else (raw_price or 0))
                    item["price"] = price
                    title = item.get("title", "")
                    desc = item.get("description", "")

                    is_reserved_now = bool((item.get("reserved") or {}).get("flag", False))
                    should_skip_item, price_drop_info = handle_seen_and_reserved(
                        conn, item_id, title, price, is_reserved_now,
                        drop_pct=drop_pct, drop_eur=drop_eur,
                    )
                    if should_skip_item:
                        continue

                    # Aplazado por presupuesto: esperar a que se reanude la IA sin repetir la alerta flash
                    was_budget_deferred = item_id in budget_deferred
                    if was_budget_deferred:
                        if is_ai_budget_paused(conn):
                            continue
                        budget_deferred.discard(item_id)

                    target_new_count += 1
                    round_new += 1

                    if warmup:
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        continue

                    # Retraso de detección: cuánto llevaba publicado al verlo por primera vez.
                    # Mide el índice de Wallapop + nuestra cadencia con miles de muestras, no solo alertas.
                    if price_drop_info is None:
                        _age_s = listing_age_seconds(item)
                        if _age_s is not None and _age_s <= 3600.0:
                            fresh_ages.append(_age_s)

                    # FASE 1: Pre-Filtro Determinista (0 tokens, 0 descargas de fotos)
                    t["filters"] = time.perf_counter()
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
                    listing_ts = None  # hora de publicación (para medir el retraso real hasta la alerta)
                    if created_at_ms:
                        try:
                            ts_val = float(created_at_ms)
                            if ts_val > 1e11:
                                ts_val = ts_val / 1000.0
                            listing_ts = ts_val
                            age_days = max(0.0, (time.time() - ts_val) / 86400.0)
                            if should_skip_stale_listing(
                                conn, item_id, age_days,
                                price_drop_info=price_drop_info,
                                was_budget_deferred=was_budget_deferred,
                            ):
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
                    target_candidates += 1
                    t["enrich"] = time.perf_counter()
                    enrich_seller_profile(item, http_session=None, conn=conn, health_monitor=health_monitor)
                    item["landed_cost"] = landed_cost
                    item["_pre_cex"] = estimate_cex_from_title(conn, title, desc, target)
                    seller = item.get("seller")
                    current_seller_id = ""
                    if isinstance(seller, dict):
                        current_seller_id = str(seller.get("id") or seller.get("user_id") or "").strip()
                    elif seller:
                        current_seller_id = str(seller).strip()

                    fraud_eval = evaluate_fraud_risk(
                        title, desc, price, radar_id=target.get("id", ""),
                        conn=conn, platform="WALLAPOP", item_id=item_id,
                        seller=seller, cex_cash=item["_pre_cex"].get("cash", 0)
                    )

                    if fraud_eval.get("is_fraud") or fraud_eval.get("should_block") or fraud_eval.get("risk", 0) >= 4:
                        log_fraud_event(conn, "WALLAPOP", item_id, title, price, fraud_eval["risk"], fraud_eval["hits"])
                        record_fraud_sample(
                            conn, "WALLAPOP", item_id, seller_id=current_seller_id,
                            risk=fraud_eval.get("risk", 0), codes=fraud_eval.get("hits"),
                            item_url=item_url, seller=seller,
                        )
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

                    # La foto de portada se descarga ya, en paralelo con la Puerta 1 (ahorra su latencia
                    # antes de la visión); si la Puerta 1 rechaza, solo se pierde una descarga de CDN.
                    _img_hash_provider, _img_prefetch = make_image_hash_prefetcher(
                        img_urls, rotator=rotator, timings=t, eager=True
                    )

                    # PUERTA 1: Criba ultra-ligera por texto (0 imágenes, 0 thinking tokens)
                    t["p1"] = time.perf_counter()
                    ps_decision, ps_reason, ps_conf, from_cache = prescreen_item_text(
                        "WALLAPOP", title, desc, price, landed_cost, target, conn=conn, health_monitor=health_monitor
                    )
                    t["p1_end"] = time.perf_counter()

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
                            _emit_pipeline("p1_reject")
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
                        _emit_pipeline("p1_unsure_fraud")
                        continue

                    # FASE 5: Comprobación de duplicado semántico (P1-6 / T3.1)
                    text_for_embed = f"{title}\n{desc}".strip()
                    sem_action, ai_data, sem_rej, item_vec, _ = evaluate_semantic_duplicate(
                        conn, "WALLAPOP", item_id, title, desc, price,
                        radar_id=target.get("id", ""),
                        current_seller_id=current_seller_id,
                        image_hash_provider=_img_hash_provider,
                    )
                    if sem_action == "reject":
                        if flash_msg:
                            resolve_flash_discord_alert(
                                flash_msg, approved=False, reason=f"Duplicado semántico rechazado: {sem_rej}",
                                title=title, price=price, item_url=item_url
                            )
                        mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        _emit_pipeline("sem_dup_reject")
                        continue

                    if ai_data is None:
                        # PUERTA 2: Visión adaptativa (1 foto primero, hasta 3 si la confianza visual es baja)
                        _img_hash_provider()  # recoge la foto descargada en paralelo con la Puerta 1
                        if _img_prefetch["photos"] is not None:
                            raw_photos = _img_prefetch["photos"]
                        else:
                            t["image"] = time.perf_counter()
                            raw_photos = download_item_images(img_urls, rotator=rotator, max_images=1)

                        # Comprobación de dHash para fotos reutilizadas (P2-5)
                        if raw_photos and len(raw_photos) > 0 and raw_photos[0]:
                            img_h = _img_prefetch["dhash"] or calculate_dhash(raw_photos[0])
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
                                        _emit_pipeline("image_fraud_reject")
                                        continue

                        round_evaluated += 1
                        drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                        print(f"[EVALUANDO WALLAPOP IA + {len(raw_photos)} FOTOS]{drop_tag} {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€)...")
                        t["vision"] = time.perf_counter()
                        ai_data = analyze_and_generate_kit(
                            "WALLAPOP", title, desc, price, landed_cost, is_foreign, target=target,
                            conn=conn, health_monitor=health_monitor, raw_images=raw_photos, call_type="vision", pre_cex=item["_pre_cex"]
                        )

                        # Reintento con 3 fotos si la IA aprueba pero duda visualmente y hay más fotos disponibles
                        if ai_data and ai_data.get("is_valid_deal", False) and ai_data.get("visual_confidence") == "baja" and len(img_urls) > 1:
                            print(f"[WALLAPOP REINTENTO VISUAL] Confianza baja con 1 foto para '{title}'. Descargando hasta 3 fotos...")
                            more_photos = download_item_images(img_urls, rotator=rotator, max_images=3)
                            if len(more_photos) > 1:
                                ai_data_retry = analyze_and_generate_kit(
                                    "WALLAPOP", title, desc, price, landed_cost, is_foreign, target=target,
                                    conn=conn, health_monitor=health_monitor, raw_images=more_photos, call_type="vision_retry", pre_cex=item["_pre_cex"]
                                )
                                if ai_data_retry:
                                    ai_data = ai_data_retry
                        t["vision_end"] = time.perf_counter()

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
                        _emit_pipeline("budget_paused")
                        continue

                    if not ai_data or ai_data.get("_api_error"):
                        print(f"[WALLAPOP WARN] Fallo temporal IA en {title} ({price:.0f}€). Fail-open.")
                        fail_cnt = item_eval_fails.get(item_id, 0) + 1
                        item_eval_fails[item_id] = fail_cnt
                        if fail_cnt >= 2:
                            mark_item_seen(conn, "WALLAPOP", item_id, title, price)
                        _emit_pipeline("vision_error")
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
                        _emit_pipeline("vision_reject")
                        continue

                    # FASE 3: Verificación CeX España en vivo + Regla de Amortización + Rating Determinista
                    cex_data = None
                    t["cex"] = time.perf_counter()
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
                    t["cex_end"] = time.perf_counter()

                    ai_data["fraud_risk_score"] = int(fraud_eval.get("risk", 0) or 0)
                    approved, score, optimal_price, best_net_profit, charger_penalty, reject_msg = evaluate_deal_math_and_score(
                        price=price, landed_cost=landed_cost, target=target, ai_data=ai_data, cex_data=cex_data,
                        min_profit=min_profit, min_personal_savings=min_personal_savings, min_score=min_score,
                        filter_pro_seller=True, conn=conn
                    )
                    if not approved:
                        print(f"[WALLAPOP DESCARTADO - {score}/10] {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€) -> {reject_msg}")
                        if flash_msg:
                            resolve_flash_discord_alert(
                                flash_msg, approved=False, reason=reject_msg,
                                title=title, price=price, item_url=item_url
                            )
                        _emit_pipeline("math_reject")
                        continue

                    kit_folder = build_kit_folder_name("WALLAPOP", item_id, ai_data)
                    print(f"[🔥 CHOLLO WALLAPOP ({score}/10) +{best_net_profit:.0f}€] {title} ({landed_cost:.0f}€ puesto en casa) -> Alertando Discord!")
                    t["alert"] = time.perf_counter()
                    ai_data["_pipeline_ms"] = compute_pipeline_ms(t)
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
                        item_vec=item_vec,
                        listing_published_at=listing_ts
                    )
                    raw_city = item.get("seller_city") or (item.get("location") or {}).get("city")
                    if raw_city:
                        item["seller_city"] = raw_city
                    if country_tag and not item.get("country_tag"):
                        item["country_tag"] = country_tag
                    item["url"] = item_url
                    send_one_tap_ntfy_alert(
                        item,
                        build_ntfy_analysis_payload(
                            ai_data, cex_data, best_net_profit, target=target, score=score
                        ),
                        is_flash=False
                    )
                    t["alert_end"] = time.perf_counter()
                    target_alerts += 1
                    ms_final = _emit_pipeline("alerted")
                    try:
                        conn.execute(
                            "UPDATE deals SET alert_context_json = json_set(COALESCE(NULLIF(alert_context_json,''),'{}'), "
                            "'$.pipeline_ms.ms_alert', ?) WHERE deal_id = ?",
                            (ms_final["ms_alert"], f"WAL_{item_id}"[:40]),
                        )
                        conn.commit()
                    except Exception:
                        pass

            print(f"[RADAR WALLAPOP] {target.get('name', task.radar_id)} ('{q}'): {target_items_count} items ({target_new_count} nuevos)")

        scheduler.done(task, yield_count=scheduler.yield_score(target_candidates, target_alerts))
        q_dur = round(time.time() - q_start, 3)
        fresh_n, fresh_min_s, fresh_med_s = summarize_ages(fresh_ages)
        log_metric(
            event="query_completed",
            service="wallapop",
            platform="WALLAPOP",
            radar_id=task.radar_id,
            query=q,
            tier=task.tier,
            duration_s=q_dur,
            items_fetched=target_items_count,
            items_new=target_new_count,
            items_evaluated=round_evaluated - eval_before,
            rate_per_s=round(bucket.rate, 3),
            candidates=target_candidates,
            alerts=target_alerts,
            interval_eff_s=round(task.effective_interval, 1),
            cost=round(task.cost, 2),
            yield_per_h=round(task.yield_rate * 3600.0, 3),
            fresh_new=fresh_n,
            fresh_age_min_s=fresh_min_s,
            fresh_age_med_s=fresh_med_s,
        )

        queries_in_round += 1
        cycle_size = max(1, len(scheduler.tasks))
        if queries_in_round >= cycle_size:
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
            reason = f"TokenBucket {bucket.rate:.2f} req/s ({'nocturno x' + str(int(scheduler.night_multiplier)) if scheduler.is_night_time() else 'diurno'})"

            log_metric(
                event="round_completed",
                service="wallapop",
                platform="WALLAPOP",
                round=round_num,
                duration_s=duration,
                items_new=round_new,
                items_evaluated=round_evaluated,
                interval_s=0,
                reason=reason
            )

            print(f"[WALLAPOP] Ciclo #{round_num} ({queries_in_round} consultas) en {duration}s ({round_new} nuevos, {round_evaluated} IA). {reason}.\n")
            round_start = time.time()
            round_new = 0
            round_evaluated = 0
            round_total_items = 0
            queries_in_round = 0


if __name__ == "__main__":
    main()
