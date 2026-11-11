import unittest

import _harness  # noqa: F401  (debe ir primero)
import core_engine


class TestHarness(unittest.TestCase):
    def test_db_aislada(self):
        self.assertTrue(core_engine.DB_PATH.startswith("/work/"), core_engine.DB_PATH)

    def test_sin_secretos(self):
        self.assertEqual(core_engine.GEMINI_API_KEY, "")

    def test_sin_red(self):
        from curl_cffi import requests as creq
        with self.assertRaises(Exception):
            creq.get("https://example.com", timeout=3)

    def test_socket_externo_bloqueado(self):
        import socket
        s = socket.socket()
        try:
            with self.assertRaises(OSError):
                s.connect(("93.184.216.34", 80))
        finally:
            s.close()

    def test_pipeline_version(self):
        self.assertRegex(core_engine.PIPELINE_VERSION, r"^\d{4}\.\d{2}\.\d{2}-")

    def test_save_alert_context_guarda_pipeline_version(self):
        import json
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE deals (deal_id TEXT, alert_context_json TEXT)")
        conn.execute("INSERT INTO deals VALUES ('x', NULL)")
        core_engine.save_alert_context(conn, "x", seller={"name": "a"}, ai_data={})
        ctx = json.loads(conn.execute("SELECT alert_context_json FROM deals").fetchone()[0])
        self.assertEqual(ctx["pipeline_version"], core_engine.PIPELINE_VERSION)


if __name__ == "__main__":
    unittest.main()
