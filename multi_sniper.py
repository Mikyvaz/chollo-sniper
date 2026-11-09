# -*- coding: utf-8 -*-
"""
Multi-Market Sequential Round-Robin Sniper Engine v4.2 (Boti Arbitrage & Homelab Suite).
Orquestador secuencial unificado para los 4 mercados secundarios:
  1. Cash Converters España (Web Directa + 3 Años Garantía)
  2. Milanuncios España (Particulares SSR)
  3. eBay España / UE (¡Cómpralo Ya! Recién Listados)
  4. TodoColección España (Lotes Retro & Coleccionismo)

Ventajas de arquitectura v4.2:
- Embudo en 3 fases: Pre-Filtro Determinista -> Visión Multimodal Gemini 2.5 Flash (hasta 3 fotos) -> Amortización CeX España.
- Un único contenedor y conexión SQLite WAL (0 bloqueos 'database is locked').
- Pausas limpias entre tiendas y perfiles TLS adaptados por origen (Chrome / Safari 17.0).
"""

import os
import copy
import json
import time
import random
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from curl_cffi import requests

from antiban import BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor, ParserHealthTracker, default_rate_limiter
from logger import setup_logger, log_metric
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL, AI_ANALYST_MODEL,
    init_db, load_config, run_db_maintenance,
    check_item_seen_or_dropped, is_duplicate_recent_deal, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, download_item_images,
    analyze_and_generate_kit, evaluate_deal_math_and_score,
    build_kit_folder_name, send_discord_alert, send_flash_discord_alert, resolve_flash_discord_alert,
    send_one_tap_ntfy_alert, sanitize_item_url,
    fetch_cashconverters, fetch_milanuncios, fetch_ebay_es, fetch_todocoleccion,
    touch_heartbeat, sleep_with_heartbeat,
    prescreen_item_text, AI_PRESCREEN_SHADOW_MODE,
    evaluate_fraud_risk, log_fraud_event, calculate_dhash, check_and_record_image_hash,
    enrich_seller_profile, evaluate_seller_risk,
    find_semantic_duplicate, save_item_embedding, WafBlockedError, is_ai_budget_paused,
    CONSOLE_RADAR_IDS, evaluate_bundle_cex_bonus, pre_gate_cex_offer_margin
)

SCAN_INTERVAL = int(os.getenv("MULTI_SCAN_INTERVAL_SECONDS", "45"))

rotator = BrowserProfileRotator(rotate_every_rounds=30)
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
health_monitor = AntiBanHealthMonitor("MULTI-MARKET SNIPER")
parser_tracker = ParserHealthTracker("MULTI-MARKET SNIPER", default_threshold=3)
logger = setup_logger("MULTI-SNIPER")
default_rate_limiter.register_platform("MULTI-MARKET", SCAN_INTERVAL)
for _p in ("MILANUNCIOS", "CASHCONVERTERS", "TODOCOLECCION", "EBAY"):
    default_rate_limiter.register_platform(_p, SCAN_INTERVAL)

STORE_PIPELINE = [
    {
        "platform": "CASHCONVERTERS",
        "key": "cashconverters",
        "kw_field": "cashconverters_keywords",
        "fetcher": fetch_cashconverters,
        "FilterProSeller": False,
        "img_safari": False,
        "query_sleep": (0.6, 1.3),
        "default_city": "Cash Converters España",
        "default_shipping": "🏪 Compra Directa Web + Garantía",
        "cooldown_until": 0
    },
    {
        "platform": "MILANUNCIOS",
        "key": "milanuncios",
        "kw_field": "milanuncios_keywords",
        "fetcher": fetch_milanuncios,
        "FilterProSeller": True,
        "img_safari": True,
        "query_sleep": (1.5, 3.0),
        "default_city": "España",
        "default_shipping": "📦 Milanuncios Express",
        "cooldown_until": 0
    },
    {
        "platform": "EBAY",
        "key": "ebay",
        "kw_field": "ebay_keywords",
        "fetcher": fetch_ebay_es,
        "FilterProSeller": False,
        "img_safari": True,
        "query_sleep": (0.8, 1.8),
        "default_city": "eBay ES/UE",
        "default_shipping": "🌐 Envío eBay",
        "cooldown_until": 0
    },
    {
        "platform": "TODOCOLECCION",
        "key": "todocoleccion",
        "kw_field": "todocoleccion_keywords",
        "fetcher": fetch_todocoleccion,
        "FilterProSeller": False,
        "img_safari": True,
        "query_sleep": (0.7, 1.5),
        "default_city": "TodoColección España",
        "default_shipping": "📦 Envío Correos TC",
        "cooldown_until": 0
    }
]


def get_queries_for_store(target, kw_field):
    if kw_field in target:
        mk = target[kw_field]
        return mk if isinstance(mk, list) else [mk]
    if "multi_keywords" in target:
        mk = target["multi_keywords"]
        return mk if isinstance(mk, list) else [mk]
    return [target.get("keywords", "").strip()]


def fetch_store_for_target(store, target, rotator):
    """
    Función worker para ThreadPoolExecutor (Task 4.5):
    Ejecuta la captura de anuncios para una tienda y un radar específicos.
    NO interactúa con SQLite (conn) para mantener total seguridad multihilo.
    """
    plat = store["platform"]
    fetcher = store["fetcher"]
    queries = get_queries_for_store(target, store["kw_field"])
    q_min_s, q_max_s = store.get("query_sleep", (0.6, 1.3))

    batches = []
    waf_err = None

    for q_idx, q in enumerate(queries[:3]):
        if q_idx > 0:
            time.sleep(random.uniform(q_min_s, q_max_s))
        try:
            items = fetcher(rotator, q, target["min_price"], target["max_price"])
            batches.append((q, items or []))
        except WafBlockedError as wbe:
            waf_err = wbe
            break
        except Exception:
            batches.append((q, []))

    return store, batches, waf_err


def send_startup_ping(total_targets):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        return
    engine = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal 3-Fotos)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    msg = {
        "content": (
            f"🌐 **Boti Multi-Market Sniper v5.0 (CONCURRENTE PARALELO • MULTIMODAL + CEX) ONLINE**\n"
            f"⚡ **Captura paralela 4x:** `Cash Converters || Milanuncios || eBay ES/UE || TodoColección`\n"
            f"🧠 Motor IA: **`{engine}`** | Radares activos: **{total_targets}**"
        )
    }
    try:
        requests.post(WEBHOOK_URL, json=msg, timeout=10)
    except Exception:
        pass


def main():
    conn = init_db()
    config = load_config()
    round_num = 0
    item_eval_fails = {}
    budget_deferred = set()  # Anuncios aplazados por pausa de presupuesto IA (A-001)

    warmup_by_plat = {
        st["platform"]: (count_platform_seen(conn, st["platform"]) == 0)
        for st in STORE_PIPELINE
    }

    active_cnt = sum(1 for t in config.get("targets", []) if t.get("enabled", True))
    print(
        f"[STARTUP] Multi-Market Sniper v4.2 activo | IA: {AI_ANALYST_MODEL} | "
        f"Radares activos: {active_cnt} | Warmup: {warmup_by_plat}"
    )
    send_startup_ping(active_cnt)
    last_maintenance = time.time()
    touch_heartbeat()

    while True:
        touch_heartbeat()
        if time.time() - last_maintenance > 21600:
            run_db_maintenance(conn)
            item_eval_fails.clear()
            last_maintenance = time.time()
        try:
            config = load_config()
        except Exception as e:
            print(f"[WARN] Error transitorio leyendo config.json ({e}). Reteniendo configuración en memoria.")
        targets = config.get("targets", [])
        global_blacklist = config.get("global_blacklist", [])
        min_profit = float(config.get("min_net_profit_eur", 35))
        min_personal_savings = float(config.get("min_personal_savings_eur", 25))
        min_score = int(config.get("min_deal_score", 8))
        drop_pct = float(config.get("price_drop_threshold_pct", 15))
        drop_eur = float(config.get("price_drop_min_eur", 10))

        round_start = time.time()
        round_new = 0
        round_evaluated = 0
        store_stats = {st["platform"]: {"items": 0, "new": 0} for st in STORE_PIPELINE}
        is_any_warmup = any(warmup_by_plat.values())

        # A-005: al salir de un cooldown, los errores previos no deben seguir frenando la tienda
        for st in STORE_PIPELINE:
            if st.get("cooldown_until", 0) and st["cooldown_until"] <= round_start:
                st["cooldown_until"] = 0
                st["next_fetch_at"] = 0
                default_rate_limiter.reset_window(st["platform"])
                print(f"[{st['platform']} COOLDOWN FINALIZADO] Ventana de errores reiniciada.")

        # A-005: cada tienda tiene su propio turno; las que no tocan esta ronda se saltan
        due_plats = {
            st["platform"] for st in STORE_PIPELINE
            if st.get("cooldown_until", 0) <= round_start and st.get("next_fetch_at", 0) <= round_start
        }
        fetched_plats = set()

        for target in targets:
            touch_heartbeat()
            if not target.get("enabled", True):
                continue
            if not timing.should_scan_target(target, round_num, warmup=is_any_warmup):
                continue

            enabled_plats = [p.lower() for p in target.get("platforms", [])]
            stores_to_fetch = [
                st for st in STORE_PIPELINE
                if st["key"] in enabled_plats and st["platform"] in due_plats
                and st.get("cooldown_until", 0) <= time.time()
            ]

            if not stores_to_fetch:
                continue
            fetched_plats.update(st["platform"] for st in stores_to_fetch)

            # Task 4.5: Paralelización concurrente de las tiendas para este radar en ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(stores_to_fetch))) as executor:
                future_map = {
                    executor.submit(fetch_store_for_target, st, target, rotator): st
                    for st in stores_to_fetch
                }
                # Concurrencia acotada con timeout de seguridad (Punto 6)
                done, not_done = concurrent.futures.wait(future_map.keys(), timeout=40.0)
                for fut in not_done:
                    fut.cancel()
                    st = future_map.get(fut, {})
                    plat_name = st.get("platform", "DESCONOCIDA")
                    print(f"[MULTI-SNIPER TIMEOUT] Timeout de 40s alcanzado para tienda {plat_name}. Cancelando tarea.")

                for fut in done:
                    if fut.cancelled() or fut.exception() is not None:
                        continue
                    try:
                        store, batches, waf_err = fut.result()
                    except Exception as e:
                        print(f"[MULTI-SNIPER ERROR] Excepción en worker de tienda: {e}")
                        continue
                    plat = store["platform"]
                    is_warmup = warmup_by_plat.get(plat, False)

                    if waf_err:
                        cd_secs = 900  # 15 minutos de cooldown preventivo
                        store["cooldown_until"] = time.time() + cd_secs
                        print(f"[{plat} COOLDOWN PREVENTIVO] WAF/DataDome bloqueo detectado ({waf_err}). Pausando {plat} durante {cd_secs // 60} minutos...")
                        health_monitor.send_health_embed(
                            f"🛡️ [COOLDOWN PREVENTIVO] {plat}: WAF / PerimeterX Detectado",
                            f"Se ha detectado bloqueo o desafío anti-bot en **{plat}** ({waf_err}).\n\n"
                            f"• Se activa cooldown preventivo de **{cd_secs // 60} minutos** para esta tienda.\n"
                            f"• Las demás tiendas siguen operando con normalidad.",
                            0xE67E22
                        )
                        continue

                    mode = target.get("mode", "resale")

                    # Recolectar todos los ítems de las búsquedas
                    raw_items = []
                    for q, items in batches:
                        store_stats[plat]["items"] += len(items)
                        if items:
                            parser_tracker.record_success(plat, q, len(items), health_monitor)
                            raw_items.extend(items)

                    # Cola de Prioridad Fast-Lane por Margen
                    tgt_max = max(1.0, float(target.get("max_price", 9999)))
                    tgt_direct = float(target.get("direct_buy_max_price") or tgt_max)

                    def fast_lane_key(it):
                        p = float(it.get("price") or 0.0)
                        is_direct = 0 if p <= tgt_direct else 1
                        ratio = p / tgt_max
                        return (is_direct, ratio)

                    raw_items.sort(key=fast_lane_key)

                    for item in raw_items:
                        item_id = item["id"]
                        title = item["title"]
                        desc = item["description"]
                        price = item["price"]
                        landed_cost = item["landed_cost"]
                        fees = item["fees"]

                        already_seen, price_drop_info = check_item_seen_or_dropped(
                            conn, plat, item_id, price, min_drop_pct=drop_pct, min_drop_eur=drop_eur
                        )
                        if already_seen:
                            continue

                        # Aplazado por presupuesto: esperar a que se reanude la IA sin repetir la alerta flash
                        deferred_key = f"{plat}_{item_id}"
                        was_budget_deferred = deferred_key in budget_deferred
                        if was_budget_deferred:
                            if is_ai_budget_paused(conn):
                                continue
                            budget_deferred.discard(deferred_key)

                        store_stats[plat]["new"] += 1
                        round_new += 1

                        if is_warmup:
                            mark_item_seen(conn, plat, item_id, title, price)
                            continue

                        # FASE 1: Pre-Filtro Determinista
                        if not passes_filters(title, desc, price, target, global_blacklist):
                            mark_item_seen(conn, plat, item_id, title, price)
                            continue

                        if is_duplicate_recent_deal(conn, plat, title, price):
                            mark_item_seen(conn, plat, item_id, title, price)
                            continue

                        item["platform"] = plat
                        enrich_seller_profile(item)

                        # EVALUACIÓN DE FRAUDE DETERMINISTA EN CAPAS (P2)
                        fraud_eval = evaluate_fraud_risk(
                            title, desc, price, radar_id=target.get("id", ""),
                            conn=conn, platform=plat, item_id=item_id,
                            seller=item.get("seller")
                        )

                        if fraud_eval.get("is_fraud") or fraud_eval.get("should_block") or fraud_eval.get("risk", 0) >= 4:
                            log_fraud_event(conn, plat, item_id, title, price, fraud_eval["risk"], fraud_eval["hits"])
                            print(f"[{plat} FRAUDE BLOQUEADO] {title} ({price:.0f}€) -> {[h['label'] for h in fraud_eval['hits']]}")
                            mark_item_seen(conn, plat, item_id, title, price)
                            continue

                        # ALERTA FLASH + 1-TAP (Solo si el vendedor no tiene alerta de riesgo)
                        s_rev = int(item.get("seller_reviews_count", 0) or 0)
                        s_rat = item.get("seller_rating")
                        is_cash = (plat == "CASHCONVERTERS") or ("CASH" in str(item.get("platform", "")).upper())
                        can_flash = is_cash or (s_rev >= 2 and (s_rat is not None and s_rat >= 4.0))

                        flash_msg = None
                        if fraud_eval["risk"] < 2 and can_flash and not was_budget_deferred and price <= tgt_direct:
                            clean_url = sanitize_item_url(item.get("url", ""), plat)
                            flash_msg = send_flash_discord_alert(
                                platform=plat,
                                item_id=item_id,
                                title=title,
                                price=price,
                                landed_cost=landed_cost,
                                item_url=clean_url,
                                image_url=(item.get("images") or [""])[0],
                                target=target
                            )
                            send_one_tap_ntfy_alert(item, {"cex_cash": 0, "p2p_net_profit": 0}, is_flash=True)

                        # Pre-gating matemático CeX antes de IA (TASK-03)
                        ok_pregate, pregate_reason = pre_gate_cex_offer_margin(conn, item, target, config)
                        if not ok_pregate:
                            print(f"[{plat} PRE-GATE CEX DESCARTADO] {title} ({price:.0f}€) -> {pregate_reason}")
                            if flash_msg:
                                resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Pre-gate CeX: {pregate_reason}", title=title, price=price, item_url=item.get("url", ""))
                            mark_item_seen(conn, plat, item_id, title, price)
                            continue

                        # PUERTA 1: Criba ultra-ligera por texto (0 fotos, 0 thinking tokens)
                        ps_decision, ps_reason, ps_conf, from_cache = prescreen_item_text(
                            plat, title, desc, price, landed_cost, target, conn=conn, health_monitor=health_monitor
                        )

                        if ps_decision == "reject":
                            if AI_PRESCREEN_SHADOW_MODE:
                                print(f"[{plat} SHADOW PRESCREEN RECHAZARÍA] {title} ({price:.0f}€) -> {ps_reason} | Pasando a visión por shadow mode...")
                            else:
                                print(f"[{plat} DESCARTADO TEXTO PUERTA 1] {title} ({price:.0f}€) -> {ps_reason}")
                                if flash_msg:
                                    resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Descartado texto Puerta 1: {ps_reason}", title=title, price=price, item_url=item.get("url", ""))
                                mark_item_seen(conn, plat, item_id, title, price)
                                continue

                        # Si hay riesgo moderado (2-3) y Puerta 1 no está segura ('unsure'), descartar y registrar evento
                        if fraud_eval["risk"] in (2, 3) and ps_decision == "unsure":
                            log_fraud_event(conn, plat, item_id, title, price, fraud_eval["risk"], fraud_eval["hits"])
                            print(f"[{plat} RIESGO MODERADO DESCARTADO] {title} ({price:.0f}€) -> {fraud_eval['warning_banner']}")
                            if flash_msg:
                                resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Riesgo moderado antifraude", title=title, price=price, item_url=item.get("url", ""))
                            mark_item_seen(conn, plat, item_id, title, price)
                            continue

                        # FASE 5: Comprobación de duplicado semántico (P1-6)
                        seller = item.get("seller")
                        current_seller_id = ""
                        if isinstance(seller, dict):
                            current_seller_id = str(seller.get("id") or seller.get("user_id") or "").strip()
                        elif seller:
                            current_seller_id = str(seller).strip()

                        text_for_embed = f"{title}\n{desc}".strip()
                        sem_dup, item_vec = find_semantic_duplicate(
                            conn, text_for_embed, radar_id=target.get("id", ""), threshold=0.92, hours=720, price=price,
                            exclude_item_key=f"{plat}:{item_id}"
                        )

                        ai_data = None
                        if sem_dup and sem_dup.get("verdict_json"):
                            try:
                                cached_verdict = json.loads(sem_dup["verdict_json"])
                                if isinstance(cached_verdict, dict):
                                    sim_score = float(sem_dup.get("similarity", sem_dup.get("score", 0.0)))
                                    cached_seller_id = str(sem_dup.get("seller_id", "")).strip()
                                    cached_price = float(sem_dup.get("price", 999999.0))
                                    is_pro = bool(cached_verdict.get("is_pro_seller", False)) if store["FilterProSeller"] else False
                                    is_cached_reject = (not cached_verdict.get("is_valid_deal", False)) or is_pro or cached_verdict.get("has_defect_or_missing_parts", False)

                                    if is_cached_reject:
                                        if sim_score >= 0.96:
                                            rej = cached_verdict.get("reject_reason") or "Descartado previamente por IA"
                                            print(f"[{plat} DEDUP SEMÁNTICA RECHAZO] '{title}' duplicado de '{sem_dup['title']}' (sim={sim_score:.2f} >= 0.96) -> {rej}")
                                            if flash_msg:
                                                resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Duplicado semántico: {rej}", title=title, price=price, item_url=item.get("url", ""))
                                            mark_item_seen(conn, plat, item_id, title, price)
                                            continue
                                        else:
                                            print(f"[{plat} DEDUP SEMÁNTICA] Match rechazado previo pero sim={sim_score:.2f} < 0.96 -> Inspección visual obligatoria")
                                            ai_data = None
                                    else:
                                        if current_seller_id and current_seller_id == cached_seller_id and price <= cached_price:
                                            print(f"[{plat} DEDUP SEMÁNTICA REUTILIZADO] '{title}' duplicado aprobado de '{sem_dup['title']}' (mismo vendedor {current_seller_id}, {price:.0f}€ <= {cached_price:.0f}€, sim={sim_score:.2f}) -> Reutilizando kit sin visión")
                                            ai_data = copy.deepcopy(cached_verdict)
                                        else:
                                            print(f"[{plat} DEDUP SEMÁNTICA] Match aprobado pero vendedor ({current_seller_id} vs {cached_seller_id}) o precio ({price:.0f}€ vs {cached_price:.0f}€) no apto para bypass -> Forzando visión")
                                            ai_data = None
                            except Exception as e:
                                print(f"[WARN] Error parseando veredicto de dedup: {e}")

                        if ai_data is None:
                            # PUERTA 2: Visión adaptativa (1 foto primero, hasta 3 si la confianza visual es baja)
                            img_urls = item.get("images", [])
                            cover_url = img_urls[0] if img_urls else ""
                            raw_photos = download_item_images(
                                img_urls, rotator=rotator, max_images=1, force_safari=store["img_safari"]
                            )

                            # Comprobación de dHash para fotos reutilizadas (P2-5)
                            if raw_photos and len(raw_photos) > 0 and raw_photos[0]:
                                img_h = calculate_dhash(raw_photos[0])
                                if img_h:
                                    is_reused, r_info = check_and_record_image_hash(conn, img_h, plat, item_id, target.get("id", ""))
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
                                            log_fraud_event(conn, plat, item_id, title, price, fraud_eval["risk"], fraud_eval["hits"])
                                            print(f"[{plat} FRAUDE POR FOTO REUTILIZADA] Bloqueado anuncio {item_id} (riesgo={fraud_eval['risk']})")
                                            if flash_msg:
                                                resolve_flash_discord_alert(flash_msg, approved=False, reason="Foto reutilizada de otro anuncio", title=title, price=price, item_url=item.get("url", ""))
                                            mark_item_seen(conn, plat, item_id, title, price)
                                            continue

                            round_evaluated += 1
                            drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                            print(f"[EVALUANDO {plat} IA + {len(raw_photos)} FOTOS]{drop_tag} {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€)...")

                            ai_data = analyze_and_generate_kit(
                                plat, title, desc, price, landed_cost, False, target=target,
                                conn=conn, health_monitor=health_monitor, raw_images=raw_photos, call_type="vision"
                            )

                            # Reintento con 3 fotos si la IA aprueba pero duda visualmente y hay más fotos disponibles
                            if ai_data and ai_data.get("is_valid_deal", False) and ai_data.get("visual_confidence") == "baja" and len(img_urls) > 1:
                                print(f"[{plat} REINTENTO VISUAL] Confianza baja con 1 foto para '{title}'. Descargando hasta 3 fotos...")
                                more_photos = download_item_images(
                                    img_urls, rotator=rotator, max_images=3, force_safari=store["img_safari"]
                                )
                                if len(more_photos) > 1:
                                    ai_data_retry = analyze_and_generate_kit(
                                        plat, title, desc, price, landed_cost, False, target=target,
                                        conn=conn, health_monitor=health_monitor, raw_images=more_photos, call_type="vision_retry"
                                    )
                                    if ai_data_retry:
                                        ai_data = ai_data_retry

                            if ai_data and not ai_data.get("_api_error"):
                                save_item_embedding(
                                    conn, f"{plat}:{item_id}", target.get("id", ""),
                                    vector_or_text=item_vec or text_for_embed,
                                    model="models/gemini-embedding-2",
                                    verdict_json=json.dumps(ai_data, ensure_ascii=False),
                                    title=title, price=price,
                                    seller_id=current_seller_id,
                                    precomputed_vec=item_vec
                                )

                        if ai_data and ai_data.get("_budget_paused"):
                            budget_deferred.add(deferred_key)
                            print(f"[{plat} BUDGET] '{title}' ({price:.0f}€) aplazado hasta que se reanude el presupuesto IA.")
                            continue

                        if not ai_data or ai_data.get("_api_error"):
                            fail_key = f"{plat}_{item_id}"
                            fail_cnt = item_eval_fails.get(fail_key, 0) + 1
                            item_eval_fails[fail_key] = fail_cnt
                            if fail_cnt >= 2:
                                mark_item_seen(conn, plat, item_id, title, price)
                            print(f"[{plat} WARN] Fallo temporal IA en {title} ({price:.0f}€). Fail-open.")
                            continue

                        mark_item_seen(conn, plat, item_id, title, price)

                        is_pro = bool(ai_data.get("is_pro_seller", False)) if store["FilterProSeller"] else False
                        if not ai_data.get("is_valid_deal", False) or is_pro or ai_data.get("has_defect_or_missing_parts", False):
                            reason = ai_data.get("reject_reason") or "Descartado en inspección visual IA"
                            print(f"[{plat} DESCARTADO IA] {title} ({price:.0f}€) -> {reason}")
                            if flash_msg:
                                resolve_flash_discord_alert(flash_msg, approved=False, reason=reason, title=title, price=price, item_url=item.get("url", ""))
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
                            filter_pro_seller=store["FilterProSeller"]
                        )
                        if not approved:
                            print(f"[{plat} DESCARTADO - {score}/10] {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€) -> {reject_msg}")
                            if flash_msg:
                                resolve_flash_discord_alert(flash_msg, approved=False, reason=reject_msg, title=title, price=price, item_url=item.get("url", ""))
                            continue

                        kit_folder = build_kit_folder_name(plat, item_id, ai_data)
                        print(f"[🔥 CHOLLO {plat} ({score}/10) +{best_net_profit:.0f}€] {title} ({landed_cost:.0f}€) -> Alertando Discord!")
                        send_discord_alert(
                            conn=conn,
                            platform=plat,
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
                            item_url=item["url"],
                            checkout_url=item.get("checkout_url", item["url"]),
                            image_url=cover_url,
                            seller_city=item.get("city", store["default_city"]),
                            is_local=item.get("is_local", False),
                            country_tag="ES",
                            shipping_label=item.get("shipping_label", store["default_shipping"]),
                            price_drop_info=price_drop_info,
                            image_urls=img_urls,
                            flash_message=flash_msg,
                            fraud_banner=fraud_eval.get("warning_banner", ""),
                            seller=item.get("seller"),
                            item_vec=item_vec
                        )
                        timing.item_processing_sleep()

            time.sleep(random.uniform(0.6, 1.4))

        for store in STORE_PIPELINE:
            plat = store["platform"]
            is_warmup = warmup_by_plat.get(plat, False)
            st_items = store_stats[plat]["items"]
            st_new = store_stats[plat]["new"]

            if plat not in fetched_plats:
                continue  # No le tocaba esta ronda (A-005): ni cierra warm-up ni cuenta como extracción vacía
            if is_warmup:
                print(f"[INFO] Warm-up {plat} completado ({count_platform_seen(conn, plat)} items). Filtro activo.")
                warmup_by_plat[plat] = False
            else:
                print(f"[MULTI-MARKET • {plat}] {st_items} anuncios revisados ({st_new} nuevos)")
                if st_items > 0:
                    parser_tracker.record_platform_success(plat, st_items, health_monitor)
                elif store.get("cooldown_until", 0) <= time.time():
                    parser_tracker.record_platform_empty(plat, health_monitor, threshold=4, reason=f"0 anuncios extraídos en todos los radares de {plat}")

        round_num += 1
        duration = round(time.time() - round_start, 1)
        rotator.step_round()
        # A-005: espera por tienda. Solo se adapta (y penaliza) la tienda que hizo peticiones;
        # la ronda duerme hasta que le toque a la PRIMERA tienda, no a la más lenta.
        now_ts = time.time()
        platform_sleeps = {}
        for st in STORE_PIPELINE:
            plat = st["platform"]
            if plat in fetched_plats:
                res = timing.calculate_round_sleep(SCAN_INTERVAL, plat)
                platform_sleeps[plat] = res
                st["last_sleep"] = res[0]
                st["next_fetch_at"] = now_ts + res[0]
            elif plat in due_plats:
                # Le tocaba pero ningún radar de esta ronda la usaba: reprogramar con su último intervalo
                st["next_fetch_at"] = now_ts + st.get("last_sleep", SCAN_INTERVAL)

        if not platform_sleeps:
            platform_sleeps = {"MULTI": timing.calculate_round_sleep(SCAN_INTERVAL, "MULTI")}
        any_transition = next((res[2] for res in platform_sleeps.values() if res[2]), None)
        if any_transition:
            health_monitor.notify_transition(any_transition)

        wake_times = []
        for st in STORE_PIPELINE:
            if st.get("cooldown_until", 0) > now_ts:
                wake_times.append((st["cooldown_until"], st["platform"]))
            elif st.get("next_fetch_at", 0) > now_ts:
                wake_times.append((st["next_fetch_at"], st["platform"]))
        if wake_times:
            next_wake, next_plat = min(wake_times)
            sleep_secs = int(max(5, round(next_wake - now_ts)))
        else:
            next_plat = None
            sleep_secs = int(max(res[0] for res in platform_sleeps.values()))
        ref = platform_sleeps.get(next_plat) or next(iter(platform_sleeps.values()))
        is_night = ref[1]
        per_store = ", ".join(f"{p}:{int(r[0])}s" for p, r in platform_sleeps.items())
        reason = f"Próxima tienda: {next_plat or '-'} | intervalos [{per_store}] | {ref[3]}"

        log_metric(
            event="round_completed",
            service="multi_market",
            platform="MULTI",
            round=round_num,
            duration_s=duration,
            items_new=round_new,
            items_evaluated=round_evaluated,
            interval_s=sleep_secs,
            reason=reason
        )

        print(
            f"[MULTI-MARKET] Ronda #{round_num} completada en {duration}s "
            f"({round_new} nuevos, {round_evaluated} IA). {reason}. Próximo ciclo en {sleep_secs}s.\n"
        )
        sleep_with_heartbeat(sleep_secs)


if __name__ == "__main__":
    main()
