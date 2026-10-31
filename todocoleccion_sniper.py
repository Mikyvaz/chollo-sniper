# -*- coding: utf-8 -*-
"""
TodoColección Sniper Engine v4.1 (Boti Arbitrage & Homelab Suite).
Contenedor dedicado al rastreo de lotes y coleccionismo en TodoColección:
- TLS Safari anti-bloqueo WAF.
- Detección instantánea de lotes recién listados (O=r).
- Ideal para videojuegos retro, Game Boy, consolas clásicas y ediciones especiales.
- Auditoría IA ultra low-token (< 200 tokens por anuncio, caché en SQLite).
"""

import os
import time
from curl_cffi import requests

from antiban import BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL,
    init_db, load_config,
    check_item_seen_or_dropped, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, enhance_and_clean_image,
    analyze_and_generate_kit, build_kit_folder_name, save_resale_kit,
    send_discord_alert, fetch_todocoleccion
)

SCAN_INTERVAL = int(os.getenv("TODOCOLECCION_SCAN_INTERVAL_SECONDS", "60"))

rotator = BrowserProfileRotator(rotate_every_rounds=8)
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
health_monitor = AntiBanHealthMonitor("TODOCOLECCION SNIPER")


def get_queries_for_target(target):
    if "todocoleccion_keywords" in target:
        mk = target["todocoleccion_keywords"]
        return mk if isinstance(mk, list) else [mk]
    if "multi_keywords" in target:
        mk = target["multi_keywords"]
        return mk if isinstance(mk, list) else [mk]
    return [target.get("keywords", "").strip()]


def send_startup_ping(total_targets):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        return
    engine = "Google Gemini Flash Direct (0-Latency)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    msg = {
        "content": (
            f"📜 **TodoColección Sniper v4.1 ONLINE**\n"
            f"🛒 **Mercado:** `TodoColección España (Lotes Retro & Coleccionismo)`\n"
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

    warmup = (count_platform_seen(conn, "TODOCOLECCION") == 0)
    print(f"[STARTUP] TodoColección Sniper v4.1 activo | Warmup inicial: {warmup}")
    send_startup_ping(len(config.get("targets", [])))

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

        for target in targets:
            if not target.get("enabled", True):
                continue
            if not timing.should_scan_target(target, round_num, warmup=warmup):
                continue

            enabled_plats = [p.lower() for p in target.get("platforms", [])]
            if "todocoleccion" not in enabled_plats:
                continue

            queries = get_queries_for_target(target)
            mode = target.get("mode", "resale")
            target_items_count = 0
            target_new_count = 0

            for q in queries[:3]:
                items = fetch_todocoleccion(rotator, q, target["min_price"], target["max_price"])
                target_items_count += len(items)

                for item in items:
                    item_id = item["id"]
                    title = item["title"]
                    desc = item["description"]
                    price = item["price"]
                    landed_cost = item["landed_cost"]
                    fees = item["fees"]

                    already_seen, price_drop_info = check_item_seen_or_dropped(
                        conn, "TODOCOLECCION", item_id, price, min_drop_pct=drop_pct, min_drop_eur=drop_eur
                    )
                    if already_seen:
                        continue

                    target_new_count += 1
                    round_new += 1

                    if warmup:
                        mark_item_seen(conn, "TODOCOLECCION", item_id, title, price)
                        continue

                    if not passes_filters(title, desc, price, target, global_blacklist):
                        mark_item_seen(conn, "TODOCOLECCION", item_id, title, price)
                        continue

                    round_evaluated += 1
                    drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                    print(f"[EVALUANDO TODOCOLECCION IA]{drop_tag} {title} ({price:.0f}€ | Casa: {landed_cost:.0f}€)...")

                    ai_data = analyze_and_generate_kit(
                        "TODOCOLECCION", title, desc, price, landed_cost, False, target=target,
                        conn=conn, health_monitor=health_monitor
                    )

                    if not ai_data:
                        fail_cnt = item_eval_fails.get(item_id, 0) + 1
                        item_eval_fails[item_id] = fail_cnt
                        if fail_cnt >= 2:
                            mark_item_seen(conn, "TODOCOLECCION", item_id, title, price)
                        continue

                    mark_item_seen(conn, "TODOCOLECCION", item_id, title, price)

                    is_valid = bool(ai_data.get("is_valid_deal", False))
                    score = int(ai_data.get("deal_score", 0) or 0)

                    if not is_valid or score < min_score:
                        reason = ai_data.get("reject_reason") or "Sin margen suficiente"
                        print(f"[TODOCOLECCION DESCARTADO - {score}/10] {title} ({price:.0f}€) -> {reason}")
                        continue

                    cex_query = ai_data.get("cex_search_query", q)
                    cex_data = fetch_cex_live(conn, cex_query, reference_price=price)

                    missing_charger = bool(ai_data.get("missing_original_charger", False)) if mode == "resale" else False
                    charger_penalty = 12.0 if missing_charger else 0.0
                    ai_est_price = float(ai_data.get("estimated_resale_price", 0) or 0)

                    if mode == "personal":
                        optimal_price = ai_est_price if ai_est_price > 0 else (cex_data["sell"] if cex_data else price)
                        best_net_profit = optimal_price - landed_cost
                        req_margin = float(target.get("min_savings_eur", min_personal_savings))
                    else:
                        cex_realistic_cap = round(cex_data["sell"] * 0.82) if (cex_data and cex_data["sell"] > 0) else 9999
                        if ai_est_price > 0 and cex_realistic_cap < 9999:
                            optimal_price = min(ai_est_price, cex_realistic_cap)
                        elif cex_realistic_cap < 9999:
                            optimal_price = cex_realistic_cap
                        else:
                            optimal_price = ai_est_price if ai_est_price > 0 else price

                        net_resale_profit = optimal_price - landed_cost - charger_penalty - 2.0
                        cex_cash_profit = (cex_data["cash"] - landed_cost - charger_penalty) if (cex_data and cex_data["cash"] > 0) else -999
                        best_net_profit = max(net_resale_profit, cex_cash_profit)
                        req_margin = float(target.get("min_profit_eur", min_profit))

                    if best_net_profit < req_margin:
                        print(
                            f"[TODOCOLECCION POCO MARGEN (+{best_net_profit:.0f}€ < {req_margin:.0f}€)] "
                            f"{title} (Casa: {landed_cost:.0f}€ | Reventa: {optimal_price:.0f}€)"
                        )
                        continue

                    img_urls = item.get("images", [])
                    cover_url = img_urls[0] if img_urls else ""
                    cover_raw = None
                    if cover_url:
                        try:
                            resp = requests.get(cover_url, impersonate="safari17_0", timeout=10)
                            if resp.status_code == 200:
                                cover_raw = resp.content
                        except Exception:
                            pass

                    kit_folder = build_kit_folder_name("TODOCOLECCION", item_id, ai_data)
                    enhanced_photos = []
                    try:
                        raw_photos = [cover_raw] if cover_raw else []
                        for p_bytes in raw_photos:
                            try:
                                enhanced_photos.append(enhance_and_clean_image(p_bytes))
                            except Exception:
                                pass
                        save_resale_kit(
                            kit_folder, "TODOCOLECCION", item_id, title, item["url"],
                            ai_data, optimal_price, landed_cost, enhanced_photos
                        )
                    except Exception as e_kit:
                        print(f"[WARN] Error guardando kit TodoColección: {e_kit}")

                    enhanced_cover = enhanced_photos[0] if enhanced_photos else cover_raw
                    print(f"[🔥 CHOLLO TODOCOLECCION +{best_net_profit:.0f}€] {title} ({landed_cost:.0f}€) -> Alertando Discord!")
                    send_discord_alert(
                        conn=conn,
                        platform="TODOCOLECCION",
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
                        seller_city=item.get("city", "TodoColección España"),
                        is_local=False,
                        country_tag="ES",
                        shipping_label=item.get("shipping_label", "📦 Envío Correos TC"),
                        price_drop_info=price_drop_info
                    )
                    timing.item_processing_sleep()

                timing.inter_query_sleep(0.6, 1.4)

            print(f"[RADAR TODOCOLECCION] {target['name']}: {target_items_count} items ({target_new_count} nuevos)")

        if warmup:
            print(f"[INFO] Warm-up TodoColección completado ({count_platform_seen(conn, 'TODOCOLECCION')} items). Filtro activo.")
            warmup = False

        round_num += 1
        duration = round(time.time() - round_start, 1)
        rotator.step_round()
        sleep_secs, is_night, transition, reason = timing.calculate_round_sleep(SCAN_INTERVAL, "TODOCOLECCION")
        if transition:
            health_monitor.notify_transition(transition)

        print(f"[TODOCOLECCION] Ronda #{round_num} en {duration}s ({round_new} nuevos, {round_evaluated} IA). {reason}. Próximo en {sleep_secs}s.\n")
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()

