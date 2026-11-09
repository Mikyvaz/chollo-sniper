# -*- coding: utf-8 -*-
"""
Test Suite - Fase 5: Deduplicación Semántica (P1-6)
Verifica:
1. Operaciones vectoriales en Python puro (pack, unpack, coseno).
2. Generación de embeddings con Gemini API (models/gemini-embedding-2, 768 dims).
3. Estructura y persistencia en tabla SQLite item_embeddings.
4. Detección de duplicados semánticos (Switch OLED vs Switch OLED reescrita >= 0.90 vs PS5).
5. Reutilización de veredictos (descarte o aprobación sin visión).
6. Integración con is_duplicate_recent_deal.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/app")

import math
import json
import sqlite3

from core_engine import (
    init_db,
    pack_embedding,
    unpack_embedding,
    cosine_similarity,
    generate_text_embedding,
    save_item_embedding,
    find_semantic_duplicate,
    is_duplicate_recent_deal
)


def test_vector_math():
    print("\n--- Test 1: Matemáticas vectoriales en Python puro ---")
    # 1. Identical vectors
    v1 = [1.0, 2.0, 3.0, 4.0]
    v2 = [1.0, 2.0, 3.0, 4.0]
    sim = cosine_similarity(v1, v2)
    assert math.isclose(sim, 1.0, rel_tol=1e-5), f"Expected 1.0, got {sim}"

    # 2. Orthogonal vectors
    v_orth1 = [1.0, 0.0]
    v_orth2 = [0.0, 1.0]
    sim_orth = cosine_similarity(v_orth1, v_orth2)
    assert math.isclose(sim_orth, 0.0, abs_tol=1e-5), f"Expected 0.0, got {sim_orth}"

    # 3. Opposite vectors
    v_opp1 = [1.0, 1.0]
    v_opp2 = [-1.0, -1.0]
    sim_opp = cosine_similarity(v_opp1, v_opp2)
    assert math.isclose(sim_opp, -1.0, rel_tol=1e-5), f"Expected -1.0, got {sim_opp}"

    # 4. Mismatched length or empty
    assert cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    # 5. Pack & Unpack
    v_768 = [float(i) * 0.001 for i in range(768)]
    blob = pack_embedding(v_768)
    assert len(blob) == 768 * 4, f"Expected {768 * 4} bytes, got {len(blob)}"
    unpacked = unpack_embedding(blob)
    assert len(unpacked) == 768, f"Expected 768 floats, got {len(unpacked)}"
    assert math.isclose(unpacked[100], v_768[100], rel_tol=1e-5)
    assert unpack_embedding(b"") == ()
    assert unpack_embedding(b"123") == ()  # not divisible by 4

    print("  [OK] Pack/Unpack y Coseno en Python puro verificados al 100%.")


def test_gemini_embedding_generation():
    print("\n--- Test 2: Generación de embeddings con Gemini API ---")
    text = "Nintendo Switch OLED blanca 64GB en perfecto estado con caja"
    vec = generate_text_embedding(text)
    if vec is None:
        print("  [SKIP/WARN] GEMINI_API_KEY no disponible o cuota agotada. Saltando llamada en vivo.")
        return

    assert isinstance(vec, list), f"Expected list, got {type(vec)}"
    assert len(vec) == 768, f"Expected 768 dimensions, got {len(vec)}"
    assert any(x != 0.0 for x in vec), "Vector cannot be all zeros"

    # Empty text handling
    assert generate_text_embedding("") is None
    assert generate_text_embedding("   ") is None
    print(f"  [OK] Embedding generado con éxito ({len(vec)} dimensiones).")


def test_item_embeddings_db():
    print("\n--- Test 3: Persistencia en SQLite (item_embeddings) ---")
    conn = sqlite3.connect(":memory:")
    init_db(conn)

    # Verify table schema
    cols = [col[1] for col in conn.execute("PRAGMA table_info(item_embeddings)").fetchall()]
    for expected_col in ["item_key", "radar_id", "embedding", "model", "verdict_json", "title", "price", "created_at"]:
        assert expected_col in cols, f"Missing column {expected_col} in item_embeddings"

    # Save mock embedding
    mock_vec = [0.1] * 768
    ok = save_item_embedding(
        conn,
        item_key="WALLAPOP:123456",
        radar_id="switch_oled",
        vector_or_text=mock_vec,
        model="models/gemini-embedding-2",
        verdict_json=json.dumps({"is_valid_deal": True, "score": 9}),
        title="Nintendo Switch OLED",
        price=180.0
    )
    assert ok is True

    # Retrieve and verify
    c = conn.cursor()
    c.execute("SELECT item_key, radar_id, embedding, verdict_json, title, price FROM item_embeddings WHERE item_key = 'WALLAPOP:123456'")
    row = c.fetchone()
    assert row is not None
    assert row[0] == "WALLAPOP:123456"
    assert row[1] == "switch_oled"
    unpacked_vec = unpack_embedding(row[2])
    assert len(unpacked_vec) == 768
    assert math.isclose(unpacked_vec[0], 0.1, rel_tol=1e-5)
    assert json.loads(row[3])["score"] == 9
    assert row[4] == "Nintendo Switch OLED"
    assert row[5] == 180.0

    print("  [OK] Tabla y persistencia de item_embeddings verificadas.")


def test_semantic_duplicate_detection():
    print("\n--- Test 4: Detección de duplicado semántico ---")
    conn = sqlite3.connect(":memory:")
    init_db(conn)

    t1 = "Nintendo Switch OLED blanca 64GB en perfecto estado con caja y accesorios"
    t2 = "Switch OLED 64 gb color blanco impecable completa en caja original"
    t3 = "PlayStation 5 Slim 1TB con mando DualSense blanca"

    v1 = generate_text_embedding(t1)
    if not v1:
        print("  [SKIP] API key no disponible para test semántico.")
        return

    v2 = generate_text_embedding(t2)
    v3 = generate_text_embedding(t3)

    # Save t1 in item_embeddings
    save_item_embedding(
        conn, "WALLAPOP:1001", "switch_oled", v1,
        verdict_json=json.dumps({"is_valid_deal": True, "deal_score": 9, "identified_model": "Switch OLED Blanca"}),
        title="Nintendo Switch OLED blanca", price=190.0
    )

    # Find duplicate for rewritten t2 (passing pre-computed v2)
    dup, _ = find_semantic_duplicate(conn, t2, radar_id="switch_oled", threshold=0.90, vector=v2)
    assert dup is not None, "Expected duplicate match for rewritten Switch OLED"
    assert dup["item_key"] == "WALLAPOP:1001"
    assert dup["score"] >= 0.90, f"Expected score >= 0.90, got {dup['score']}"
    print(f"  [OK] Coincidencia semántica detectada con score: {dup['score']:.4f}")

    # Check that unrelated PS5 is NOT a duplicate
    no_dup, _ = find_semantic_duplicate(conn, t3, radar_id="switch_oled", threshold=0.90, vector=v3)
    assert no_dup is None, f"Unrelated product should NOT match as duplicate, got {no_dup}"
    print("  [OK] Producto no relacionado (PS5) rechazado correctamente como duplicado.")


def test_verdict_reuse():
    print("\n--- Test 5: Reutilización de veredictos (salto de visión) ---")
    conn = sqlite3.connect(":memory:")
    init_db(conn)

    # Case A: Previous listing was REJECTED by IA
    mock_vec_a = [0.05] * 768
    save_item_embedding(
        conn, "WALLAPOP:2001", "switch_oled", mock_vec_a,
        verdict_json=json.dumps({"is_valid_deal": False, "reject_reason": "Pantalla rota / accesorios"}),
        title="Nintendo Switch rota para piezas", price=70.0
    )

    dup_a, _ = find_semantic_duplicate(conn, "texto", radar_id="switch_oled", threshold=0.95, vector=mock_vec_a)
    assert dup_a is not None
    v_a = json.loads(dup_a["verdict_json"])
    assert v_a["is_valid_deal"] is False
    assert "Pantalla rota" in v_a["reject_reason"]
    print("  [OK] Veredicto de rechazo previo recuperado correctamente para saltar visión.")

    # Case B: Previous listing was APPROVED
    mock_vec_b = [0.08] * 768
    save_item_embedding(
        conn, "VINTED:3001", "ps5", mock_vec_b,
        verdict_json=json.dumps({"is_valid_deal": True, "identified_model": "PS5 Slim", "deal_score": 10}),
        title="PS5 Slim impecable", price=320.0
    )

    dup_b, _ = find_semantic_duplicate(conn, "texto", radar_id="ps5", threshold=0.95, vector=mock_vec_b)
    assert dup_b is not None
    v_b = json.loads(dup_b["verdict_json"])
    assert v_b["is_valid_deal"] is True
    assert v_b["identified_model"] == "PS5 Slim"
    print("  [OK] Veredicto de aprobación previo recuperado correctamente para reutilizar kit.")


def test_is_duplicate_recent_deal_integration():
    print("\n--- Test 6: Integración con is_duplicate_recent_deal ---")
    conn = sqlite3.connect(":memory:")
    init_db(conn)

    # Insert a deal into deals
    conn.execute(
        """INSERT INTO deals (deal_id, platform, item_id, radar_id, title, price, created_at)
           VALUES ('d1', 'WALLAPOP', '100', 'switch_oled', 'Nintendo Switch OLED Blanca 64GB', 190.0, CURRENT_TIMESTAMP)"""
    )
    conn.commit()

    # Exact title match within +-10 eur
    assert is_duplicate_recent_deal(conn, "WALLAPOP", "nintendo switch oled blanca 64gb", 195.0) is True
    # Price difference > 10 eur -> not duplicate
    assert is_duplicate_recent_deal(conn, "WALLAPOP", "Nintendo Switch OLED Blanca 64GB", 220.0) is False
    # Different platform -> not duplicate
    assert is_duplicate_recent_deal(conn, "VINTED", "Nintendo Switch OLED Blanca 64GB", 190.0) is False

    # Semantic check with embedding
    mock_vec = [0.03] * 768
    save_item_embedding(conn, "WALLAPOP:100", "switch_oled", mock_vec, title="Nintendo Switch OLED Blanca 64GB", price=190.0)

    # Rewritten title with same vector and similar price (192 eur)
    assert is_duplicate_recent_deal(
        conn, "WALLAPOP", "Switch OLED 64 blanca", 192.0, radar_id="switch_oled", semantic_check=True, vector=mock_vec
    ) is True

    print("  [OK] is_duplicate_recent_deal exacto y semántico verificado.")


if __name__ == "__main__":
    print("=== INICIANDO TEST SUITE FASE 5 (DEDUP SEMÁNTICA) ===")
    test_vector_math()
    test_gemini_embedding_generation()
    test_item_embeddings_db()
    test_semantic_duplicate_detection()
    test_verdict_reuse()
    test_is_duplicate_recent_deal_integration()
    print("\n>>> TODOS LOS TESTS DE LA FASE 5 HAN PASADO CON ÉXITO! <<<")
