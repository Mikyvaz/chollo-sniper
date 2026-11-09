# -*- coding: utf-8 -*-
"""
Test de verificación del BLOQUE 2 (Plan v5.2):
4 perfiles simulados y 1 real:
1. Wallapop rating=96, reviews=14 -> debe normalizar a 4.8⭐ y dar 0 flags de vendedor.
2. Cuenta nueva reviews=0, joined_days=3, price=90€ -> debe dar risk >= 5 y bloquear Flash.
3. Vendedor reviews=0, rating=None -> no debe disparar SELLER_LOW_RATING, solo SELLER_ZERO_REVIEWS.
4. CashConverters -> 0 riesgo y vendedor oficial verificado.
5. enrich_seller_profile con caché en memoria TTL.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fraud_signals import evaluate_seller_risk, evaluate_fraud_risk, enrich_seller_profile, _SELLER_PROFILE_CACHE


def test_profile_1_wallapop_trusted_normalized():
    print("[1/5] Probando Caso 1: Wallapop rating=96 (escala 0-100), reviews=14...")
    item = {
        "platform": "WALLAPOP",
        "price": 85.0,
        "seller": {
            "id": "user_w14",
            "name": "Vendedor Pro Legítimo",
            "rating": 96.0,
            "reviews_count": 14,
            "joined_days": 180
        }
    }
    enriched = enrich_seller_profile(item)
    assert enriched["seller_rating"] == 4.8, f"Rating no normalizado a 4.8: {enriched['seller_rating']}"
    assert enriched["seller_reviews_count"] == 14

    eval_res = evaluate_seller_risk(item, price=85.0, platform="WALLAPOP")
    assert eval_res["seller_rating"] == 4.8
    assert len(eval_res["hits"]) == 0, f"No debería haber flags de vendedor, obtenidos: {eval_res['hits']}"
    assert eval_res["is_trusted"] is True
    assert "4.8⭐" in eval_res["seller_trust_badge"]
    assert eval_res["block_flash"] is False

    fraud_res = evaluate_fraud_risk(
        title="Pokemon Edicion Blanca 2 DS",
        description="Juego original completo con caja y manual en perfecto estado.",
        price=85.0,
        seller=item["seller"],
        platform="WALLAPOP"
    )
    assert fraud_res["risk"] == 0, f"Riesgo esperado 0, obtenido {fraud_res['risk']}"
    assert fraud_res["is_fraud"] is False
    print("  -> OK: Wallapop normalizado a 4.8⭐, 0 flags de riesgo, confianza alta.")


def test_profile_2_scam_new_account():
    print("[2/5] Probando Caso 2: Cuenta nueva reviews=0, joined_days=3, price=90€...")
    item = {
        "platform": "WALLAPOP",
        "price": 90.0,
        "seller": {
            "id": "scam_new_acc",
            "name": "Novato Fantasma",
            "rating": None,
            "reviews_count": 0,
            "joined_days": 3
        }
    }
    eval_res = evaluate_seller_risk(item, price=90.0, platform="WALLAPOP")
    codes = [h["code"] for h in eval_res["hits"]]
    assert "SELLER_NEW_ACCOUNT_ZERO_REVIEWS" in codes, f"Falta SELLER_NEW_ACCOUNT_ZERO_REVIEWS: {codes}"
    assert "HIGH_TICKET_ZERO_REP_SCAM_RISK" in codes, f"Falta HIGH_TICKET_ZERO_REP_SCAM_RISK: {codes}"
    assert eval_res["block_flash"] is True
    assert eval_res["risk_delta"] >= 5, f"Riesgo esperado >= 5, obtenido {eval_res['risk_delta']}"

    fraud_res = evaluate_fraud_risk(
        title="Nintendo Switch OLED Blanca",
        description="Vendo consola como nueva, funciona perfecta.",
        price=90.0,
        seller=item["seller"],
        platform="WALLAPOP"
    )
    assert fraud_res["risk"] >= 5, f"Riesgo global esperado >= 5, obtenido {fraud_res['risk']}"
    assert fraud_res["is_fraud"] is True
    print("  -> OK: Cuenta nueva con 0 valoraciones detectada con risk >= 5 y Flash bloqueado.")


def test_profile_3_zero_reviews_not_low_rating():
    print("[3/5] Probando Caso 3: Vendedor reviews=0, rating=None...")
    item = {
        "platform": "VINTED",
        "price": 25.0,
        "seller": {
            "id": "user_zero_rev",
            "name": "Usuario Sin Ventas",
            "rating": None,
            "reviews_count": 0,
            "joined_days": None
        }
    }
    eval_res = evaluate_seller_risk(item, price=25.0, platform="VINTED")
    codes = [h["code"] for h in eval_res["hits"]]
    assert "SELLER_ZERO_REVIEWS" in codes, f"Debe tener SELLER_ZERO_REVIEWS: {codes}"
    assert "SELLER_LOW_RATING" not in codes, f"NO debe disparar SELLER_LOW_RATING: {codes}"
    assert eval_res["block_flash"] is True
    print("  -> OK: Cuenta con 0 reviews recibe SELLER_ZERO_REVIEWS sin falso positivo de SELLER_LOW_RATING.")


def test_profile_4_cash_converters():
    print("[4/5] Probando Caso 4: Cash Converters tienda oficial...")
    item = {
        "platform": "CASHCONVERTERS",
        "price": 140.0,
        "seller": {
            "id": "cash_converters_official",
            "name": "Cash Converters"
        }
    }
    enriched = enrich_seller_profile(item)
    assert enriched["seller_rating"] == 5.0
    assert enriched["seller_reviews_count"] == 999
    assert enriched["seller_verified"] is True

    eval_res = evaluate_seller_risk(item, price=140.0, platform="CASHCONVERTERS")
    assert eval_res["risk_delta"] == 0
    assert len(eval_res["hits"]) == 0
    assert eval_res["is_trusted"] is True
    assert eval_res["block_flash"] is False

    fraud_res = evaluate_fraud_risk(
        title="PlayStation 5 Chasis C",
        description="Consola en buen estado, 3 anos de garantia.",
        price=140.0,
        seller=item["seller"],
        platform="CASHCONVERTERS"
    )
    assert fraud_res["risk"] == 0
    assert fraud_res["is_fraud"] is False
    print("  -> OK: Cash Converters verificado con riesgo 0 absoluto.")


def test_cache_enrich_seller_profile():
    print("[5/5] Probando caché TTL en memoria de enrich_seller_profile...")
    item = {
        "platform": "WALLAPOP",
        "price": 50.0,
        "seller": {
            "id": "cache_test_user_99",
            "seller_reviews_count": 7,
            "seller_rating": 4.9
        }
    }
    enrich_seller_profile(item)
    key = ("WALLAPOP", "cache_test_user_99")
    assert key in _SELLER_PROFILE_CACHE, "El vendedor no fue guardado en _SELLER_PROFILE_CACHE"
    cached_entry = _SELLER_PROFILE_CACHE[key]
    assert cached_entry[1]["seller_reviews_count"] == 7
    print("  -> OK: Perfil cacheado correctamente en memoria.")


if __name__ == "__main__":
    test_profile_1_wallapop_trusted_normalized()
    test_profile_2_scam_new_account()
    test_profile_3_zero_reviews_not_low_rating()
    test_profile_4_cash_converters()
    test_cache_enrich_seller_profile()
    print("\n✅ TODOS LOS TESTS DEL BLOQUE 2 HAN PASADO CON ÉXITO.")

