#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de Verificación Exhaustivo - Punto 2 de CORRECION_FALLOS.md
Degradación Estricta de Modelos (Model Routing a Flash-Lite / Mini)
"""

import os
import sys

# Agregar path raíz para imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core_engine as ce
import bot

def test_model_routing_strict():
    print("[TEST 1/5] Verificando constantes de modelos por defecto...")
    assert "lite" in ce.AI_MODEL.lower() or "mini" in ce.AI_MODEL.lower(), f"ce.AI_MODEL={ce.AI_MODEL} no es lite/mini"
    assert "lite" in ce.AI_PRESCREEN_MODEL.lower() or "mini" in ce.AI_PRESCREEN_MODEL.lower(), f"ce.AI_PRESCREEN_MODEL={ce.AI_PRESCREEN_MODEL} no es lite/mini"
    assert "3.8" not in ce.AI_MODEL, "ce.AI_MODEL contiene 3.8"
    assert "3.8" not in ce.AI_PRESCREEN_MODEL, "ce.AI_PRESCREEN_MODEL contiene 3.8"
    print("  -> OK: Constantes en core_engine configuradas a Flash-Lite / Mini.")

    print("[TEST 2/5] Verificando enrutamiento de anuncios unitarios de ticket alto (PS5 260€)...")
    target_ps5 = {"id": "ps5_consoles_gta6", "max_price": 290}
    # Anuncio unitario de 260€: NO debe escalar a modelo pro ni a 3.8
    m_ps5_unit = ce.select_multimodal_model(
        {"title": "PlayStation 5 Chasis C con mando DualSense", "price": 260.0},
        target_ps5,
        {}
    )
    print(f"  Modelo seleccionado para PS5 unitaria 260€: {m_ps5_unit} (OpenRouter: {m_ps5_unit.openrouter_model})")
    assert "lite" in str(m_ps5_unit).lower(), f"PS5 unitaria debe usar flash-lite, obtuvo {m_ps5_unit}"
    assert "3.8" not in str(m_ps5_unit), f"Prohibido 3.8 en PS5 unitaria: {m_ps5_unit}"
    assert "lite" in str(m_ps5_unit.openrouter_model).lower(), f"OpenRouter debe ser lite: {m_ps5_unit.openrouter_model}"
    print("  -> OK: Anuncio unitario de 260€ se mantiene en Flash-Lite.")

    print("[TEST 3/5] Verificando anuncio de ticket bajo (Switch Lite 45€)...")
    target_switch = {"id": "switch_std_lite", "max_price": 95}
    m_switch = ce.select_multimodal_model(
        {"title": "Nintendo Switch Lite Turquesa consola", "price": 45.0},
        target_switch,
        {}
    )
    print(f"  Modelo seleccionado para Switch 45€: {m_switch}")
    assert "lite" in str(m_switch).lower(), f"Switch Lite debe usar flash-lite, obtuvo {m_switch}"
    assert "3.8" not in str(m_switch), f"Prohibido 3.8 en Switch: {m_switch}"
    print("  -> OK: Switch 45€ usa Flash-Lite.")

    print("[TEST 4/5] Verificando lote complejo (Lote PS5 + 6 juegos + 2 mandos 260€)...")
    m_lot = ce.select_multimodal_model(
        {"title": "Lote PS5 Disco + 6 juegos + 2 mandos", "price": 260.0},
        target_ps5,
        {}
    )
    print(f"  Modelo seleccionado para Lote complejo 260€: {m_lot}")
    assert "3.8" not in str(m_lot), f"Prohibido 3.8 incluso en lote complejo: {m_lot}"
    assert str(m_lot) == "gemini-3.5-flash", f"Lote complejo debe usar 3.5-flash, obtuvo {m_lot}"
    print("  -> OK: Lote complejo usa gemini-3.5-flash (nunca 3.8).")

    print("[TEST 5/5] Verificando modelos en bot.py...")
    assert "lite" in bot.AI_MODEL.lower() or "mini" in bot.AI_MODEL.lower(), f"bot.AI_MODEL={bot.AI_MODEL} no es lite/mini"
    assert "3.8" not in bot.AI_MODEL, "bot.AI_MODEL contiene 3.8"
    print("  -> OK: bot.py usa Flash-Lite.")

    print("\nTODOS LOS TESTS DEL PUNTO 2 COMPLETADOS CON ÉXITO.")

if __name__ == "__main__":
    test_model_routing_strict()

