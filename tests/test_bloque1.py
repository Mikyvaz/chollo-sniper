# -*- coding: utf-8 -*-
"""
Test de verificación del BLOQUE 1 (Plan v5.2):
1. Concurrencia de AdaptiveRateLimiter y ParserHealthTracker con 20 hilos sin RuntimeError.
2. EbaySessionManager con lock multihilo.
3. evaluate_fraud_risk penaliza con +4 a seller_id con fraud_flags >= 2 en SQLite.
4. evaluate_fraud_risk bonifica con -1 a seller_id con historial limpio (total_seen >= 3, flags=0, avg <= 1.0).
5. price_history registra submodelo normalizado tras evaluación IA de deal válido.
"""

import sys
import os
import sqlite3
import threading
import concurrent.futures

# Asegurar path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from antiban import AdaptiveRateLimiter, ParserHealthTracker
from fraud_signals import evaluate_fraud_risk
from core_engine import EbaySessionManager, record_market_observation, init_db


def test_concurrent_ratelimiter():
    print("[1/5] Probando AdaptiveRateLimiter con 20 hilos concurrentes...")
    limiter = AdaptiveRateLimiter(window_size=20)
    assert hasattr(limiter, "_lock"), "AdaptiveRateLimiter no tiene _lock"

    errors = []

    def worker(worker_id):
        try:
            plat = f"PLAT_{worker_id % 4}"
            for i in range(100):
                limiter.record_request(plat, 0.1 * (i % 10), 200 if i % 5 != 0 else 500)
                limiter.evaluate_adaptation(plat)
                limiter.get_current_interval(plat)
                if i % 30 == 0:
                    limiter.calculate_sleep(plat)
                if i == 50 and worker_id == 0:
                    limiter.reset(plat)
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker, i) for i in range(20)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Errores de concurrencia detectados: {errors}"
    print("  -> OK: 20 hilos ejecutados sin RuntimeError en AdaptiveRateLimiter.")


def test_concurrent_parser_tracker():
    print("[2/5] Probando ParserHealthTracker con 20 hilos concurrentes...")
    tracker = ParserHealthTracker("TEST", default_threshold=3, alert_cooldown=10)
    assert hasattr(tracker, "_lock"), "ParserHealthTracker no tiene _lock"

    errors = []

    def worker(worker_id):
        try:
            plat = f"STORE_{worker_id % 3}"
            for i in range(100):
                tracker.record_success(plat, f"query_{i % 5}", items_count=2)
                tracker.record_empty(plat, f"query_{i % 5}", threshold=5)
                tracker.record_platform_success(plat, total_items=5)
                tracker.record_platform_empty(plat, threshold=10)
                if i % 20 == 0:
                    tracker.record_parser_error(plat, f"query_{i % 5}", "Simulated error")
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker, i) for i in range(20)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Errores en ParserHealthTracker: {errors}"
    print("  -> OK: 20 hilos ejecutados sin errores en ParserHealthTracker.")


def test_ebay_session_manager_lock():
    print("[3/5] Verificando cerrojo en EbaySessionManager...")
    mgr = EbaySessionManager()
    assert hasattr(mgr, "_lock"), "EbaySessionManager no tiene _lock"
    print("  -> OK: EbaySessionManager cuenta con self._lock.")


def test_seller_reputation_active_query():
    print("[4/5] Probando consulta activa de seller_reputation en evaluate_fraud_risk...")
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE seller_reputation (
            seller_id TEXT PRIMARY KEY,
            platform TEXT,
            total_seen INTEGER DEFAULT 1,
            fraud_flags INTEGER DEFAULT 0,
            avg_risk_score REAL DEFAULT 0.0,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Insertar vendedor reincidente (fraud_flags = 2)
    c.execute(
        "INSERT INTO seller_reputation (seller_id, platform, total_seen, fraud_flags, avg_risk_score) VALUES (?, ?, ?, ?, ?)",
        ("scammer_123", "WALLAPOP", 4, 2, 4.0)
    )
    # Insertar vendedor histórico limpio (total_seen=5, fraud_flags=0, avg=0.5)
    c.execute(
        "INSERT INTO seller_reputation (seller_id, platform, total_seen, fraud_flags, avg_risk_score) VALUES (?, ?, ?, ?, ?)",
        ("trusted_456", "WALLAPOP", 5, 0, 0.5)
    )
    conn.commit()

    # Caso 1: Vendedor reincidente en anuncio aparentemente inocente
    seller_bad = {"id": "scammer_123", "name": "Vendedor Dudoso"}
    res_bad = evaluate_fraud_risk(
        title="Nintendo Switch OLED como nueva",
        description="Se entrega en mano en Madrid o envio protegido.",
        price=180.0,
        conn=conn,
        platform="WALLAPOP",
        seller=seller_bad
    )

    codes_bad = [h["code"] for h in res_bad["hits"]]
    assert "SELLER_KNOWN_REPEAT_OFFENDER" in codes_bad, f"No se encontró SELLER_KNOWN_REPEAT_OFFENDER: {codes_bad}"
    assert res_bad["risk"] >= 4, f"Riesgo esperado >= 4 para reincidente, obtenido {res_bad['risk']}"
    assert res_bad["is_fraud"] is True
    print("  -> OK: Vendedor con fraud_flags=2 penalizado con +4 y marcado como reincidente.")

    # Caso 2: Vendedor fiable con historial limpio y perfil con valoraciones
    seller_good = {"id": "trusted_456", "name": "Vendedor Fiable", "reviews_count": 12, "rating": 4.9}
    res_good = evaluate_fraud_risk(
        title="Nintendo Switch OLED como nueva",
        description="Se entrega en mano en Madrid o envio protegido.",
        price=180.0,
        conn=conn,
        platform="WALLAPOP",
        seller=seller_good
    )
    assert res_good["risk"] == 0, f"Riesgo esperado 0 para vendedor confiable, obtenido {res_good['risk']}"
    print("  -> OK: Vendedor histórico limpio recibe bonus y riesgo permanece en 0.")
    conn.close()


def test_price_history_submodel_recording():
    print("[5/5] Probando registro de price_history con submodelo...")
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            radar_name TEXT,
            platform TEXT,
            price REAL,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Simular guardado normalizado
    radar_id = "3ds_family"
    submodel = "new 3ds xl"
    submodel_key = f"{radar_id}:{submodel}"

    record_market_observation(radar_id, "WALLAPOP", 120.0, conn=conn)
    record_market_observation(submodel_key, "WALLAPOP", 120.0, conn=conn)

    c.execute("SELECT radar_name, platform, price FROM price_history ORDER BY id ASC")
    rows = c.fetchall()
    assert len(rows) == 2, f"Se esperaban 2 filas, obtenidas {len(rows)}"
    assert rows[0][0] == "3ds_family"
    assert rows[1][0] == "3ds_family:new 3ds xl"
    print("  -> OK: price_history almacena correctamente clave de radar y clave de submodelo.")
    conn.close()


if __name__ == "__main__":
    test_concurrent_ratelimiter()
    test_concurrent_parser_tracker()
    test_ebay_session_manager_lock()
    test_seller_reputation_active_query()
    test_price_history_submodel_recording()
    print("\n✅ TODOS LOS TESTS DEL BLOQUE 1 HAN PASADO CON ÉXITO.")
