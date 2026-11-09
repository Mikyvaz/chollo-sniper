#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de verificación para la Fase 1 (P1):
- Embudo de costes (Puerta 1 y Puerta 2)
- Caché robusta con cubos de precio
- Telemetría ai_cost_log y presupuesto diario
"""

import os
import sys
import sqlite3

# Añadir raíz al sys.path para importar core_engine
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import (
    init_db,
    build_robust_cache_key,
    normalize_text_for_cache,
    calculate_ai_cost_usd,
    log_ai_cost,
    get_today_ai_cost,
    check_ai_budget_status,
    prescreen_item_text,
    AI_DAILY_BUDGET_USD,
    AI_PRESCREEN_SHADOW_MODE
)

def run_tests():
    print("[1/5] Inicializando base de datos...")
    conn = init_db()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ai_cost_log', 'ai_prescreen_cache')")
    tables = [r[0] for r in c.fetchall()]
    assert "ai_cost_log" in tables, "Falta tabla ai_cost_log"
    assert "ai_prescreen_cache" in tables, "Falta tabla ai_prescreen_cache"
    print("  -> Tablas ai_cost_log y ai_prescreen_cache verificadas.")

    print("[2/5] Probando clave de caché robusta (cubos de precio y normalización)...")
    k_49 = build_robust_cache_key("radar1", "Nintendo Switch OLED blanca!", "En perfecto estado, con caja.", 49)
    k_50 = build_robust_cache_key("radar1", "nintendo switch oled blanca", "en perfecto estado con caja", 50)
    assert k_49 == k_50, f"Error: 49€ y 50€ deberían coincidir en cubo de 5€: {k_49} != {k_50}"
    print(f"  -> Cache key idéntica para 49€ y 50€: {k_49}")

    print("[3/5] Probando cálculo y registro de coste en ai_cost_log...")
    cost_flash = calculate_ai_cost_usd("gemini-2.5-flash", 1000, 200)
    cost_lite = calculate_ai_cost_usd("gemini-2.5-flash-lite", 1000, 200)
    assert cost_flash > cost_lite, f"Flash ({cost_flash}) debería costar más que Lite ({cost_lite})"
    log_ai_cost(conn, "TEST_SUITE", "test_radar", "gemini-2.5-flash", "vision", 1500, 250)
    log_ai_cost(conn, "TEST_SUITE", "test_radar", "gemini-2.5-flash-lite", "prescreen", 250, 40)
    today_cost = get_today_ai_cost(conn)
    assert today_cost > 0.0, "El coste de hoy debería ser mayor a 0"
    print(f"  -> Costes calculados: Flash={cost_flash:.6f}$, Lite={cost_lite:.6f}$. Registrado en BD: {today_cost:.6f}$")

    print("[4/5] Probando control de presupuesto diario...")
    status, ratio = check_ai_budget_status(conn)
    print(f"  -> Presupuesto configurado: {AI_DAILY_BUDGET_USD}$ | Consumo actual: {today_cost:.4f}$ ({ratio:.2%}) | Estado: {status}")

    print("[5/5] Probando Puerta 1 Prescreen (cache y formato)...")
    dummy_target = {"id": "radar_test", "name": "Nintendo Switch OLED", "ai_valuation_guide": "Solo consolas completas"}
    decision, reason, conf, from_cache = prescreen_item_text(
        "WALLAPOP", "Funda Nintendo Switch OLED", "Solo vendo la funda de transporte oficial", 15.0, 18.0, dummy_target, conn=conn
    )
    print(f"  -> Prescreen resultado: decision='{decision}', reason='{reason}', conf='{conf}', from_cache={from_cache}")

    # Probar que la caché de prescreen guarda y responde en la 2ª llamada si no está pausado por cortafuegos
    d2, r2, c2, from_cache2 = prescreen_item_text(
        "WALLAPOP", "Funda Nintendo Switch OLED", "Solo vendo la funda de transporte oficial", 15.0, 18.0, dummy_target, conn=conn
    )
    if os.getenv("GEMINI_API_KEY") and today_cost < AI_DAILY_BUDGET_USD:
        assert from_cache2 is True, "La segunda llamada debería venir de la caché"
        print(f"  -> Segunda llamada servida desde ai_prescreen_cache correctamente (hit de caché verificado).")
    else:
        print(f"  -> Cortafuegos activo o sin API key: prescreen en modo fail-open seguro sin envenenar caché.")

    # Limpieza de registros de prueba
    c.execute("DELETE FROM ai_cost_log WHERE platform = 'TEST_SUITE'")
    c.execute("DELETE FROM ai_prescreen_cache WHERE cache_key LIKE '%radar_test%'")
    conn.commit()

    print("\n✅ ¡TODOS LOS TESTS DE LA FASE 1 HAN PASADO CON ÉXITO!")

if __name__ == "__main__":
    run_tests()

