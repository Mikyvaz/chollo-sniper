"""T1.12: ntfy por JSON UTF-8 con click al anuncio."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import _harness  # noqa: F401
import core_engine

REQS = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        REQS.append((self.path, dict(self.headers), self.rfile.read(n)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


ITEM = {"title": "Switch OLED \U0001F525 nueva", "price": 120.0, "platform": "wallapop",
        "url": "https://es.wallapop.com/item/switch-oled-123"}


class TestNtfyJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/mi-topic"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        REQS.clear()
        self.o = (core_engine.NTFY_TOPIC_URL, getattr(core_engine, "NTFY_TOKEN", ""),
                  core_engine.get_notification_policy)
        core_engine.NTFY_TOPIC_URL = self.url
        core_engine.NTFY_TOKEN = ""

    def tearDown(self):
        core_engine.NTFY_TOPIC_URL, core_engine.NTFY_TOKEN, core_engine.get_notification_policy = self.o

    def test_json_con_emoji_y_click(self):
        ok = core_engine.send_one_tap_ntfy_alert(ITEM, {"deal_tier": 1, "p2p_net_profit": 30})
        self.assertTrue(ok)
        self.assertEqual(len(REQS), 1)
        path, hdrs, body = REQS[0]
        self.assertEqual(path, "/")
        d = json.loads(body.decode("utf-8"))
        self.assertEqual(d["topic"], "mi-topic")
        self.assertIn("\U0001F525", d["title"])
        self.assertTrue(d["click"].startswith("https://"))
        self.assertEqual(d["priority"], 5)
        self.assertEqual(d["actions"][0]["action"], "view")

    def test_token_bearer(self):
        core_engine.NTFY_TOKEN = "tk_abc"
        core_engine.send_one_tap_ntfy_alert(ITEM, {"deal_tier": 2})
        self.assertEqual(REQS[0][1].get("Authorization"), "Bearer tk_abc")

    def test_flash_off_no_envia(self):
        core_engine.get_notification_policy = lambda: {"flash_mode": "off"}
        self.assertFalse(core_engine.send_one_tap_ntfy_alert(ITEM, None, is_flash=True))
        self.assertEqual(len(REQS), 0)


if __name__ == "__main__":
    unittest.main()
