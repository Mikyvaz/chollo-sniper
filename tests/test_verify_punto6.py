#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de Verificación Exhaustivo - Punto 6 de CORRECION_FALLOS.md
Control de concurrencia en peticiones asíncronas y limpiador determinista de JSON con schema defaults.
"""

import os
import sys
import time
import sqlite3
import threading
from unittest import mock
import concurrent.futures

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core_engine as ce

def test_punto6_complete():
    print("[TEST 1/5] Verificando limpiador determinista de JSON avanzado con literales Python y arrays...")
    # Caso 1: Literales Python (True, False, None) y comas finales
    py_json = '{"is_valid": True, "error": False, "details": None, "score": 9, }'
    parsed1 = ce.repair_and_parse_json(py_json)
    assert parsed1 == {"is_valid": True, "error": False, "details": None, "score": 9}, f"Fallo en literales: {parsed1}"

    # Caso 2: Array que contiene un objeto
    arr_json = '[{"decision": "proceed", "confidence": "alta"}]'
    parsed2 = ce.repair_and_parse_json(arr_json)
    assert parsed2 == {"decision": "proceed", "confidence": "alta"}, f"Fallo en array: {parsed2}"

    # Caso 3: Texto conversacional + markdown con saltos de línea
    noisy_markdown = (
        "¡Hola! Aquí tienes la evaluación solicitada:\n"
        "```json\n"
        '{\n  "identified_model": "PS5 Slim",\n  "deal_score": 8,\n}\n'
        "```\n"
        "Espero que te sea de ayuda."
    )
    parsed3 = ce.repair_and_parse_json(noisy_markdown)
    assert parsed3 == {"identified_model": "PS5 Slim", "deal_score": 8}, f"Fallo en markdown: {parsed3}"
    print("  -> OK: Limpiador repara literales Python, arrays y markdown sin reintentos por API.")

    print("[TEST 2/5] Verificando validación y relleno con schema_defaults (cero caídas por campos faltantes)...")
    schema = {
        "decision": "proceed",
        "reject_reason": "",
        "confidence": "media",
        "deal_score": 0,
        "is_valid": False,
        "tags": []
    }

    # Respuesta parcial de la IA (falta reject_reason, deal_score, is_valid y tags; tiene booleanos en string)
    partial_ai_resp = '{"decision": "reject", "confidence": "alta", "is_valid": "true", "deal_score": "7"}'
    parsed_schema = ce.repair_and_parse_json(partial_ai_resp, schema_defaults=schema)
    assert parsed_schema["decision"] == "reject"
    assert parsed_schema["reject_reason"] == ""  # Rellenado con default
    assert parsed_schema["confidence"] == "alta"
    assert parsed_schema["deal_score"] == 7     # Coerción a int
    assert parsed_schema["is_valid"] is True    # Coerción de "true" a bool True
    assert parsed_schema["tags"] == []          # Rellenado con default

    # Texto roto no JSON con schema_defaults debe retornar dict con defaults exactos sin lanzar excepción
    broken_resp = "Error 500: Internal Server Overload"
    fallback_result = ce.repair_and_parse_json(broken_resp, schema_defaults=schema)
    assert fallback_result == schema, f"Fallback debe ser idéntico al schema: {fallback_result}"
    print("  -> OK: schema_defaults asegura integridad y tipo de campos con fallback resiliente.")

    print("[TEST 3/5] Verificando semáforo de concurrencia en ai_gateway_call...")
    assert ce.AI_MAX_CONCURRENCY >= 1, "AI_MAX_CONCURRENCY debe ser al menos 1"

    # Verificar que el semáforo acota la concurrencia
    sem = ce._AI_GATEWAY_SEMAPHORE
    # Adquirir todos los slots disponibles
    acquired_count = 0
    while sem.acquire(blocking=False):
        acquired_count += 1

    try:
        # Con todos los slots ocupados, una llamada a ai_gateway_call con timeout breve debe abortar limpiamente
        t_start = time.time()
        res_blocked = ce.ai_gateway_call(
            prompt="test prompt",
            conn=None,
            call_type="test_concurrency",
            timeout=1,
            enforce_budget=False,
            schema_defaults={"status": "aborted"}
        )
        elapsed = time.time() - t_start
        assert elapsed < 2.5, f"La llamada debió abortar por timeout del semáforo rápidamente ({elapsed:.2f}s)"
        assert res_blocked == {"status": "aborted"}, f"Debe retornar schema_defaults al abortar por concurrencia: {res_blocked}"
    finally:
        # Liberar los slots adquiridos
        for _ in range(acquired_count):
            sem.release()

    print(f"  -> OK: Semáforo de concurrencia ({ce.AI_MAX_CONCURRENCY} slots) y abort timeout verificados.")

    print("[TEST 4/5] Verificando timeout acotado y cancelación de futures en download_item_images...")
    def hanging_fetch(url, imp, proxies):
        if "hang" in url:
            time.sleep(15.0)  # Simula petición colgada
            return b"hanged_image"
        return b"GIF89a" + b"\x00" * 2000

    urls = ["https://example.com/ok1.jpg", "https://example.com/hang.jpg", "https://example.com/ok2.jpg"]
    with mock.patch("core_engine._fetch_single_image", side_effect=hanging_fetch):
        with mock.patch("concurrent.futures.wait") as mock_wait:
            # Simulamos que el wait detecta 2 completados y 1 no completado (timeout)
            fut_ok1 = mock.MagicMock()
            fut_ok1.cancelled.return_value = False
            fut_ok1.exception.return_value = None
            fut_ok1.result.return_value = b"GIF89a" + b"\x00" * 2000

            fut_ok2 = mock.MagicMock()
            fut_ok2.cancelled.return_value = False
            fut_ok2.exception.return_value = None
            fut_ok2.result.return_value = b"GIF89a" + b"\x00" * 2000

            fut_hang = mock.MagicMock()

            def fake_wait(fs, timeout=None):
                fs_list = list(fs)
                for f in fs_list[2:]:
                    f.cancel = fut_hang.cancel
                return set(fs_list[:2]), set(fs_list[2:])
            mock_wait.side_effect = fake_wait

            res_imgs = ce.download_item_images(urls)
            assert fut_hang.cancel.called, "Los futures no completados dentro del timeout deben ser cancelados"
            assert len(res_imgs) == 2, f"Deben procesarse solo las imágenes completadas: {len(res_imgs)}"
    print("  -> OK: download_item_images cancela descargas colgadas y respeta timeout acotado.")

    print("[TEST 5/5] Verificando integración completa en prescreen_item_text con JSON roto...")
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.row_factory = sqlite3.Row
    ce.init_db(mem_conn)

    # Simular que Gemini devuelve JSON roto (sin comillas de cierre y texto cortado)
    broken_gemini_resp = mock.MagicMock()
    broken_gemini_resp.status_code = 200
    broken_gemini_resp.json.return_value = {
        "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 10},
        "candidates": [{
            "content": {"parts": [{"text": '{"decision": "proceed", "reject_reason": '}]}
        }]
    }

    t = {"id": "switch_test", "name": "Switch Lite", "max_price": 90}
    with mock.patch("core_engine.HTTP_SESSION.post", return_value=broken_gemini_resp):
        dec, reason, conf, from_cache = ce.prescreen_item_text(
            "WALLAPOP", "Nintendo Switch Lite", "Caja y cargador", 65.0, 70.0, t, conn=mem_conn
        )
        assert dec == "proceed", f"Debe devolver proceed por fallback: {dec}"
        assert conf in ("alta", "media", "baja")
        assert from_cache is False
    print("  -> OK: prescreen_item_text tolera JSON malformado de APIs y recurre a defaults seguros.")

    print("\nTODOS LOS TESTS DEL PUNTO 6 COMPLETADOS CON ÉXITO.")

if __name__ == "__main__":
    test_punto6_complete()

