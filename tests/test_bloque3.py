# -*- coding: utf-8 -*-
"""
Test de verificación del BLOQUE 3 (Plan v5.2):
Sistema de Clasificación de 3 Niveles de Chollo (CeX vs Mercado P2P):
- Caso 1: cost=50€, cex_cash=72€ -> NIVEL 1 (Arbitraje Puro CeX)
- Caso 2: cost=100€, cex_cash=82€, market_val=155€ -> NIVEL 2 (Cubre 82% CeX + 42.6€ beneficio P2P)
- Caso 3: cost=80€, cex_cash=0€, market_val=145€ -> NIVEL 3 (Oportunidad Pura P2P)
- Caso 4: Integración con evaluate_deal_math_and_score
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import classify_deal_tier, evaluate_deal_math_and_score


def test_case_1_pure_cex_arbitrage():
    print("[1/4] Probando Caso 1: cost=50€, cex_cash=72€...")
    res = classify_deal_tier(
        total_buy_cost=50.0,
        cex_cash_total=72.0,
        cex_voucher_total=90.0,
        estimated_market_value=75.0,
        fraud_risk_score=0,
        buy_confidence=10
    )
    assert res["is_deal"] is True, "Caso 1 debe ser deal"
    assert res["deal_tier"] == 1, f"Esperado deal_tier=1, obtenido {res['deal_tier']}"
    assert res["tier_code"] == "NIVEL_1"
    assert res["cex_cash_net"] == 22.0
    assert "NIVEL 1: ARBITRAJE CeX" in res["tier_label"]
    print("  -> OK: Identificado como NIVEL 1 (Arbitraje Puro CeX, +22€ cash directo).")


def test_case_2_cushion_cex_deal():
    print("[2/4] Probando Caso 2: cost=100€, cex_cash=82€, market_val=155€...")
    res = classify_deal_tier(
        total_buy_cost=100.0,
        cex_cash_total=82.0,
        cex_voucher_total=105.0,
        estimated_market_value=155.0,
        fraud_risk_score=1,
        buy_confidence=9
    )
    assert res["is_deal"] is True, "Caso 2 debe ser deal"
    assert res["deal_tier"] == 2, f"Esperado deal_tier=2, obtenido {res['deal_tier']}"
    assert res["tier_code"] == "NIVEL_2"
    assert res["cex_coverage_pct"] == 82.0, f"Cobertura esperada 82.0%, obtenida {res['cex_coverage_pct']}%"
    assert res["p2p_net_profit"] == 42.6, f"Beneficio P2P esperado 42.6€, obtenido {res['p2p_net_profit']}€"
    assert "NIVEL 2" in res["tier_label"]
    assert "Cubre 82%" in res["tier_label"]
    print("  -> OK: Identificado como NIVEL 2 (Cubre 82% CeX + 42.6€ beneficio P2P).")


def test_case_3_p2p_market_opportunity():
    print("[3/4] Probando Caso 3: cost=80€, cex_cash=0€, market_val=145€...")
    res = classify_deal_tier(
        total_buy_cost=80.0,
        cex_cash_total=0.0,
        cex_voucher_total=0.0,
        estimated_market_value=145.0,
        fraud_risk_score=1,
        buy_confidence=8
    )
    assert res["is_deal"] is True, "Caso 3 debe ser deal"
    assert res["deal_tier"] == 3, f"Esperado deal_tier=3, obtenido {res['deal_tier']}"
    assert res["tier_code"] == "NIVEL_3"
    assert res["p2p_net_profit"] == 53.4, f"Beneficio esperado 53.4€, obtenido {res['p2p_net_profit']}€"
    assert res["p2p_roi_pct"] >= 45.0, f"ROI esperado >= 45%, obtenido {res['p2p_roi_pct']}%"
    assert "NIVEL 3: REVENTA P2P" in res["tier_label"]
    print("  -> OK: Identificado como NIVEL 3 (Oportunidad Pura P2P, +53.4€ limpio).")


def test_integration_evaluate_deal_math_and_score():
    print("[4/4] Probando integración en evaluate_deal_math_and_score...")
    target = {
        "id": "switch_oled_v1_lite",
        "name": "Nintendo Switch OLED",
        "mode": "resale",
        "max_price": 200,
        "direct_buy_max_price": 180,
        "submodel_price_caps": []
    }
    ai_data = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "visual_confidence": "alta",
        "identified_model": "Nintendo Switch OLED Blanca",
        "estimated_resale_price": 155
    }
    cex_data = {
        "cash": 82.0,
        "voucher": 105.0,
        "sell": 180.0,
        "box_name": "Nintendo Switch OLED White"
    }

    is_deal, score, opt_p, net_p, penalty, tag = evaluate_deal_math_and_score(
        price=95.0,
        landed_cost=100.0,
        target=target,
        ai_data=ai_data,
        cex_data=cex_data
    )

    assert is_deal is True, "evaluate_deal_math_and_score debería aprobar el chollo Nivel 2"
    assert score == 9, f"Puntuación esperada 9 para Nivel 2, obtenida {score}"
    assert ai_data["deal_tier"] == 2
    assert "NIVEL 2" in ai_data["deal_tier_label"]
    assert tag == "OK_NIVEL_2"
    print("  -> OK: evaluate_deal_math_and_score clasifica y devuelve OK_NIVEL_2 con nota 9/10.")


if __name__ == "__main__":
    test_case_1_pure_cex_arbitrage()
    test_case_2_cushion_cex_deal()
    test_case_3_p2p_market_opportunity()
    test_integration_evaluate_deal_math_and_score()
    print("\n✅ TODOS LOS TESTS DEL BLOQUE 3 HAN PASADO CON ÉXITO.")

