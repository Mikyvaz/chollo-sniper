#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de Verificación Exhaustivo - Punto 4 de CORRECION_FALLOS.md
Poda Radical del Contexto (Context Pruning) y Saneamiento de Entradas (Input Sanitization)
"""

import os
import sys
import sqlite3
import unittest.mock as mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core_engine as ce
import bot

def test_punto4_pruning_and_sanitization():
    print("[TEST 1/4] Verificando poda y sanitización de texto en Puerta 1 (prescreen_item_text)...")
    massive_title = "Nintendo Switch Lite " + ("A" * 500)
    massive_desc = "Consola en perfecto estado " + ("X" * 5000)
    target = {
        "id": "switch_std_lite",
        "name": "Switch Lite " + ("B" * 200),
        "ai_valuation_guide": "Guia de prueba " + ("G" * 2000),
        "max_price": 95
    }

    captured_prompts = []
    def mock_ai_call(**kwargs):
        captured_prompts.append(kwargs.get("prompt", ""))
        return {"decision": "proceed", "reject_reason": "", "confidence": "alta"}

    with mock.patch("core_engine.ai_gateway_call", side_effect=mock_ai_call):
        ce.prescreen_item_text("WALLAPOP", massive_title, massive_desc, 50.0, 55.0, target, conn=None)

    assert len(captured_prompts) == 1, "Debe haber capturado 1 prompt"
    p = captured_prompts[0]
    assert len(p) < 1300, f"Prompt de prescreen debe tener < 1300 caracteres tras poda, tiene {len(p)}"
    assert p.count("X") <= 250, f"Spam X debe estar acotado a <= 250 caracteres, tiene {p.count('X')}"
    assert "Cero texto conversacional" in p, "Debe incluir restricción contra texto conversacional"
    print(f"  -> OK: Prescreen podado con éxito ({len(p)} caracteres vs >7.000 sin podar).")

    print("[TEST 2/4] Verificando poda y sanitización en Puerta 2 (analyze_and_generate_kit)...")
    captured_vision_prompts = []
    def mock_vision_call(**kwargs):
        captured_vision_prompts.append(kwargs.get("prompt", ""))
        return {"is_valid_deal": True, "deal_score": 9, "identified_model": "Switch Lite"}

    with mock.patch("core_engine.ai_gateway_call", side_effect=mock_vision_call):
        ce.analyze_and_generate_kit(
            "WALLAPOP",
            massive_title,
            massive_desc,
            50.0,
            55.0,
            False,
            target,
            conn=None,
            extra_meta="Z" * 500
        )

    assert len(captured_vision_prompts) == 1, "Debe haber capturado 1 prompt de visión"
    pv = captured_vision_prompts[0]
    # Comprobar que no incluye los miles de caracteres basura
    assert len(pv) < 2500, f"Prompt de visión debe tener < 2500 caracteres tras poda, tiene {len(pv)}"
    assert pv.count("X") <= 400, f"Spam X debe estar acotado a <= 400 caracteres en visión, tiene {pv.count('X')}"
    assert "Cero texto conversacional" in pv, "Restricción de texto conversacional activa en visión"
    print(f"  -> OK: Visión podada con éxito ({len(pv)} caracteres vs >7.000 sin podar).")

    print("[TEST 3/4] Verificando sanitización y poda en bot.py (call_ai_copywriter)...")
    captured_bot_prompts = []
    def mock_bot_call(**kwargs):
        captured_bot_prompts.append(kwargs.get("prompt", ""))
        return {"seo_title": "PS5 Impecable", "description": "Vendo PS5"}

    with mock.patch("bot.ai_gateway_call", side_effect=mock_bot_call):
        bot.call_ai_copywriter(
            "PS5 Lector " + ("P" * 300),
            "Texto base " + ("B" * 2000),
            "Extras " + ("E" * 1000),
            "Notas " + ("N" * 1000),
            "250€ " + ("D" * 200)
        )

    assert len(captured_bot_prompts) == 1
    p_bot = captured_bot_prompts[0]
    assert p_bot.count("B") < 350, f"Texto base debe estar acotado a < 350, tiene {p_bot.count('B')}"
    assert p_bot.count("E") < 250, f"Extras debe estar acotado a < 250, tiene {p_bot.count('E')}"
    assert "Cero texto conversacional" in p_bot
    print("  -> OK: bot.call_ai_copywriter sanitiza y poda inputs.")

    print("[TEST 4/4] Verificando poda de catálogo CeX en bot.py (call_ai_appraiser)...")
    captured_app_prompts = []
    many_variants = [{"box_name": f"Variant {i} " + ("V" * 100), "cash": 50 + i, "sell": 100 + i} for i in range(25)]

    def mock_app_call(**kwargs):
        captured_app_prompts.append(kwargs.get("prompt", ""))
        return {"clean_model_name": "Switch", "fast_resale_48h_eur": 120}

    with mock.patch("bot.ai_gateway_call", side_effect=mock_app_call):
        bot.call_ai_appraiser("Switch " + ("S" * 300), 70, many_variants)

    assert len(captured_app_prompts) == 1
    p_app = captured_app_prompts[0]
    # Solo debe contener 3 variantes máximo, no las 25
    assert p_app.count("Variant") == 3, f"Solo debe incluir top 3 variantes, tiene {p_app.count('Variant')}"
    assert "Cero texto conversacional" in p_app
    print(f"  -> OK: bot.call_ai_appraiser limita a top 3 variantes ({len(p_app)} caracteres).")

    print("\nTODOS LOS TESTS DEL PUNTO 4 COMPLETADOS CON ÉXITO.")

if __name__ == "__main__":
    test_punto4_pruning_and_sanitization()
