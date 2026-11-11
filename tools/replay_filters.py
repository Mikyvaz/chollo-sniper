"""Reproduce los filtros de la Puerta 0 sobre las alertas reales de los últimos días.

Se ejecuta SIEMPRE aislado (BD copiada, sin red):
    tools/run_isolated.sh tools/replay_filters.py [dias]

1. Recorre `deals` de los últimos N días (14 por defecto) y cuenta cuántos dejan de pasar
   `passes_filters` con el código y la `config.json` actuales (las alertas pasaron el filtro
   antiguo cuando se enviaron).
2. Imprime los casos legítimos / sospechosos de la auditoría con el veredicto nuevo.
"""
import os
import sqlite3
import sys

assert os.environ.get("SNIPER_ISOLATED") == "1", "Ejecuta con tools/run_isolated.sh"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core_engine as ce  # noqa: E402

DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 14

# (radar, título, descripción, precio, esperado)
CASOS = [
    ("switch_std_lite", "Mario Kart 8 Deluxe Nintendo Switch", "Juego en perfecto estado", 28, "RECHAZAR (T1.2)"),
    ("switch_std_lite", "Luigi's mansion 3 Nintendo switch", "", 35, "RECHAZAR (T1.2)"),
    ("switch_std_lite", "Joycon Nintendo switch", "Funcionan bien", 41, "RECHAZAR (T1.2)"),
    ("switch_std_lite", "Nintendo Switch V2 completa", "Incluye joy-con, dock y cargador original. Funciona perfecta.", 60, "PASAR"),
    ("switch_std_lite", "Switch neón 32gb", "Sin golpes ni rayadas, con caja", 60, "PASAR"),
    ("new_3ds_family", "New 3DS XL azul", "Pantalla superior e inferior sin rayadas, funciona perfecta", 95, "PASAR"),
    ("new_3ds_family", "Nintendo 3DS XL", "Se vende sin caja, no cargador. Funciona.", 70, "PASAR"),
    ("apple_high_liquidity", "iPad 9 64GB wifi", "Sin bloqueo iCloud, desvinculado. Batería 90%", 120, "PASAR"),
    ("ps5_consoles_gta6", "PS5 Slim digital", "Con 2 mandos y 3 juegos ps5. No acepto bizum, solo envío por la app", 280, "PASAR"),
    ("ps5_consoles_gta6", "PS5 slim lector", "Regalo mando de ps4. Perfecta", 290, "PASAR"),
    ("switch_oled", "Nintendo Switch OLED", "Joy-con sin problemas con drift, como nueva", 95, "PASAR"),
    ("steam_deck_handhelds", "Steam Deck 512GB", "Funciona perfecto, luz azul de carga ok", 240, "PASAR"),
    ("ps5_consoles_gta6", "PS5 Slim lector", "Solo bizum", 280, "BLOQUEAR"),
    ("new_3ds_family", "New 3DS XL azul", "Pantalla superior rota", 95, "BLOQUEAR"),
    ("steam_deck_handhelds", "Steam Deck 512GB", "Luz azul parpadeando", 240, "BLOQUEAR"),
]


def main():
    cfg = ce.load_config()
    targets = {t["id"]: t for t in cfg.get("targets", cfg.get("searches", []))}
    gb = cfg.get("global_blacklist", [])

    conn = sqlite3.connect(ce.DB_PATH)
    rows = conn.execute(
        "SELECT radar_id, title, description, price, platform FROM deals "
        "WHERE created_at >= datetime('now', ?)", (f"-{DIAS} days",)
    ).fetchall()
    conn.close()

    total = evaluadas = fallan = 0
    por_radar = {}
    ejemplos = []
    for radar, title, desc, price, platform in rows:
        total += 1
        t = targets.get(radar)
        if t is None:
            continue
        evaluadas += 1
        if not ce.passes_filters(title or "", desc or "", float(price or 0), t, gb):
            fallan += 1
            por_radar[radar] = por_radar.get(radar, 0) + 1
            if len(ejemplos) < 15:
                ejemplos.append((radar, platform, price, (title or "")[:70]))

    pasan = evaluadas - fallan
    pct = 100.0 * pasan / evaluadas if evaluadas else 100.0
    print(f"Replay de deals (últimos {DIAS} días): {total} alertas, {evaluadas} con radar vigente")
    print(f"  siguen pasando: {pasan}  |  dejan de pasar: {fallan}  |  {pct:.1f} % pasan")
    for r, n in sorted(por_radar.items(), key=lambda kv: -kv[1]):
        print(f"    {r}: {n}")
    for e in ejemplos:
        print(f"    - {e}")

    print("\nCasos de la auditoría (veredicto nuevo)")
    for radar, title, desc, price, esperado in CASOS:
        ok = ce.passes_filters(title, desc, price, targets[radar], gb)
        print(f"  [{radar}] {title!r} {price}€ -> passes={ok} | esperado={esperado}")


if __name__ == "__main__":
    main()
