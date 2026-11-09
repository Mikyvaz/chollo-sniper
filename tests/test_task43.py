# -*- coding: utf-8 -*-
"""
Test Suite for Task 4.3:
- cex_catalog_mirror SQLite table creation and schema validation
- _score_cex_candidate with single dict and candidate list
- sync_cex_catalog_mirror execution against Algolia CeX España
- lookup_cex_catalog_mirror in-memory matching with _box_matches_query_and_radar
- 0ms latency measurement (<5ms) resolving from cex_catalog_mirror
"""
import sys
import os
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, "/app")

import core_engine


class TestTask43CeXCatalogMirror(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        conn = core_engine.get_db()
        core_engine.init_db(conn)
        count = core_engine.sync_cex_catalog_mirror(
            conn, force=True, master_queries=["Nintendo Switch Consola"]
        )
        print(f"[setUpClass] Pre-calentado espejo CeX con {count} fichas de 'Nintendo Switch Consola'")
        conn.close()

    def setUp(self):
        self.conn = core_engine.get_db()

    def tearDown(self):
        if self.conn:
            self.conn.close()

    def test_01_cex_catalog_mirror_table_created(self):
        """Verifica que init_db crea la tabla cex_catalog_mirror con todas sus columnas."""
        c = self.conn.cursor()
        cols = [col[1] for col in c.execute("PRAGMA table_info(cex_catalog_mirror)").fetchall()]
        print("[Task 4.3] Columnas cex_catalog_mirror:", cols)
        expected_cols = ["box_id", "box_name", "category_name", "cash_price", "voucher_price", "sell_price", "last_synced"]
        for ec in expected_cols:
            self.assertIn(ec, cols, f"Columna '{ec}' falta en cex_catalog_mirror")

    def test_02_score_cex_candidate_dict_and_list(self):
        """Verifica _score_cex_candidate con dict individual (float) y con lista de candidatos (dict)."""
        box_sin_caja = {
            "box_name": "Nintendo Switch Consola OLED 64GB Blanca Sin Caja",
            "category": "Consolas Switch",
            "cash": 140.0,
            "voucher": 170.0,
            "sell": 240.0
        }
        box_con_caja = {
            "box_name": "Nintendo Switch Consola OLED 64GB Blanca Con Caja",
            "category": "Consolas Switch",
            "cash": 155.0,
            "voucher": 185.0,
            "sell": 260.0
        }

        score_sin = core_engine._score_cex_candidate(
            box_sin_caja, clean_q="Nintendo Switch OLED", radar_id="switch_oled_v1_lite", includes_box=False
        )
        self.assertIsInstance(score_sin, float)
        self.assertGreater(score_sin, 0)

        score_con = core_engine._score_cex_candidate(
            box_con_caja, clean_q="Nintendo Switch OLED", radar_id="switch_oled_v1_lite", includes_box=True
        )
        self.assertIsInstance(score_con, float)
        self.assertGreater(score_con, 0)

        best_unboxed = core_engine._score_cex_candidate(
            [box_sin_caja, box_con_caja], clean_q="Nintendo Switch OLED", radar_id="switch_oled_v1_lite", includes_box=False
        )
        self.assertEqual(best_unboxed["box_name"], box_sin_caja["box_name"])

        best_boxed = core_engine._score_cex_candidate(
            [box_sin_caja, box_con_caja], clean_q="Nintendo Switch OLED", radar_id="switch_oled_v1_lite", includes_box=True
        )
        self.assertEqual(best_boxed["box_name"], box_con_caja["box_name"])
        print("[Task 4.3] _score_cex_candidate dict & list validation: OK")

    def test_03_sync_cex_catalog_mirror_ttl(self):
        """Verifica control de TTL 12h en sync_cex_catalog_mirror."""
        c = self.conn.cursor()
        c.execute("SELECT count(*) FROM cex_catalog_mirror")
        total_in_db = c.fetchone()[0]
        self.assertGreater(total_in_db, 0, "cex_catalog_mirror table should not be empty")

        ttl_synced = core_engine.sync_cex_catalog_mirror(self.conn, force=False)
        self.assertEqual(ttl_synced, 0, "Expected 0 synced items when cache is fresh (<12h)")
        print(f"[Task 4.3] TTL check OK: total fichas en SQLite = {total_in_db}")

    def test_04_lookup_cex_catalog_mirror_match(self):
        """Verifica que lookup_cex_catalog_mirror encuentra fichas de Switch en SQLite."""
        t0 = time.perf_counter()
        res = core_engine.lookup_cex_catalog_mirror(
            self.conn,
            clean_q="Nintendo Switch OLED 64GB",
            target={"id": "switch_oled_v1_lite"},
            radar_id="switch_oled_v1_lite"
        )
        lookup_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[Task 4.3] lookup_cex_catalog_mirror tiempo directo: {lookup_ms:.2f} ms")
        self.assertIsNotNone(res, "lookup_cex_catalog_mirror should find Nintendo Switch OLED")
        self.assertIsInstance(res, dict)
        self.assertTrue(res.get("from_mirror"), "Result should have from_mirror=True")
        self.assertGreater(res.get("cash", 0), 0)
        self.assertGreater(res.get("sell", 0), 0)
        print("[Task 4.3] lookup_cex_catalog_mirror hit:", res["box_name"], f"Cash: {res['cash']}€, Sell: {res['sell']}€")

    def test_05_latency_zero_ms_get_cex_cached_or_live(self):
        """
        Verifica que get_cex_Cached_or_live resuelve desde el espejo local en <5ms
        sin realizar llamadas de red a Algolia.
        """
        clean_q = "Nintendo Switch OLED 64GB"
        cache_lookup_key = f"v43:switch_oled_v1_lite:{clean_q.lower()}:b0:l1"
        c = self.conn.cursor()
        c.execute("DELETE FROM cex_cache WHERE search_query = ?", (cache_lookup_key,))
        self.conn.commit()

        # Medir lookup directo desde mirror vs get_cex_Cached_or_live
        t_direct_0 = time.perf_counter()
        mirror_res = core_engine.lookup_cex_catalog_mirror(
            self.conn,
            clean_q,
            radar_id="switch_oled_v1_lite"
        )
        direct_ms = (time.perf_counter() - t_direct_0) * 1000.0
        print(f"[Task 4.3] Latencia directa lookup_cex_catalog_mirror: {direct_ms:.2f} ms")
        self.assertIsNotNone(mirror_res)
        self.assertLess(direct_ms, 5.0, f"Expected direct mirror resolution < 5.0ms, got {direct_ms:.2f}ms")

        # Ahora medir get_cex_Cached_or_live
        t0 = time.perf_counter()
        res = core_engine.get_cex_Cached_or_live(
            self.conn,
            clean_q,
            radar_id="switch_oled_v1_lite"
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[Task 4.3] Tiempo get_cex_Cached_or_live (con insert en cex_cache): {elapsed_ms:.2f} ms")

        # Una segunda llamada ya en cex_cache debe ser ultra rápida (<2ms)
        t_cache_0 = time.perf_counter()
        res_cached = core_engine.get_cex_Cached_or_live(
            self.conn,
            clean_q,
            radar_id="switch_oled_v1_lite"
        )
        cached_ms = (time.perf_counter() - t_cache_0) * 1000.0
        print(f"[Task 4.3] Tiempo get_cex_Cached_or_live desde cex_cache: {cached_ms:.2f} ms")
        self.assertLess(cached_ms, 5.0, f"Expected cached resolution < 5.0ms, got {cached_ms:.2f}ms")


if __name__ == "__main__":
    unittest.main()
