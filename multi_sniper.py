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
import time
import random
from curl_cffi import requests

from antiban import BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL, AI_ANALYST_MODEL,
    init_db, load_config,
    check_item_seen_or_dropped, is_duplicate_recent_deal, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, download_item_images, enhance_and_clean_image,
    analyze_and_generate_kit, evaluate_deal_math_and_score,
    build_kit_folder_name, save_resale_kit, send_discord_alert,
    fetch_cashconverters, fetch_milanuncios, fetch_ebay_es, fetch_todocoleccion
)

SCAN_INTERVAL = int(os.getenv("MULTI_SCAN_INTERVAL_SECONDS", "45"))

rotator = BrowserProfileRotator(rotate_every_rounds=30)
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
health_monitor = AntiBanHealthMonitor("MULTI-MARKET SNIPER")

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
        "default_shipping": "🏪 Compra Directa Web + Garantía"
    },
    {
        "platform": "MILANUNCIOS",
        "key": "milanuncios",
        "kw_field": "milanuncios_keywords",
        "fetcher": fetch_milanuncios,
        "FilterProSeller": True,
        "img_safari": False,
        "query_sleep": (0.6, 1.3),
        "default_city": "España",
        "default_shipping": "📦 Milanuncios Express"
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
        "default_shipping": "🌐 Envío eBay"
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
        "default_shipping": "📦 Envío Correos TC"
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


def send_startup_ping(total_targets):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        return
    engine = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal 3-Fotos)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    msg = {
        "content": (
            f"🌐 **Boti Multi-Market Sniper v4.2 (ORQUESTADOR ROUND-ROBIN • MULTIMODAL + CEX) ONLINE**\n"
            f"🛒 **Ciclo en serie:** `Cash Converters ➔ Milanuncios ➔ eBay ES/UE ➔ TodoColección`\n"
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

    while True:
        config = load_config()
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

        for store_idx, store in enumerate(STORE_PIPELINE):
            plat = store["platform"]
            plat_key = store["key"]
            fetcher = store["fetcher"]
            is_warmup = warmup_by_plat.get(plat, False)
            q_min_s, q_max_s = store["query_sleep"]

            store_items_count = 0
            store_new_count = 0

            for target in targets:
                if not target.get("enabled", True):
                    continue
                if not timing.should_scan_target(target, round_num, warmup=is_warmup):
                    continue

                enabled_plats = [p.lower() for p in target.get("platforms", [])]
                if plat_key not in enabled_plats:
                    continue

                queries = get_queries_for_store(target, store["kw_field"])
                mode = target.get("mode", "resale")

                for q in queries[:3]:
                    items = fetcher(rotator, q, target["min_price"], target["max_price"])
                    store_items_count += len(items)

                    for item in items:
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

                        store_new_count += 1
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

                        # FASE 2 ("El Punto Dulce"): Descargar hasta 3 fotos reales del candidato
                        img_urls = item.get("images", [])
                        cover_url = img_urls[0] if img_urls else ""
                        raw_photos = download_item_images(
                            img_urls, rotator=rotator, max_images=3, force_safari=store["img_safari"]
                        )

                        round_evaluated += 1
                        drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                        print(f"[EVALUANDO {plat} IA + {len(raw_photos)} FOTOS]{drop_tag} {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€)...")

                        ai_data = analyze_and_generate_kit(
                            plat, title, desc, price, landed_cost, False, target=target,
                            conn=conn, health_monitor=health_monitor, raw_images=raw_photos
                        )

                        if not ai_data:
                            fail_key = f"{plat}_{item_id}"
                            fail_cnt = item_eval_fails.get(fail_key, 0) + 1
                            item_eval_fails[fail_key] = fail_cnt
                            if fail_cnt >= 2:
                                mark_item_seen(conn, plat, item_id, title, price)
                            continue

                        mark_item_seen(conn, plat, item_id, title, price)

                        is_pro = bool(ai_data.get("is_pro_seller", False)) if store["FilterProSeller"] else False
                        if not ai_data.get("is_valid_deal", False) or is_pro or ai_data.get("has_defect_or_missing_parts", False):
                            reason = ai_data.get("reject_reason") or "Descartado en inspección visual IA"
                            print(f"[{plat} DESCARTADO IA] {title} ({price:.0f}€) -> {reason}")
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
                            filter_pro_seller=store["FilterProSeller"]
                        )
                        if not approved:
                            print(f"[{plat} DESCARTADO - {score}/10] {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€) -> {reject_msg}")
                            continue

                        kit_folder = build_kit_folder_name(plat, item_id, ai_data)
                        enhanced_photos = []
                        try:
                            for p_bytes in raw_photos:
                                try:
                                    enhanced_photos.append(enhance_and_clean_image(p_bytes))
                                except Exception:
                                    pass
                            save_resale_kit(
                                kit_folder, plat, item_id, title, item["url"],
                                ai_data, optimal_price, landed_cost, enhanced_photos
                            )
                        except Exception as e_kit:
                            print(f"[WARN] Error guardando kit {plat}: {e_kit}")

                        enhanced_cover = enhanced_photos[0] if enhanced_photos else (raw_photos[0] if raw_photos else None)
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
                            enhanced_cover_bytes=enhanced_cover,
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
                            price_drop_info=price_drop_info
                        )
                        timing.item_processing_sleep()

                    timing.inter_query_sleep(q_min_s, q_max_s)

            if is_warmup:
                print(f"[INFO] Warm-up {plat} completado ({count_platform_seen(conn, plat)} items). Filtro activo.")
                warmup_by_plat[plat] = False
            else:
                print(f"[MULTI-MARKET • {plat}] {store_items_count} anuncios revisados ({store_new_count} nuevos)")

            if store_idx < len(STORE_PIPELINE) - 1:
                time.sleep(random.uniform(2.5, 4.5))

        round_num += 1
        duration = round(time.time() - round_start, 1)
        rotator.step_round()
        sleep_secs, is_night, transition, reason = timing.calculate_round_sleep(SCAN_INTERVAL, "MULTI-MARKET")
        if transition:
            health_monitor.notify_transition(transition)

        print(
            f"[MULTI-MARKET] Ronda #{round_num} completada en {duration}s "
            f"({round_new} nuevos, {round_evaluated} IA). {reason}. Próximo ciclo en {sleep_secs}s.\n"
        )
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()
