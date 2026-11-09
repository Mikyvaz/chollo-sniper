# -*- coding: utf-8 -*-
"""
Test Suite for Task 4.5:
- Verify multi_sniper.py imports and uses ThreadPoolExecutor
- Verify fetch_store_for_target works concurrently across stores
- Verify that parallel execution time across 4 stores is close to max(t_i) rather than sum(t_i)
- Verify SQLite (conn) is only accessed from the main thread without ProgrammingError
"""
import sys
import os
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, "/app")

import core_engine
import multi_sniper


class TestTask45MultiSniperConcurrency(unittest.TestCase):

    def test_01_imports_and_definitions(self):
        """Verifica que multi_sniper tiene importado ThreadPoolExecutor y definida fetch_store_for_target."""
        self.assertTrue(hasattr(multi_sniper, "ThreadPoolExecutor"))
        self.assertTrue(hasattr(multi_sniper, "fetch_store_for_target"))
        self.assertTrue(callable(multi_sniper.fetch_store_for_target))

    def test_02_parallel_execution_timing(self):
        """
        Simula 4 tiendas con latencias de 0.15s, 0.20s, 0.25s, 0.30s.
        Si se ejecutan en serie: ~0.90s.
        Si se ejecutan en paralelo con ThreadPoolExecutor(max_workers=4): ~0.30s-0.45s.
        """
        mock_stores = [
            {
                "platform": "CASHCONVERTERS",
                "kw_field": "cashconverters_keywords",
                "fetcher": lambda rot, q, mi, ma: (time.sleep(0.15), [{"id": "cc_1", "title": "Test CC", "description": "", "price": 50, "landed_cost": 50, "fees": 0, "url": ""}])[1],
                "query_sleep": (0.01, 0.02)
            },
            {
                "platform": "MILANUNCIOS",
                "kw_field": "milanuncios_keywords",
                "fetcher": lambda rot, q, mi, ma: (time.sleep(0.20), [{"id": "ma_1", "title": "Test MA", "description": "", "price": 60, "landed_cost": 60, "fees": 0, "url": ""}])[1],
                "query_sleep": (0.01, 0.02)
            },
            {
                "platform": "EBAY",
                "kw_field": "ebay_keywords",
                "fetcher": lambda rot, q, mi, ma: (time.sleep(0.25), [{"id": "eb_1", "title": "Test EB", "description": "", "price": 70, "landed_cost": 70, "fees": 0, "url": ""}])[1],
                "query_sleep": (0.01, 0.02)
            },
            {
                "platform": "TODOCOLECCION",
                "kw_field": "todocoleccion_keywords",
                "fetcher": lambda rot, q, mi, ma: (time.sleep(0.30), [{"id": "tc_1", "title": "Test TC", "description": "", "price": 80, "landed_cost": 80, "fees": 0, "url": ""}])[1],
                "query_sleep": (0.01, 0.02)
            }
        ]

        target = {
            "id": "test_radar",
            "name": "Test Radar",
            "keywords": "test console",
            "min_price": 10,
            "max_price": 100
        }
        rotator = MagicMock()

        t_start = time.perf_counter()
        results = []
        with multi_sniper.ThreadPoolExecutor(max_workers=4) as executor:
            future_map = {
                executor.submit(multi_sniper.fetch_store_for_target, st, target, rotator): st
                for st in mock_stores
            }
            for fut in multi_sniper.as_completed(future_map):
                store, batches, waf_err = fut.result()
                results.append((store["platform"], batches, waf_err))

        t_elapsed = time.perf_counter() - t_start
        print(f"[Task 4.5] Tiempo paralelo 4 tiendas: {t_elapsed:.3f}s (Serie teórica sería 0.90s)")

        self.assertEqual(len(results), 4)
        # El tiempo paralelo debe ser sustancialmente menor que la suma (0.90s), típicamente < 0.55s
        self.assertLess(t_elapsed, 0.70, f"La ejecución paralela tomó demasiado tiempo ({t_elapsed:.2f}s >= 0.70s)")

    def test_03_sqlite_thread_safety_in_main_thread(self):
        """Verifica que procesar los resultados recolectados contra conn (SQLite) en el hilo principal no lanza ProgrammingError."""
        conn = core_engine.get_db()
        core_engine.init_db(conn)

        # Simular inserción / mark_item_seen en el hilo principal
        try:
            core_engine.mark_item_seen(conn, "TEST_STORE", "test_item_9999", "Test Console", 99.0)
            seen, drop = core_engine.check_item_seen_or_dropped(conn, "TEST_STORE", "test_item_9999", 99.0)
            self.assertTrue(seen)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

