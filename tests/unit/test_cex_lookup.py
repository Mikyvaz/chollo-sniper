"""T1.3: CeX con caché por consulta (hits crudos), espejo con frescura y caché negativa."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce

TARGET = {t["id"]: t for t in ce.load_config()["targets"]}["ps5_consoles_gta6"]
Q = "PlayStation 5 Slim"
DIGITAL = "Playstation 5 Slim Digital, 825GB, Blanca, Sin Caja"
LECTOR = "Playstation 5 Slim, 1TB, Blanca, Sin Caja"


def _resp(hits, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = {"hits": hits}
    return r


class TestCexLookup(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        with mock.patch("core_engine.threading.Thread"):
            ce.init_db(self.conn)
        self.conn.execute("DELETE FROM cex_catalog_mirror")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _mirror(self, box_id, name, cash, sell, age="0 hours"):
        self.conn.execute(
            "INSERT OR REPLACE INTO cex_catalog_mirror (box_id, box_name, category_name, cash_price, voucher_price, sell_price, last_synced)"
            " VALUES (?, ?, 'PS5 Consolas', ?, ?, ?, datetime('now', ?))",
            (box_id, name, cash, cash + 20, sell, f"-{age}"),
        )
        self.conn.commit()

    def _hit(self, box_id, name, cash, sell):
        return {"boxId": box_id, "boxName": name, "categoryFriendlyName": "PS5 Consolas",
                "cashPriceCalculated": cash, "exchangePriceCalculated": cash + 20, "sellPrice": sell}

    def _fetch(self, desc):
        return ce.fetch_cex_live(self.conn, Q, reference_price=280, target=TARGET,
                                 title_context="PS5 Slim", desc_context=desc)

    def test_el_contexto_decide_la_ficha_en_cada_llamada(self):
        self._mirror("A1", DIGITAL, 372, 429)
        self._mirror("B1", LECTOR, 437, 489)
        with mock.patch("core_engine.requests.post", side_effect=AssertionError("sin red")):
            a = self._fetch("Versión digital sin lector, como nueva")
            b = self._fetch("Con lector de discos, 1TB, como nueva")
        self.assertIn("Digital", a["box_name"])
        self.assertEqual(b["box_name"], LECTOR)
        self.assertEqual(b["cash"], 437)
        n = self.conn.execute("SELECT COUNT(*) FROM cex_cache").fetchone()[0]
        self.assertEqual(n, 0, "ya no se escribe cex_cache")

    def test_alias_acepta_radar_id(self):
        self._mirror("B1", LECTOR, 437, 489)
        res = ce.get_cex_Cached_or_live(self.conn, Q, radar_id="ps5_consoles_gta6", desc_context="con lector 1TB")
        self.assertEqual((res or {}).get("box_name"), LECTOR)

    def test_espejo_de_3_dias_se_ignora_y_se_recurre_a_algolia(self):
        self._mirror("B1", LECTOR, 437, 489, age="3 days")
        hit = self._hit("B1", LECTOR, 440, 495)
        with mock.patch("core_engine.requests.post", return_value=_resp([hit])) as post:
            res = self._fetch("Con lector de discos, 1TB")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(res["cash"], 440)

    def test_cache_negativa_no_repite_algolia(self):
        with mock.patch("core_engine.requests.post", return_value=_resp([])) as post:
            self.assertIsNone(self._fetch("lector"))
            self.assertIsNone(self._fetch("lector"))
        self.assertEqual(post.call_count, 1)
        row = self.conn.execute("SELECT hits_json FROM cex_query_hits").fetchone()
        self.assertEqual(row[0], "[]")

    def test_cache_negativa_caduca_a_las_6_horas_y_positiva_a_las_24(self):
        with mock.patch("core_engine.requests.post", return_value=_resp([])) as post:
            self._fetch("lector")
            self.conn.execute("UPDATE cex_query_hits SET fetched_at = datetime('now','-7 hours')")
            self.conn.commit()
            self._fetch("lector")
        self.assertEqual(post.call_count, 2)
        hit = self._hit("B1", LECTOR, 440, 495)
        self.conn.execute("DELETE FROM cex_query_hits")
        self.conn.commit()
        with mock.patch("core_engine.requests.post", return_value=_resp([hit])) as post:
            self._fetch("lector")
            self.conn.execute("UPDATE cex_query_hits SET fetched_at = datetime('now','-10 hours')")
            self.conn.commit()
            self._fetch("lector")
            self.assertEqual(post.call_count, 1)
            self.conn.execute("UPDATE cex_query_hits SET fetched_at = datetime('now','-25 hours')")
            self.conn.commit()
            self._fetch("lector")
        self.assertEqual(post.call_count, 2)

    def test_hits_cacheados_se_reevaluan_con_otro_contexto(self):
        hits = [self._hit("A1", DIGITAL, 372, 429), self._hit("B1", LECTOR, 437, 489)]
        with mock.patch("core_engine.requests.post", return_value=_resp(hits)) as post:
            a = self._fetch("Versión digital sin lector")
            b = self._fetch("Con lector de discos, 1TB")
        self.assertEqual(post.call_count, 1)
        self.assertIn("Digital", a["box_name"])
        self.assertEqual(b["box_name"], LECTOR)

    def test_error_de_red_no_se_cachea(self):
        with mock.patch("core_engine.requests.post", return_value=_resp([], status=500)):
            self.assertIsNone(self._fetch("lector"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cex_query_hits").fetchone()[0], 0)

    def test_mantenimiento_y_purga_del_espejo(self):
        self.conn.execute("INSERT INTO cex_query_hits (query, hits_json, fetched_at) VALUES ('viejo','[]',datetime('now','-3 days'))")
        self.conn.execute("INSERT INTO cex_query_hits (query, hits_json) VALUES ('nuevo','[]')")
        self.conn.commit()
        ce.run_db_maintenance(self.conn)
        qs = [r[0] for r in self.conn.execute("SELECT query FROM cex_query_hits")]
        self.assertEqual(qs, ["nuevo"])
        self._mirror("OLD", "Viejo 8 dias", 10, 20, age="8 days")
        self._mirror("NEW", "Nuevo", 10, 20)
        with mock.patch("core_engine.requests.post", return_value=_resp([])):
            ce.sync_cex_catalog_mirror(self.conn, force=True, master_queries=["x"])
        ids = [r[0] for r in self.conn.execute("SELECT box_id FROM cex_catalog_mirror")]
        self.assertEqual(ids, ["NEW"])


if __name__ == "__main__":
    unittest.main()
