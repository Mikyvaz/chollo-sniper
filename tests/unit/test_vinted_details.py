import tests.unit._harness  # noqa: F401
import sqlite3
import unittest
from unittest.mock import patch


class FakeHttpResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data or {}
        self.headers = {}

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kw):
        self.calls.append(url)
        if not self.responses:
            return FakeHttpResponse(500, "")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class TestVintedDetails(unittest.TestCase):
    def setUp(self):
        import core_engine
        import fraud_signals
        self.conn = sqlite3.connect(":memory:")
        core_engine.init_db(self.conn)
        fraud_signals._SELLER_PROFILE_CACHE.clear()

    def tearDown(self):
        self.conn.close()

    def test_fetch_item_details_returns_none_on_403_429_timeout_and_cooldown(self):
        import vinted_sniper

        client = vinted_sniper.VintedClient.__new__(vinted_sniper.VintedClient)
        client.proxies = None
        client.rotator = vinted_sniper.rotator
        client.health_monitor = vinted_sniper.AntiBanHealthMonitor("TEST_VINTED")
        client.consecutive_empty = 0
        client.session_epoch = 0

        # 1) 403 -> None
        client.session = FakeSession([FakeHttpResponse(403, "Forbidden")])
        self.assertIsNone(client.fetch_item_details("https://www.vinted.es/items/1001"))

        # Clear cooldown triggered by 403 to test 500 and timeout
        client.health_monitor.cooldown_until = 0
        client.session = FakeSession([FakeHttpResponse(500, "Server Error")])
        self.assertIsNone(client.fetch_item_details("https://www.vinted.es/items/1002"))

        client.session = FakeSession([TimeoutError("timed out")])
        self.assertIsNone(client.fetch_item_details("https://www.vinted.es/items/1003"))

        # Cooldown active -> None without calling session.get
        client.health_monitor.cooldown_until = 9999999999.0
        sess_cd = FakeSession([FakeHttpResponse(200, "<html></html>")])
        client.session = sess_cd
        self.assertIsNone(client.fetch_item_details("https://www.vinted.es/items/1004"))
        self.assertEqual(len(sess_cd.calls), 0)

    def test_retry_then_evaluate_and_skip_after_retries_exhausted(self):
        import vinted_sniper
        from core_engine import check_item_seen_or_dropped

        client = vinted_sniper.VintedClient.__new__(vinted_sniper.VintedClient)
        client.proxies = None
        client.rotator = vinted_sniper.rotator
        client.health_monitor = vinted_sniper.AntiBanHealthMonitor("TEST_VINTED_LOOP")
        client.consecutive_empty = 0
        client.session_epoch = 0

        html_ok = (
            '<html><head><meta name="description" content="Consola Nintendo Switch OLED completa con caja y dock"/></head>'
            '<body><span data-testid="profile-username">vendedor_top</span>'
            '"user":{"id":555111},"feedback_count":25,"feedback_reputation":0.98'
            '<div itemProp="upload_date"><span>hace 5 minutos</span></div>'
            '</body></html>'
        )

        # Item A: 403 on pass 1, 200 on pass 2
        details_fail = {}
        item_a = {"id": "vin_a", "title": "Nintendo Switch OLED", "price": 140.0, "url": "https://www.vinted.es/items/1"}

        client.session = FakeSession([FakeHttpResponse(403, "Forbidden")])
        res1 = client.fetch_item_details(item_a["url"])
        self.assertIsNone(res1)
        ok1 = vinted_sniper.handle_vinted_details_result(
            self.conn, item_a["id"], item_a["title"], item_a["price"], res1, details_fail
        )
        self.assertFalse(ok1)
        self.assertEqual(details_fail.get("vin_a"), 1)
        seen1, _ = check_item_seen_or_dropped(self.conn, "VINTED", "vin_a", 140.0)
        self.assertFalse(seen1, "Item must NOT be marked seen after 1st details failure")

        # Pass 2: 200 OK -> proceeds
        client.health_monitor.cooldown_until = 0
        client.session = FakeSession([FakeHttpResponse(200, html_ok)])
        res2 = client.fetch_item_details(item_a["url"])
        self.assertIsNotNone(res2)
        ok2 = vinted_sniper.handle_vinted_details_result(
            self.conn, item_a["id"], item_a["title"], item_a["price"], res2, details_fail
        )
        self.assertTrue(ok2)
        self.assertNotIn("vin_a", details_fail)

        # Item B: fails 3 times (initial + 2 retries) -> marked seen and emits item_skipped
        item_b = {"id": "vin_b", "title": "Nintendo Switch Lite", "price": 75.0, "url": "https://www.vinted.es/items/2"}
        metrics = []
        with patch.object(vinted_sniper, "log_metric", side_effect=lambda *a, **kw: metrics.append((a, kw))):
            for attempt in (1, 2):
                ok = vinted_sniper.handle_vinted_details_result(
                    self.conn, item_b["id"], item_b["title"], item_b["price"], None, details_fail
                )
                self.assertFalse(ok)
                seen, _ = check_item_seen_or_dropped(self.conn, "VINTED", "vin_b", 75.0)
                self.assertFalse(seen, f"Item must NOT be marked seen on attempt {attempt}")
                self.assertEqual(len(metrics), 0)

            # 3rd failure (after 2 retries) -> marked seen + metric emitted
            ok3 = vinted_sniper.handle_vinted_details_result(
                self.conn, item_b["id"], item_b["title"], item_b["price"], None, details_fail
            )
            self.assertFalse(ok3)
            seen3, _ = check_item_seen_or_dropped(self.conn, "VINTED", "vin_b", 75.0)
            self.assertTrue(seen3, "Item MUST be marked seen after exhausting retries")
            self.assertEqual(len(metrics), 1)
            args, kwargs = metrics[0]
            self.assertEqual(args[0], "item_skipped")
            self.assertEqual(kwargs.get("reason"), "details_unavailable")
            self.assertEqual(kwargs.get("item_id"), "vin_b")

    def test_cooldown_stops_fetching_more_details_in_batch(self):
        import vinted_sniper
        from core_engine import check_item_seen_or_dropped

        client = vinted_sniper.VintedClient.__new__(vinted_sniper.VintedClient)
        client.proxies = None
        client.rotator = vinted_sniper.rotator
        client.health_monitor = vinted_sniper.AntiBanHealthMonitor("TEST_VINTED_CD")
        client.consecutive_empty = 0
        client.session_epoch = 0

        # 1st item triggers 403 -> cooldown; 2nd and 3rd items must NOT be requested
        sess = FakeSession([
            FakeHttpResponse(403, "Forbidden"),
            FakeHttpResponse(200, "<html>should not be fetched</html>"),
            FakeHttpResponse(200, "<html>should not be fetched</html>"),
        ])
        client.session = sess
        items = [
            {"id": "i1", "title": "Nintendo Switch OLED 1", "price": 130.0, "url": "https://www.vinted.es/items/10"},
            {"id": "i2", "title": "Nintendo Switch OLED 2", "price": 135.0, "url": "https://www.vinted.es/items/11"},
            {"id": "i3", "title": "Nintendo Switch OLED 3", "price": 140.0, "url": "https://www.vinted.es/items/12"},
        ]
        details_fail = {}
        evaluated = []
        for it in items:
            if client.is_in_cooldown():
                break
            res = client.fetch_item_details(it["url"])
            if not vinted_sniper.handle_vinted_details_result(
                self.conn, it["id"], it["title"], it["price"], res, details_fail
            ):
                if client.is_in_cooldown():
                    break
                continue
            evaluated.append(it["id"])

        self.assertEqual(len(sess.calls), 1, "Must stop requesting item fichas as soon as cooldown starts")
        self.assertEqual(evaluated, [])
        for iid in ("i1", "i2", "i3"):
            seen, _ = check_item_seen_or_dropped(self.conn, "VINTED", iid, 130.0)
            self.assertFalse(seen)

    def test_vinted_seller_enrichment_skips_api_when_ficha_has_10_plus_reviews(self):
        import fraud_signals

        sess = FakeSession([FakeHttpResponse(200, "{}")])
        item = {
            "platform": "VINTED",
            "seller": {
                "id": "vinted_user_10plus",
                "name": "vendedor_fiable",
                "sales": 15,
                "rating": 4.9,
                "age_days": None,
            },
        }
        prof = fraud_signals.enrich_seller_profile(item, http_session=sess, platform="VINTED")
        self.assertEqual(len(sess.calls), 0, "Should not call /api/v2/users/{id} when reviews >= 10 and rating present")
        self.assertEqual(prof["seller_reviews_count"], 15)
        self.assertAlmostEqual(prof["seller_rating"], 4.9)

        # Also test with feedback_count / feedback_reputation keys when sales/rating are None
        fraud_signals._SELLER_PROFILE_CACHE.clear()
        item2 = {
            "platform": "VINTED",
            "seller": {
                "id": "vinted_user_fb_keys",
                "name": "vendedor_fb",
                "sales": None,
                "rating": None,
                "feedback_count": 12,
                "feedback_reputation": 0.96,
                "age_days": None,
            },
        }
        prof2 = fraud_signals.enrich_seller_profile(item2, http_session=sess, platform="VINTED")
        self.assertEqual(len(sess.calls), 0, "Should not call /api/v2/users/{id} when feedback_count >= 10 and feedback_reputation present")
        self.assertEqual(prof2["seller_reviews_count"], 12)
        self.assertAlmostEqual(prof2["seller_rating"], 4.8)


if __name__ == "__main__":
    unittest.main()
