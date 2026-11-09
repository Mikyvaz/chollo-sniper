#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de verificación para la Fase 3 (P2-4):
- Contrato estándar del vendedor: {name, id, age_days, listings_count, rating, sales}
- Evaluación de riesgo de vendedor (evaluate_seller_risk)
- Tiendas oficiales contrastadas (Cash Converters)
- Cuentas recién creadas (<48h y <7 días)
- Vendedores sin valoraciones en productos de alto valor
- Reputación baja y vendedores masivos
- Bonificación para vendedores contrastados de alta reputación (is_trusted)
- Integración completa con evaluate_fraud_risk y passes_filters
"""

import os
import sys
import sqlite3

# Añadir raíz al sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import init_db, passes_filters
from fraud_signals import (
    evaluate_seller_risk,
    evaluate_fraud_risk,
    log_fraud_event
)

def run_tests():
    conn = init_db()
    c = conn.cursor()

    print("[1/6] Verificando contrato canónico de seller y evaluación base...")
    # Contrato con los 6 campos estándar
    seller_dummy = {
        "name": "usuario_test",
        "id": "usr_123",
        "age_days": 180,
        "listings_count": 5,
        "rating": 4.8,
        "sales": 25
    }
    s_eval = evaluate_seller_risk(seller_dummy, price=80.0)
    assert s_eval["risk_delta"] == 0, f"Vendedor normal no debe tener penalización: {s_eval}"
    assert s_eval["is_trusted"] is True, f"Vendedor con 25 ventas y 4.8 rating debe ser is_trusted: {s_eval}"
    print("  -> Contrato base y vendedor contrastado evaluados correctamente.")

    print("[2/6] Probando tiendas oficiales verificadas (Cash Converters)...")
    seller_cc = {
        "name": "Cash Converters España",
        "id": "cash_converters_official",
        "age_days": 3650,
        "listings_count": 9999,
        "rating": 5.0,
        "sales": 9999
    }
    cc_eval = evaluate_seller_risk(seller_cc, price=120.0)
    assert cc_eval["risk_delta"] == 0 and cc_eval["is_trusted"] is True
    assert len(cc_eval["hits"]) == 0
    print("  -> Tienda oficial (Cash Converters) verificada con riesgo 0 y confianza plena.")

    print("[3/6] Probando detección de cuentas recién creadas (<48h y <7 días)...")
    # Caso cuenta creada hace menos de 48h
    s_today = {"name": "scammer_fresh", "id": "u999", "age_days": 0.5, "sales": 0, "rating": None, "listings_count": 1}
    eval_today = evaluate_seller_risk(s_today, price=120.0)
    codes_today = [h["code"] for h in eval_today["hits"]]
    assert "SELLER_CREATED_TODAY" in codes_today, f"Debe detectar cuenta <48h: {codes_today}"
    assert "SELLER_ZERO_REVIEWS" in codes_today, f"Debe detectar 0 reviews en producto caro: {codes_today}"
    assert eval_today["risk_delta"] >= 4, f"Riesgo combinado debe ser >= 4 (actual: {eval_today['risk_delta']})"

    # Caso cuenta novata (5 días)
    s_week = {"name": "novato", "id": "u888", "age_days": 5.0, "sales": 1, "rating": 5.0, "listings_count": 2}
    eval_week = evaluate_seller_risk(s_week, price=50.0)
    codes_week = [h["code"] for h in eval_week["hits"]]
    assert "SELLER_BRAND_NEW" in codes_week, f"Debe detectar cuenta novata: {codes_week}"
    print("  -> Filtros de antigüedad de cuenta y 0 ventas verificados.")

    print("[4/6] Probando reputación baja y reventa masiva...")
    # Reputación baja en escala 5.0 (ej. Vinted/Wallapop: 2.8/5)
    s_low5 = {"name": "mala_fama", "id": "u777", "age_days": 100, "sales": 10, "rating": 2.8, "listings_count": 5}
    eval_low5 = evaluate_seller_risk(s_low5, price=50.0)
    assert any(h["code"] == "SELLER_LOW_RATING" for h in eval_low5["hits"])

    # Reputación baja en escala 100% (eBay: 74%)
    s_low_ebay = {"name": "ebay_scam", "id": "ebay_u1", "age_days": None, "sales": 15, "rating": 74.0, "listings_count": 10}
    eval_low_ebay = evaluate_seller_risk(s_low_ebay, price=80.0)
    assert any(h["code"] == "SELLER_LOW_RATING" for h in eval_low_ebay["hits"])

    # Reventa masiva (>40 anuncios)
    s_massive = {"name": "reseller_pro", "id": "u666", "age_days": 200, "sales": 10, "rating": 4.5, "listings_count": 65}
    eval_massive = evaluate_seller_risk(s_massive, price=60.0)
    assert any(h["code"] == "SELLER_MASSIVE_LISTINGS" for h in eval_massive["hits"])
    print("  -> Reputación baja (0-5 y %) y reventa masiva (>40 ads) verificadas.")

    print("[5/6] Probando integración con evaluate_fraud_risk...")
    # Caso 1: Precio sospechoso (60€ con CeX 280€ -> peso 2) + Cuenta creada hoy (<48h -> peso 2) = Riesgo 4 (Fraude seguro)
    eval_fraud = evaluate_fraud_risk(
        title="Nintendo Switch OLED",
        description="Consola impecable con todo",
        price=60.0,
        cex_cash=280.0,
        seller=s_today
    )
    assert eval_fraud["risk"] >= 4, f"Riesgo combinado esperado >= 4, obtenido: {eval_fraud['risk']}"
    assert eval_fraud["is_fraud"] is True, "Debe marcarse como fraude directo"
    assert any(h["code"] == "IMPOSSIBLE_PRICE" for h in eval_fraud["hits"])
    assert any(h["code"] == "SELLER_CREATED_TODAY" for h in eval_fraud["hits"])

    # Caso 2: Mismo precio sospechoso pero con vendedor contrastado (is_trusted)
    s_trusted = {"name": "buen_vendedor", "id": "u100", "age_days": 300, "sales": 80, "rating": 4.9, "listings_count": 3}
    eval_trusted = evaluate_fraud_risk(
        title="Nintendo Switch OLED",
        description="Consola impecable con todo",
        price=60.0,
        cex_cash=280.0,
        seller=s_trusted
    )
    # El precio imposible aporta peso 2, pero al ser vendedor de confianza se aplica bonificación (-1) -> riesgo 1 (aviso, no bloqueo)
    assert eval_trusted["is_fraud"] is False, "Vendedor trusted no debe ser bloqueado por fraude seco"
    assert eval_trusted["risk"] == 1
    print("  -> Interacción entre precio imposible y señales de vendedor verificada.")

    print("[6/6] Probando integración con passes_filters en Puerta 0...")
    dummy_target = {"id": "switch_oled", "name": "Switch OLED", "min_price": 50, "max_price": 250, "enabled": True}
    
    # Anuncio con vendedor fraudulento (cuenta <48h + 0 ventas en consola de 120€)
    ok_bad_seller = passes_filters(
        "Nintendo Switch OLED blanca",
        "Vendo consola",
        120.0,
        dummy_target,
        [],
        seller=s_today
    )
    assert ok_bad_seller is False, "passes_filters debe rechazar anuncio con vendedor fraudulento"

    # Anuncio con vendedor normal o contrastado
    ok_good_seller = passes_filters(
        "Nintendo Switch OLED blanca",
        "Consola completa con Joy-Cons y dock oficial",
        160.0,
        dummy_target,
        [],
        seller=s_trusted
    )
    assert ok_good_seller is True, "passes_filters debe aceptar anuncio con vendedor de confianza"
    print("  -> passes_filters filtra correctamente según señales del vendedor.")

    print("\n✅ ¡TODOS LOS TESTS DE LA FASE 3 HAN PASADO CON ÉXITO!")

if __name__ == "__main__":
    run_tests()

