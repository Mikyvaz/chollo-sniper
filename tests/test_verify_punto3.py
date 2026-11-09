#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de Verificación Exhaustivo - Punto 3 de CORRECION_FALLOS.md
Centralización de Llamadas en AI Gateway, Cortafuegos Hard Limit (0.10$/día) y max_tokens explícito.
"""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core_engine as ce
import bot

def test_punto3_complete():
    print("[TEST 1/5] Verificando limpiador y reparador determinista de JSON (repair_and_parse_json)...")
    # Caso A: JSON limpio
    assert ce.repair_and_parse_json('{"status": "ok"}') == {"status": "ok"}
    # Caso B: Bloque Markdown con fences
    assert ce.repair_and_parse_json('```json\n{"decision": "proceed", "score": 9}\n```') == {"decision": "proceed", "score": 9}
    # Caso C: Texto conversacional antes y después
    noisy = 'Hola! Aquí está el JSON solicitado:\n{"decision": "reject", "confidence": "alta"}\nEspero te sirva!'
    assert ce.repair_and_parse_json(noisy) == {"decision": "reject", "confidence": "alta"}
    # Caso D: Coma final rota
    trailing_comma = '{"decision": "proceed", "items": [1, 2, ], }'
    repaired = ce.repair_and_parse_json(trailing_comma)
    assert repaired == {"decision": "proceed", "items": [1, 2]}, f"Fallo reparando comas: {repaired}"
    # Caso E: Texto basura no JSON devuelve None sin excepción
    assert ce.repair_and_parse_json("No es un json valido") is None
    print("  -> OK: Limpiador determinista repara formatos y elimina reintentos por API.")

    print("[TEST 2/5] Verificando Hard Limit configurado a 0.10$...")
    assert ce.AI_DAILY_BUDGET_USD == 0.10, f"AI_DAILY_BUDGET_USD={ce.AI_DAILY_BUDGET_USD}, esperado 0.10"
    print("  -> OK: AI_DAILY_BUDGET_USD fijado en 0.10$.")

    print("[TEST 3/5] Verificando Cortafuegos Hard Limit (BudgetExceededError)...")
    # Crear BD en memoria con coste simulado >= 0.10$
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.row_factory = sqlite3.Row
    ce.init_db(mem_conn)
    # Insertar registros que sumen 0.12$ hoy
    mem_conn.execute("""
        INSERT INTO ai_cost_log (platform, radar_id, model, call_type, tokens_in, tokens_out, usd, created_at)
        VALUES ('TEST', 'radar1', 'gemini-3.5-flash-lite', 'vision', 1000, 200, 0.12, CURRENT_TIMESTAMP)
    """)
    mem_conn.commit()

    cost_today = ce.get_today_ai_cost(mem_conn)
    assert cost_today >= 0.10, f"cost_today={cost_today}"

    status, ratio = ce.check_ai_budget_status(mem_conn)
    assert status == "PAUSED", f"status={status}, esperado PAUSED"
    assert ratio >= 1.0, f"ratio={ratio}"

    # Llamar a ai_gateway_call con enforce_budget=True debe lanzar BudgetExceededError
    threw_budget_error = False
    try:
        ce.ai_gateway_call(
            prompt="test",
            conn=mem_conn,
            call_type="vision",
            enforce_budget=True
        )
    except ce.BudgetExceededError:
        threw_budget_error = True
    assert threw_budget_error is True, "ai_gateway_call NO lanzó BudgetExceededError cuando cost_today >= 0.10$"
    print("  -> OK: BudgetExceededError bloquea llamadas al alcanzar el hard limit.")

    print("[TEST 4/5] Verificando Fail-Open seguro en prescreen_item_text bajo cortafuegos...")
    # Cuando el presupuesto está excedido, prescreen debe devolver fail-open ("proceed") sin crash
    t = {"id": "switch_std_lite", "name": "Switch Lite", "max_price": 95}
    decision, reason, conf, shadow = ce.prescreen_item_text(
        "WALLAPOP",
        "Nintendo Switch Lite Turquesa",
        "Consola en perfecto estado con cargador",
        60.0,
        65.0,
        t,
        conn=mem_conn
    )
    assert decision == "proceed", f"prescreen bajo cortafuegos debe fail-open ('proceed'), obtuvo '{decision}'"
    print("  -> OK: Prescreen fail-open opera limpiamente sin romper el flujo.")

    print("[TEST 5/5] Verificando que bot.py maneja BudgetExceededError sin caer...")
    def make_mock_db():
        m_conn = sqlite3.connect(":memory:")
        m_conn.row_factory = sqlite3.Row
        ce.init_db(m_conn)
        m_conn.execute("""
            INSERT INTO ai_cost_log (platform, radar_id, model, call_type, tokens_in, tokens_out, usd, created_at)
            VALUES ('TEST', 'radar1', 'gemini-3.5-flash-lite', 'vision', 1000, 200, 0.12, CURRENT_TIMESTAMP)
        """)
        m_conn.commit()
        return m_conn

    old_get_db = bot.get_db
    bot.get_db = make_mock_db
    try:
        res_desc = bot.call_ai_copywriter("PS5 Lector", "Texto base", "2 mandos", "Muy buen estado")
        assert res_desc is None, "bot.call_ai_copywriter debe abortar limpiamente en cortafuegos"
        res_app = bot.call_ai_appraiser("Switch OLED", 150, [])
        assert res_app is None, "bot.call_ai_appraiser debe abortar limpiamente en cortafuegos"
    finally:
        bot.get_db = old_get_db
    print("  -> OK: bot.py protegido por el cortafuegos central.")

    print("\nTODOS LOS TESTS DEL PUNTO 3 COMPLETADOS CON ÉXITO.")

if __name__ == "__main__":
    test_punto3_complete()
