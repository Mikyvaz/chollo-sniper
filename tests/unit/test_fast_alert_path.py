"""Camino rápido de la alerta: push ntfy en paralelo con Discord y foto descargada junto a la Puerta 1."""
import json
import sqlite3
import threading
import time
import unittest
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
        self.headers = {}
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("sin JSON")
        return self._payload


def _target():
    return {"name": "Radar test", "mode": "resale"}


def _ai():
    return {"deal_score": 9, "deal_type": "direct_chollo", "identified_model": "Consola X", "deal_tier": 2}


class TestNtfyDispatchedBeforeDiscord(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE deals (deal_id TEXT PRIMARY KEY, alert_context_json TEXT)")
        self.conn.execute("INSERT INTO deals (deal_id, alert_context_json) VALUES ('WAL_999', '')")
        self.conn.execute("INSERT INTO deals (deal_id, alert_context_json) VALUES ('VIN_999', '')")
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.push_decision = [(True, "")]
        patches = [
            mock.patch.object(ce, "BOT_TOKEN", "tok-secreto-123"),
            mock.patch.object(ce, "ALERTS_CHANNEL_ID", "555"),
            mock.patch.object(ce, "WEBHOOK_URL", ""),
            mock.patch.object(ce, "ALERT_USER_ID", ""),
            mock.patch.object(ce, "NTFY_TOPIC_URL", "https://ntfy.example/mi-topic"),
            mock.patch.object(ce, "NTFY_TOKEN", ""),
            mock.patch.object(ce, "register_deal_in_db"),
            mock.patch.object(ce, "generate_multilingual_haggle_msg", return_value="hola"),
            mock.patch.object(ce, "decide_alert_push", side_effect=lambda *a, **k: self.push_decision[0]),
            mock.patch.object(ce, "get_notification_policy", return_value=dict(ce.NOTIFICATION_DEFAULTS)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _send(self, ai, platform="WALLAPOP", item_url="https://es.wallapop.com/item/abc-999", **kw):
        return ce.send_discord_alert(
            self.conn, platform, "999", "Consola X", "desc", 100.0, 105.0, 5.0,
            _target(), ai, None, item_url=item_url, net_profit=60.0, **kw)

    def _join_ntfy_threads(self):
        for th in threading.enumerate():
            if th.name == "ntfy-push":
                th.join(timeout=5)

    def test_ntfy_goes_out_while_discord_is_still_slow(self):
        """Discord tarda 0,6 s: el push ntfy ya ha salido antes de que Discord responda."""
        events = []
        discord_started = threading.Event()

        def fake_post(url, **kwargs):
            if "ntfy.example" in url:
                events.append(("ntfy", time.monotonic(), kwargs.get("json")))
                return FakeResp(200, {"id": "n1"})
            discord_started.set()
            time.sleep(0.6)
            events.append(("discord", time.monotonic(), kwargs.get("json")))
            return FakeResp(200, {"id": "d1"})

        ai = _ai()
        with mock.patch.object(ce.HTTP_SESSION, "post", side_effect=fake_post):
            self.assertTrue(self._send(ai))
            self._join_ntfy_threads()
        kinds = [e[0] for e in events]
        self.assertEqual(kinds.count("ntfy"), 1)
        self.assertEqual(kinds.count("discord"), 1)
        t_ntfy = [e[1] for e in events if e[0] == "ntfy"][0]
        t_discord = [e[1] for e in events if e[0] == "discord"][0]
        self.assertLess(t_ntfy, t_discord - 0.3, "el push no debe esperar a la respuesta de Discord")
        self.assertTrue(ai.get("_ntfy_dispatched"))

        ntfy_payload = [e[2] for e in events if e[0] == "ntfy"][0]
        self.assertEqual(ntfy_payload["topic"], "mi-topic")
        self.assertIn("Consola X", ntfy_payload["title"])
        self.assertIn("+60 €", ntfy_payload["title"])
        self.assertTrue(ntfy_payload["click"].startswith("https://es.wallapop.com/item/"))

    def test_scanner_follow_up_call_does_not_send_a_second_push(self):
        posts = []

        def fake_post(url, **kwargs):
            posts.append(url)
            return FakeResp(200, {"id": "x"})

        ai = _ai()
        with mock.patch.object(ce.HTTP_SESSION, "post", side_effect=fake_post):
            self._send(ai)
            self._join_ntfy_threads()
            # Lo que hacen sniper.py / vinted_sniper.py justo después de send_discord_alert
            payload = ce.build_ntfy_analysis_payload(ai, None, 60.0, target=_target(), score=9)
            self.assertTrue(payload["already_sent"])
            ok = ce.send_one_tap_ntfy_alert(
                {"title": "Consola X", "price": 100.0, "platform": "WALLAPOP",
                 "url": "https://es.wallapop.com/item/abc-999"}, payload, is_flash=False)
        self.assertTrue(ok)
        self.assertEqual(sum(1 for u in posts if "ntfy.example" in u), 1)

    def test_silent_alert_sends_no_push(self):
        posts = []
        self.push_decision[0] = (False, "regateo con margen < 80€")
        ai = _ai()
        with mock.patch.object(ce.HTTP_SESSION, "post",
                               side_effect=lambda url, **k: posts.append(url) or FakeResp(200, {"id": "x"})):
            self._send(ai)
            self._join_ntfy_threads()
        self.assertEqual(sum(1 for u in posts if "ntfy.example" in u), 0)
        self.assertFalse(ai.get("_ntfy_dispatched"))
        payload = ce.build_ntfy_analysis_payload(ai, None, 60.0, target=_target(), score=9)
        self.assertFalse(payload["push"])
        self.assertFalse(ce.send_one_tap_ntfy_alert({"title": "x", "price": 1, "platform": "WALLAPOP"}, payload))

    def test_no_ntfy_topic_means_no_thread_and_no_flag(self):
        ai = _ai()
        with mock.patch.object(ce, "NTFY_TOPIC_URL", ""), \
             mock.patch.object(ce.HTTP_SESSION, "post", return_value=FakeResp(200, {"id": "d1"})):
            self._send(ai)
        self.assertFalse(ai.get("_ntfy_dispatched"))

    def test_stale_dispatch_flag_from_reused_verdict_is_cleared(self):
        """Un veredicto reutilizado (dedup semántica) que arrastrase la marca no debe suprimir el push."""
        posts = []
        ai = _ai()
        ai["_ntfy_dispatched"] = True
        with mock.patch.object(ce.HTTP_SESSION, "post",
                               side_effect=lambda url, **k: posts.append(url) or FakeResp(200, {"id": "x"})):
            self._send(ai)
            self._join_ntfy_threads()
        self.assertEqual(sum(1 for u in posts if "ntfy.example" in u), 1)

    def test_vinted_headline_uses_country_not_brand_label(self):
        captured = []

        def fake_post(url, **kwargs):
            if "ntfy.example" in url:
                captured.append(kwargs.get("json"))
            return FakeResp(200, {"id": "x"})

        with mock.patch.object(ce.HTTP_SESSION, "post", side_effect=fake_post):
            self._send(_ai(), platform="VINTED", item_url="https://www.vinted.es/items/999-consola",
                       seller_city="Vinted (Sony)", country_tag="FR")
            self._join_ntfy_threads()
        self.assertEqual(len(captured), 1)
        self.assertIn("VINTED FR", captured[0]["title"])
        self.assertNotIn("Vinted (Sony)", captured[0]["title"])

    def test_ntfy_failure_never_breaks_the_discord_alert(self):
        def fake_post(url, **kwargs):
            if "ntfy.example" in url:
                raise RuntimeError("ntfy caído")
            return FakeResp(200, {"id": "d1"})

        with mock.patch.object(ce.HTTP_SESSION, "post", side_effect=fake_post):
            self.assertTrue(self._send(_ai()))
            self._join_ntfy_threads()


class TestEagerImagePrefetch(unittest.TestCase):
    def test_eager_download_overlaps_with_text_gate(self):
        started = threading.Event()
        release = threading.Event()

        def slow_download(urls, rotator=None, max_images=1, force_safari=False):
            started.set()
            release.wait(timeout=5)
            return [b"imgbytes"]

        with mock.patch.object(ce, "download_item_images", side_effect=slow_download) as dl, \
             mock.patch.object(ce, "calculate_dhash", return_value="abcd1234abcd1234") as dh:
            provider, state = ce.make_image_hash_prefetcher(["https://example.com/1.jpg"], eager=True)
            self.assertTrue(started.wait(timeout=5), "la descarga debe empezar sin esperar al proveedor")
            self.assertIsNone(state["photos"])           # aún en curso: la Puerta 1 corre mientras tanto
            release.set()
            self.assertEqual(provider(), ["abcd1234abcd1234"])
            self.assertEqual(provider(), ["abcd1234abcd1234"])
            self.assertEqual(state["photos"], [b"imgbytes"])
            dl.assert_called_once()
            dh.assert_called_once()

    def test_eager_download_error_yields_empty_photos_without_raising(self):
        with mock.patch.object(ce, "download_item_images", side_effect=RuntimeError("cdn")):
            provider, state = ce.make_image_hash_prefetcher(["https://example.com/1.jpg"], eager=True)
            self.assertEqual(provider(), [])
            self.assertEqual(state["photos"], [])
            self.assertEqual(state["dhash"], "")

    def test_eager_without_urls_does_not_spawn_and_keeps_none(self):
        with mock.patch.object(ce, "download_item_images") as dl:
            provider, state = ce.make_image_hash_prefetcher([], eager=True)
            self.assertEqual(provider(), [])
            self.assertIsNone(state["photos"])
            dl.assert_not_called()

    def test_lazy_mode_is_unchanged(self):
        timings = {}
        with mock.patch.object(ce, "download_item_images", return_value=[b"imgbytes"]) as dl, \
             mock.patch.object(ce, "calculate_dhash", return_value="ffff0000ffff0000"):
            provider, state = ce.make_image_hash_prefetcher(["https://example.com/1.jpg"], timings=timings)
            dl.assert_not_called()                       # bajo demanda
            self.assertEqual(provider(), ["ffff0000ffff0000"])
            self.assertIn("image", timings)
            dl.assert_called_once()

    def test_scanners_prefetch_before_text_gate_and_collect_before_vision(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for fname in ("sniper.py", "vinted_sniper.py", "multi_sniper.py"):
            with open(os.path.join(root, fname), "r", encoding="utf-8") as fh:
                src = fh.read()
            i_prefetch = src.index("eager=True")
            i_p1 = src.index("prescreen_item_text(", src.index("def main("))
            i_collect = src.index("_img_hash_provider()  # recoge")
            i_vision = src.index("analyze_and_generate_kit(", src.index("def main("))
            self.assertLess(i_prefetch, i_p1, f"{fname}: la foto debe pedirse antes de la Puerta 1")
            self.assertLess(i_p1, i_collect, fname)
            self.assertLess(i_collect, i_vision, f"{fname}: la foto se recoge antes de la visión")
            self.assertEqual(src.count("make_image_hash_prefetcher("), 1, f"{fname}: un único prefetcher por anuncio")


if __name__ == "__main__":
    unittest.main()
