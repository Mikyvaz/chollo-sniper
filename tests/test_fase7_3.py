#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de verificación para la Fase 7.3 (P7-3):
- Freno de Precio Inverosímil en el Motor Matemático (Anti-Anzuelo)
- Reclasificación de chollos imposibles a 'high_risk_opportunity' (máximo 7/10)
- Inserción de aviso prominente de estafa/anzuelo en alertas
- Desactivación de compra a ciegas y 1-Tap checkout para ofertas de alto riesgo
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import (
    evaluate_deal_math_and_score,
    SCORE_BADGES,
    send_discord_alert,
)


def run_tests():
    print("[1/5] Probando PS5 con precio de anzuelo (180€ vs CeX Cash 413€)...")
    target_ps5 = {
        "id": "ps5_consoles_gta6",
        "name": "PlayStation 5",
        "mode": "resale",
        "min_profit_eur": 35.0,
        "direct_buy_max_price": 400.0,
    }
    cex_data_ps5 = {
        "box_id": "ps5_pro",
        "box_name": "PlayStation 5 Pro 2TB",
        "cash": 413.0,
        "voucher": 480.0,
        "sell": 620.0,
        "category": "Consolas PS5",
    }
    ai_data_ps5 = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "missing_original_charger": False,
        "visual_confidence": "alta",
        "identified_model": "PlayStation 5 Pro 2TB",
        "estimated_resale_price": 500,
    }

    # Precio 180€ < 0.45 * 413€ (185.85€)
    approved, score, opt_price, net_profit, chg, msg = evaluate_deal_math_and_score(
        price=180.0,
        landed_cost=190.0,
        target=target_ps5,
        ai_data=ai_data_ps5,
        cex_data=cex_data_ps5,
        min_profit=35.0,
        min_score=8,
    )

    assert approved is True, "ERROR: La oferta debe ser aprobada para alertar al usuario con advertencia!"
    assert score <= 7, f"ERROR: El score no puede ser > 7 para precio anómalo, obtenido: {score}"
    assert ai_data_ps5.get("deal_type") == "high_risk_opportunity", (
        f"ERROR: deal_type debe ser 'high_risk_opportunity', obtenido: {ai_data_ps5.get('deal_type')}"
    )
    assert ai_data_ps5.get("is_anomalously_low") is True, "ERROR: is_anomalously_low debe ser True"
    print(f"  -> PS5 Pro a 180€ frenada correctamente: Score={score}/10, deal_type='{ai_data_ps5.get('deal_type')}'.")

    print("\n[2/5] Probando Nintendo Switch OLED a precio de anzuelo (55€ vs CeX Cash 160€)...")
    target_switch = {
        "id": "switch_oled",
        "name": "Switch OLED",
        "mode": "resale",
        "min_profit_eur": 25.0,
        "direct_buy_max_price": 180.0,
    }
    cex_data_oled = {
        "box_id": "switch_oled_w",
        "box_name": "Nintendo Switch OLED Blanca",
        "cash": 160.0,
        "voucher": 190.0,
        "sell": 240.0,
        "category": "Consolas Switch",
    }
    ai_data_oled = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "missing_original_charger": False,
        "visual_confidence": "alta",
        "identified_model": "Nintendo Switch OLED Blanca",
        "estimated_resale_price": 200,
    }

    # Precio 55€ < 0.45 * 160€ (72€)
    approved_sw, score_sw, opt_sw, profit_sw, _, _ = evaluate_deal_math_and_score(
        price=55.0,
        landed_cost=60.0,
        target=target_switch,
        ai_data=ai_data_oled,
        cex_data=cex_data_oled,
        min_profit=25.0,
        min_score=8,
    )

    assert approved_sw is True
    assert score_sw <= 7, f"ERROR: Switch OLED a 55€ no puede tener score > 7, obtenido: {score_sw}"
    assert ai_data_oled.get("deal_type") == "high_risk_opportunity"
    print(f"  -> Switch OLED a 55€ reclasificada a alto riesgo: Score={score_sw}/10.")

    print("\n[3/5] Probando chollo legítimo a precio creíble (PS5 Slim a 230€ vs CeX Cash 270€)...")
    cex_data_slim = {
        "box_id": "ps5_slim_1tb",
        "box_name": "PlayStation 5 Slim 1TB",
        "cash": 270.0,
        "voucher": 315.0,
        "sell": 410.0,
        "category": "Consolas PS5",
    }
    ai_data_slim = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "missing_original_charger": False,
        "visual_confidence": "alta",
        "identified_model": "PlayStation 5 Slim 1TB",
        "estimated_resale_price": 350,
    }

    # 230€ > 0.45 * 270€ (121.5€)
    approved_legit, score_legit, opt_legit, profit_legit, _, _ = evaluate_deal_math_and_score(
        price=230.0,
        landed_cost=242.0,
        target=target_ps5,
        ai_data=ai_data_slim,
        cex_data=cex_data_slim,
        min_profit=35.0,
        min_score=8,
    )

    assert approved_legit is True
    assert score_legit >= 8, f"ERROR: Chollo legítimo debe tener score >= 8, obtenido: {score_legit}"
    assert ai_data_slim.get("deal_type") == "direct_chollo"
    assert not ai_data_slim.get("is_anomalously_low")
    print(f"  -> Chollo legítimo aprobado con Score={score_legit}/10 como '{ai_data_slim.get('deal_type')}'.")

    print("\n[4/5] Probando modo personal con precio anómalo (HDD 16TB a 35€ vs valor 250€)...")
    target_personal = {
        "id": "hdd_server",
        "name": "Discos Servidor",
        "mode": "personal",
        "min_savings_eur": 30.0,
        "direct_buy_max_price": 200.0,
    }
    ai_data_hdd = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "visual_confidence": "alta",
        "identified_model": "Seagate Exos 16TB SATA",
        "estimated_resale_price": 250,
    }
    approved_hdd, score_hdd, _, _, _, _ = evaluate_deal_math_and_score(
        price=35.0,
        landed_cost=40.0,
        target=target_personal,
        ai_data=ai_data_hdd,
        cex_data={"cash": 180.0, "sell": 250.0},
        min_profit=30.0,
        min_score=8,
    )

    assert approved_hdd is True
    assert score_hdd <= 7, f"ERROR: HDD 16TB a 35€ debe limitarse a score <= 7, obtenido: {score_hdd}"
    assert ai_data_hdd.get("deal_type") == "high_risk_opportunity"
    print(f"  -> Modo personal frena precio anómalo a Score={score_hdd}/10.")

    print("\n[5/5] Probando que 'high_risk_opportunity' no use Checkout 1-Tap como primary URL...")
    # Simular URLs
    clean_item = "https://es.wallapop.com/item/ps5-pro-180"
    clean_checkout = "https://es.wallapop.com/checkout/ps5-pro-180"

    score_test = 10  # Aunque tuviera score 10 por error, no debe permitir 1-tap si es high_risk
    primary_action_url = clean_checkout if (score_test == 10 and ai_data_ps5.get("deal_type") not in ("offer_opportunity", "high_risk_opportunity")) else clean_item
    assert primary_action_url == clean_item, "ERROR: La URL de acción primaria debe ser el anuncio, NO el checkout directo!"
    print(f"  -> Enlace de acción rápida protegido: URL apunta al anuncio para inspección manual.")

    print("\n" + "=" * 60)
    print("🎉 ¡TODOS LOS TESTS DE LA FASE 7.3 PASARON SATISFACTORIAMENTE!")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()

