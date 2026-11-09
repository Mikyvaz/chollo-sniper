#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de verificación para la Fase 2 (P2):
- Detección exhaustiva de teléfonos (normales y camuflados)
- Reglas deterministas de contacto y pagos no protegidos
- Detección de fotos reutilizadas mediante dHash (8x8)
- Detección de precio imposible vs suelo CeX
- Auditoría en tabla fraud_log
"""

import os
import sys
import io
import sqlite3
from PIL import Image, ImageDraw

# Añadir raíz al sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import init_db, passes_filters
from fraud_signals import (
    detect_phone_number,
    calculate_dhash,
    hamming_distance,
    check_and_record_image_hash,
    check_impossible_price,
    evaluate_fraud_risk,
    log_fraud_event
)

def run_tests():
    conn = init_db()
    c = conn.cursor()

    print("[1/6] Probando detección de teléfonos (P2-1)...")
    # Caso 1: 9 dígitos contiguos
    ok, num = detect_phone_number("Vendo Switch OLED hablar al 612345678")
    assert ok and "612345678" in num, f"Fallo en 9 dígitos contiguos: {num}"

    # Caso 2: Con prefijo internacional y guiones
    ok, num = detect_phone_number("Contactar por WhatsApp: +34 612-34-56-78")
    assert ok, f"Fallo en prefijo internacional: {num}"

    # Caso 3: Camuflado con espacios
    ok, num = detect_phone_number("Hablame al seis uno dos: 6 1 2 3 4 5 6 7 8")
    assert ok, f"Fallo en camuflado con espacios: {num}"

    # Caso 4: Formato con paréntesis
    ok, num = detect_phone_number("Tlf (612) 345 678")
    assert ok, f"Fallo en formato paréntesis: {num}"

    # Caso 5: Texto normal sin teléfono
    ok, num = detect_phone_number("PS5 Slim 1TB con 2 mandos y caja original")
    assert not ok, f"Falso positivo en texto normal: {num}"
    print("  -> Todos los patrones de teléfono verificados correctamente (sin falsos positivos).")

    print("[2/6] Probando reglas de pagos no protegidos y contacto (P2-2, P2-3)...")
    eval_bizum = evaluate_fraud_risk("PS5", "Vendo barata solo bizum no envio por wallapop", 150)
    assert eval_bizum["is_fraud"] is True, "Bizum debería clasificarse como fraude"
    assert any(h["code"] == "PAYMENT_EXTERNAL" for h in eval_bizum["hits"])

    eval_virement = evaluate_fraud_risk("Switch", "Paiement uniquement via virement bancaire", 100)
    assert eval_virement["is_fraud"] is True, "Virement bancaire debería clasificarse como fraude"

    eval_bonifico = evaluate_fraud_risk("Switch", "Solo bonifico prima di acquistare", 100)
    assert eval_bonifico["is_fraud"] is True, "Bonifico debería clasificarse como fraude"
    print("  -> Detección de pagos externos en ES/FR/IT verificada.")

    print("[3/6] Probando análisis de precio imposible (P2-6)...")
    # PS5 a 60€ con suelo CeX 280€ -> 60 < 0.35 * 280 = 98€ -> IMPOSSIBLE_PRICE
    is_imp, msg = check_impossible_price(60.0, 280.0)
    assert is_imp is True, f"Debería detectar precio imposible: {msg}"

    # PS5 a 210€ con suelo CeX 280€ -> 210 > 98€ -> Normal
    is_imp2, _ = check_impossible_price(210.0, 280.0)
    assert is_imp2 is False, "No debería detectar precio imposible en 210€"
    print("  -> Filtro de precio imposible (<0.35 * CeX) verificado.")

    print("[4/6] Probando huella perceptual dHash 8x8 y fotos reutilizadas (P2-5)...")
    # Crear dos imágenes sintéticas casi idénticas
    img1 = Image.new("RGB", (300, 300), color=(100, 150, 200))
    d1 = ImageDraw.Draw(img1)
    d1.rectangle([50, 50, 250, 250], fill=(255, 0, 0))
    buf1 = io.BytesIO()
    img1.save(buf1, format="JPEG")
    b1 = buf1.getvalue()

    # Segunda imagen con compresión ligeramente distinta
    img2 = img1.copy()
    buf2 = io.BytesIO()
    img2.save(buf2, format="JPEG", quality=80)
    b2 = buf2.getvalue()

    h1 = calculate_dhash(b1)
    h2 = calculate_dhash(b2)
    assert h1 is not None and h2 is not None
    dist = hamming_distance(h1, h2)
    assert dist <= 2, f"Las dos imágenes deberían tener dHash casi idéntico: {h1} vs {h2} (dist: {dist})"

    # Probar detección de foto reutilizada en SQLite
    reused1, _ = check_and_record_image_hash(conn, h1, "WALLAPOP", "anuncio_1001", "switch_oled")
    assert reused1 is False, "La primera inserción no debería ser marcada como reutilizada"

    reused2, info2 = check_and_record_image_hash(conn, h2, "VINTED", "anuncio_2002", "switch_oled")
    assert reused2 is True, "La segunda inserción con distinto item_id DEBE ser detectada como foto reutilizada"
    assert info2["platform"] == "WALLAPOP" and info2["item_id"] == "anuncio_1001"
    print(f"  -> dHash perceptual ({h1} vs {h2}, dist {dist}) y detección de foto reutilizada cross-platform verificado.")

    print("[5/6] Probando auditoría en tabla fraud_log...")
    test_hits = [{"code": "TEST_FRAUD", "category": "test", "label": "Prueba de estafa", "weight": 4}]
    log_fraud_event(conn, "TEST_SUITE", "item_test_99", "Anuncio falso", 50.0, 4, test_hits)
    c.execute("SELECT risk, reasons_json FROM fraud_log WHERE item_id = 'item_test_99'")
    row = c.fetchone()
    assert row is not None and row[0] == 4, "No se guardó el registro en fraud_log"
    print("  -> Auditoría en tabla fraud_log verificada.")

    print("[6/6] Probando integración con passes_filters en Puerta 0...")
    dummy_target = {"id": "switch_oled", "name": "Switch OLED", "min_price": 50, "max_price": 250, "enabled": True}
    # Anuncio con bizum -> debe ser descartado directamente en passes_filters
    ok_passes = passes_filters("Nintendo Switch OLED", "Solo acepto pago por bizum antes de enviar", 120.0, dummy_target, [])
    assert ok_passes is False, "passes_filters debería rechazar anuncio con pago externo"

    # Anuncio limpio -> debe pasar
    ok_clean = passes_filters("Nintendo Switch OLED blanca", "Consola completa con Joy-Cons y dock oficial", 160.0, dummy_target, [])
    assert ok_clean is True, "passes_filters debería aceptar anuncio limpio"
    print("  -> passes_filters integrado con evaluate_fraud_risk con éxito.")

    # Limpieza de registros de test
    c.execute("DELETE FROM seen_image_hashes WHERE hash IN (?, ?)", (h1, h2))
    c.execute("DELETE FROM fraud_log WHERE platform = 'TEST_SUITE'")
    conn.commit()

    print("\n✅ ¡TODOS LOS TESTS DE LA FASE 2 HAN PASADO CON ÉXITO!")

if __name__ == "__main__":
    run_tests()
