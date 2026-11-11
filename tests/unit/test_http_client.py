# -*- coding: utf-8 -*-
import tests.unit._harness  # noqa: F401 — debe ser la primera línea

import os
import http.server
import socketserver
import threading
import unittest
from unittest import mock

import antiban
import http_client


class _CaptureHandler(http.server.BaseHTTPRequestHandler):
    captured = []

    def do_GET(self):
        _CaptureHandler.captured.append((self.path, dict(self.headers.items())))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"search_objects": [], "counters": []}')

    def do_POST(self):
        _CaptureHandler.captured.append((self.path, dict(self.headers.items())))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"hits": []}')

    def log_message(self, *args):
        pass


class TestHttpClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), _CaptureHandler)
        cls.port = cls.srv.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _CaptureHandler.captured.clear()

    def test_browser_profiles_current(self):
        imps = {p["impersonate"] for p in antiban.BROWSER_PROFILES}
        self.assertEqual(imps, {"chrome152", "chrome152_macos", "safari260"})
        self.assertEqual(antiban.BrowserProfileRotator.get_safari_impersonation(), "safari260")
        for p in antiban.BROWSER_PROFILES:
            ua = str(p.get("user_agent") or "")
            self.assertNotIn("Chrome/124", ua)
            self.assertNotIn("Chrome/131", ua)
            self.assertNotIn("Version/17.0", ua)

    def test_xhr_headers_and_wallapop_identity(self):
        client = http_client.PlatformClient(
            "WALLAPOP",
            impersonate="chrome152",
            origin="https://es.wallapop.com",
            referer="https://es.wallapop.com/",
            extra_headers={"X-DeviceOS": "0"},
        )
        r = client.get(f"{self.base_url}/api/v3/search", kind="xhr", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(_CaptureHandler.captured), 1)
        _, raw_headers = _CaptureHandler.captured[0]
        hdrs = {k.lower(): v for k, v in raw_headers.items()}

        self.assertEqual(hdrs.get("sec-fetch-mode"), "cors")
        self.assertEqual(hdrs.get("sec-fetch-dest"), "empty")
        self.assertEqual(hdrs.get("sec-fetch-site"), "same-site")
        self.assertNotIn("sec-fetch-user", hdrs)
        self.assertNotIn("upgrade-insecure-requests", hdrs)
        self.assertIn("Chrome/152", hdrs.get("user-agent", ""))
        self.assertIn('v="152"', hdrs.get("sec-ch-ua", ""))
        self.assertTrue(hdrs.get("accept-language", "").startswith("es-ES"))
        self.assertEqual(hdrs.get("origin"), "https://es.wallapop.com")
        self.assertEqual(hdrs.get("referer"), "https://es.wallapop.com/")
        self.assertEqual(hdrs.get("x-deviceos"), "0")

    def test_document_headers(self):
        client = http_client.PlatformClient(
            "VINTED",
            impersonate="chrome152",
            referer="https://www.vinted.es/",
        )
        r = client.get(f"{self.base_url}/catalog", kind="document", timeout=5)
        self.assertEqual(r.status_code, 200)
        _, raw_headers = _CaptureHandler.captured[0]
        hdrs = {k.lower(): v for k, v in raw_headers.items()}
        self.assertEqual(hdrs.get("sec-fetch-mode"), "navigate")
        self.assertEqual(hdrs.get("sec-fetch-dest"), "document")
        self.assertTrue(hdrs.get("accept-language", "").startswith("es-ES"))
        self.assertIn("Chrome/152", hdrs.get("user-agent", ""))

    def test_session_reused_across_3_gets_and_rotated(self):
        real_session = http_client.requests.Session
        created = []

        def counting_session(*args, **kwargs):
            s = real_session(*args, **kwargs)
            created.append(s)
            return s

        client = http_client.PlatformClient("WALLAPOP", impersonate="chrome152")
        with mock.patch("http_client.requests.Session", side_effect=counting_session):
            client.get(f"{self.base_url}/1", kind="xhr", timeout=5)
            client.get(f"{self.base_url}/2", kind="xhr", timeout=5)
            client.get(f"{self.base_url}/3", kind="xhr", timeout=5)
            self.assertEqual(len(created), 1, "3 get seguidos deben reutilizar 1 sola Session")

            # Otro hilo debe crear su propia Session (threading.local)
            t = threading.Thread(target=lambda: client.get(f"{self.base_url}/4", kind="xhr", timeout=5))
            t.start()
            t.join()
            self.assertEqual(len(created), 2, "Otro hilo debe tener su propia Session")

            # rotate() invalida la sesión actual
            client.rotate()
            client.get(f"{self.base_url}/5", kind="xhr", timeout=5)
            self.assertEqual(len(created), 3, "rotate() debe abrir una nueva Session")

    def test_requirements_and_dockerfile(self):
        root = tests.unit._harness._ROOT
        req_path = os.path.join(root, "requirements.txt")
        self.assertTrue(os.path.isfile(req_path), "Debe existir requirements.txt")
        with open(req_path, encoding="utf-8") as fh:
            req = fh.read()
        for pkg in ("curl_cffi==0.17.0", "pillow==13.0.0", "discord.py==2.7.1", "requests==2.34.2"):
            self.assertIn(pkg, req)

        with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as fh:
            df = fh.read()
        self.assertIn("requirements.txt", df)
        self.assertIn("http_client.py", df)


if __name__ == "__main__":
    unittest.main()
