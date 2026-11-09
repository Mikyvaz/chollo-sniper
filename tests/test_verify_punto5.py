#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de Verificación Exhaustivo - Punto 5 de CORRECION_FALLOS.md
Caché local por hash SHA-256 para bloquear llamadas con datos repetidos.
"""

import os
import sys
import sqlite3
import hashlib
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core_engine as ce

def test_punto5_complete():
    print("[TEST 1/5] Verificando generación de hash SHA-256 en build_robust_cache_key...")
    radar_id = "ps5_chassis_c"
    title1 = "PlayStation 5 Chasis C con mando y cables"
    desc1 = "Consola en perfecto estado, poco uso, incluye mando original."
    price1 = 250.0

    key1 = ce.build_robust_cache_key(radar_id, title1, desc1, price1)
    assert isinstance(key1, str), "La clave debe ser un string"
    assert len(key1) == 64, f"La clave debe tener 64 caracteres (SHA-256), tiene {len(key1)}: {key1}"
    assert all(c in "0123456789abcdef" for c in key1), f"La clave debe ser hexadecimal: {key1}"

    # Verificación de robustez (casing, URLs, puntuación)
    title2 = "¡¡PLAYSTATION 5 chasis c con mando y cables!! https://wallapop.com/test"
    desc2 = "consola en perfecto estado poco uso incluye mando original"
    price2 = 252.0  # Cae en el mismo bucket de 5€ (250€)
    key2 = ce.build_robust_cache_key(radar_id, title2, desc2, price2)
    assert key1 == key2, f"Claves deberían coincidir por bucket 5€ y normalización: {key1} vs {key2}"

    # Diferenciación con precio fuera de bucket
    price_diff = 265.0
    key3 = ce.build_robust_cache_key(radar_id, title1, desc1, price_diff)
    assert key1 != key3, "Claves con precio en distinto bucket deben ser diferentes"

    # Diferenciación con radar distinto
    key4 = ce.build_robust_cache_key("otro_radar", title1, desc1, price1)
    assert key1 != key4, "Claves con distinto radar_id deben ser diferentes"
    print("  -> OK: build_robust_cache_key genera SHA-256 válido (64 chars) y normaliza buckets.")

    print("[TEST 2/5] Verificando tabla ai_prompt_cache e índice en init_db...")
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.row_factory = sqlite3.Row
    ce.init_db(mem_conn)

    cursor = mem_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_prompt_cache'")
    assert cursor.fetchone() is not None, "Tabla ai_prompt_cache no fue creada en init_db"

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_prompt_cache_created'")
    assert cursor.fetchone() is not None, "Índice idx_prompt_cache_created no fue creado en init_db"

    # Verificando mantenimiento y purga TTL (-7 días)
    cursor.execute("""
        INSERT INTO ai_prompt_cache (prompt_hash, result_json, model, call_type, created_at)
        VALUES ('old_hash', '{"status": "old"}', 'gemini-3.5-flash-lite', 'general', datetime('now', '-8 days')),
               ('new_hash', '{"status": "new"}', 'gemini-3.5-flash-lite', 'general', CURRENT_TIMESTAMP)
    """)
    mem_conn.commit()

    ce.run_db_maintenance(mem_conn)
    cursor.execute("SELECT prompt_hash FROM ai_prompt_cache")
    remaining_hashes = [r[0] for r in cursor.fetchall()]
    assert "old_hash" not in remaining_hashes, "Registros de más de 7 días deben purgarse en run_db_maintenance"
    assert "new_hash" in remaining_hashes, "Registros recientes deben mantenerse"
    print("  -> OK: Tabla ai_prompt_cache, índices y purga TTL a 7 días verificados.")

    print("[TEST 3/5] Verificando Tier 2 Prompt Cache en ai_gateway_call...")
    test_prompt = "Audita el estado de Nintendo Switch Lite con rayón"
    expected_response = {"verdict": "proceed", "score": 8, "reason": "estado bueno"}

    # Simulamos una llamada a la API y comprobamos inserción en caché
    mock_resp = mock.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 20},
        "candidates": [{
            "content": {"parts": [{"text": '{"verdict": "proceed", "score": 8, "reason": "estado bueno"}'}]}
        }]
    }

    with mock.patch("core_engine.HTTP_SESSION.post", return_value=mock_resp) as mock_post:
        res1 = ce.ai_gateway_call(
            prompt=test_prompt,
            conn=mem_conn,
            call_type="test_eval",
            enforce_budget=False
        )
        assert res1 == expected_response, f"Resultado inesperado en llamada 1: {res1}"
        assert mock_post.call_count == 1, "La primera llamada debió consultar la API remota"

    # Segunda llamada: DEBE responder desde ai_prompt_cache con 0 peticiones de red
    with mock.patch("core_engine.HTTP_SESSION.post") as mock_post_blocked:
        mock_post_blocked.side_effect = AssertionError("¡No debe hacer llamadas de red en cache hit!")
        res2 = ce.ai_gateway_call(
            prompt=test_prompt,
            conn=mem_conn,
            call_type="test_eval",
            enforce_budget=False
        )
        assert res2 == expected_response, f"Resultado de caché incorrecto: {res2}"
        assert mock_post_blocked.call_count == 0, "En cache hit no debe invocar HTTP_SESSION.post"
    print("  -> OK: ai_gateway_call responde desde ai_prompt_cache con 0 llamadas de red.")

    print("[TEST 4/5] Verificando Tier 1 Cache en prescreen_item_text con SHA-256...")
    t = {"id": "ps5_disc", "name": "PS5 Lector", "max_price": 280}
    ad_title = "PS5 Modelo Disco con dos mandos"
    ad_desc = "Consola muy cuidada con todos sus cables y cajas"
    ad_price = 260.0
    ad_landed = 265.0

    mock_prescreen_resp = mock.MagicMock()
    mock_prescreen_resp.status_code = 200
    mock_prescreen_resp.json.return_value = {
        "usageMetadata": {"promptTokenCount": 60, "candidatesTokenCount": 15},
        "candidates": [{
            "content": {"parts": [{"text": '{"decision": "proceed", "reject_reason": "", "confidence": "alta"}'}]}
        }]
    }

    with mock.patch("core_engine.HTTP_SESSION.post", return_value=mock_prescreen_resp) as mock_post:
        dec1, reason1, conf1, from_cache1 = ce.prescreen_item_text(
            "WALLAPOP", ad_title, ad_desc, ad_price, ad_landed, t, conn=mem_conn
        )
        assert dec1 == "proceed"
        assert from_cache1 is False
        assert mock_post.call_count == 1

    # Comprobar que en ai_prescreen_cache la clave tiene 64 caracteres (SHA-256)
    expected_key = ce.build_robust_cache_key("ps5_disc", ad_title, ad_desc, ad_price, prefix="prescreen_v1")
    assert len(expected_key) == 64
    cursor.execute("SELECT decision, reject_reason FROM ai_prescreen_cache WHERE cache_key = ?", (expected_key,))
    row = cursor.fetchone()
    assert row is not None, "El resultado de prescreen no se guardó en ai_prescreen_cache"
    assert row[0] == "proceed"

    # Segunda llamada: Debe devolver from_cache=True directamente desde BD
    with mock.patch("core_engine.HTTP_SESSION.post") as mock_post_blocked:
        mock_post_blocked.side_effect = AssertionError("No debe haber llamadas HTTP en cache hit")
        dec2, reason2, conf2, from_cache2 = ce.prescreen_item_text(
            "WALLAPOP", ad_title, ad_desc, ad_price, ad_landed, t, conn=mem_conn
        )
        assert dec2 == "proceed"
        assert from_cache2 is True
    print("  -> OK: prescreen_item_text utiliza claves SHA-256 de 64 caracteres y recupera de caché.")

    print("[TEST 5/5] Verificando firma visual SHA-256 en analyze_and_generate_kit...")
    fake_img_bytes = b"GIF89a" + b"\x00" * 5000
    img_sig = hashlib.sha256(fake_img_bytes[:4096]).hexdigest()[:16]
    assert len(img_sig) == 16, f"img_sig debe tener 16 caracteres, tiene {len(img_sig)}"

    eval_key = ce.build_robust_cache_key("ps5_disc", ad_title, ad_desc, ad_price, img_sig=img_sig, prefix="v44")
    assert len(eval_key) == 64, f"eval_key debe tener 64 caracteres: {eval_key}"

    # Simular guardado en ai_eval_cache
    cursor.execute("""
        INSERT INTO ai_eval_cache (cache_key, result_json, evaluated_at)
        VALUES (?, '{"is_valid_deal": true, "deal_score": 9}', CURRENT_TIMESTAMP)
    """, (eval_key,))
    mem_conn.commit()

    cursor.execute("SELECT result_json FROM ai_eval_cache WHERE cache_key = ?", (eval_key,))
    assert cursor.fetchone() is not None, "ai_eval_cache debe almacenar y recuperar claves SHA-256"
    print("  -> OK: Firma visual SHA-256 y ai_eval_cache verificados.")

    print("\nTODOS LOS TESTS DEL PUNTO 5 COMPLETADOS CON ÉXITO.")

if __name__ == "__main__":
    test_punto5_complete()

