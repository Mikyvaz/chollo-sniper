"""T1.4: «perfil no consultado» (None) != «0 valoraciones» (0 confirmado)."""
import sqlite3
import unittest
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce
import fraud_signals as fs


def _codes(result):
    return [h["code"] for h in result["hits"]]


def _wallapop_item():
    return {"platform": "WALLAPOP", "seller": {"id": "abc123xyz", "name": "pepe", "rating": None, "sales": None}}


def _resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    return r


class TestSellerUnknown(unittest.TestCase):
    def setUp(self):
        fs._SELLER_PROFILE_CACHE.clear()
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE deals (created_at TEXT, alert_context_json TEXT)")

    def tearDown(self):
        self.conn.close()
        fs._SELLER_PROFILE_CACHE.clear()

    # --- enrich_seller_profile ---
    def test_wallapop_error_leaves_none_and_unavailable(self):
        item = _wallapop_item()
        sess = mock.Mock()
        sess.get.side_effect = OSError("sin red")
        with mock.patch("fraud_signals._get_thread_local_session", return_value=sess):
            prof = fs.enrich_seller_profile(item)
        self.assertIsNone(prof["seller_reviews_count"])
        self.assertIsNone(item["seller_reviews_count"])
        self.assertIsNone(item["seller"]["seller_reviews_count"])
        self.assertEqual(prof["seller_profile_status"], "unavailable")
        self.assertEqual(item["seller"]["seller_profile_status"], "unavailable")
        self.assertIsNone(prof["seller_rating"])

    def test_wallapop_error_risk_and_push(self):
        item = _wallapop_item()
        sess = mock.Mock()
        sess.get.side_effect = OSError("sin red")
        with mock.patch("fraud_signals._get_thread_local_session", return_value=sess):
            fs.enrich_seller_profile(item)
        sr = fs.evaluate_seller_risk(item["seller"], price=280, platform="WALLAPOP")
        codes = _codes(sr)
        self.assertIn("SELLER_PROFILE_UNKNOWN", codes)
        for bad in ("SELLER_ZERO_REVIEWS", "SELLER_GHOST_PROFILE", "SELLER_NEW_ACCOUNT_ZERO_REVIEWS",
                    "HIGH_TICKET_ZERO_REP_SCAM_RISK"):
            self.assertNotIn(bad, codes)
        hit = [h for h in sr["hits"] if h["code"] == "SELLER_PROFILE_UNKNOWN"][0]
        self.assertEqual(hit["weight"], 1)
        self.assertIn("no respondió", hit["label"])
        self.assertTrue(sr["block_flash"])
        self.assertEqual(sr["risk_delta"], 1)
        push = ce.decide_alert_push(self.conn, "WALLAPOP", "direct_chollo", 90, seller=item["seller"])
        self.assertTrue(push[0], push)

    def test_unavailable_profile_is_not_cached(self):
        item = _wallapop_item()
        sess = mock.Mock()
        sess.get.side_effect = OSError("sin red")
        with mock.patch("fraud_signals._get_thread_local_session", return_value=sess):
            fs.enrich_seller_profile(item)
        self.assertEqual(len(fs._SELLER_PROFILE_CACHE), 0)

    def test_wallapop_confirmed_zero_reviews(self):
        item = _wallapop_item()
        sess = mock.Mock()
        sess.get.return_value = _resp({"stats": {"reviews": {"total": 0}}})
        with mock.patch("fraud_signals._get_thread_local_session", return_value=sess):
            prof = fs.enrich_seller_profile(item)
        self.assertEqual(prof["seller_reviews_count"], 0)
        self.assertEqual(prof["seller_profile_status"], "ok")
        self.assertIsNone(prof["seller_rating"])
        sr = fs.evaluate_seller_risk(item["seller"], price=280, platform="WALLAPOP")
        self.assertIn("SELLER_ZERO_REVIEWS", _codes(sr))
        self.assertNotIn("SELLER_PROFILE_UNKNOWN", _codes(sr))

    def test_confirmed_zero_keeps_scam_rules(self):
        seller = {"seller_reviews_count": 0, "seller_profile_status": "ok", "seller_joined_days": 3,
                  "seller_sold_count": 0, "seller_bought_count": 0}
        codes = _codes(fs.evaluate_seller_risk(seller, price=120, platform="WALLAPOP"))
        for c in ("SELLER_ZERO_REVIEWS", "SELLER_GHOST_PROFILE", "SELLER_NEW_ACCOUNT_ZERO_REVIEWS",
                  "HIGH_TICKET_ZERO_REP_SCAM_RISK"):
            self.assertIn(c, codes)

    def test_unknown_does_not_trigger_scam_rules_even_with_new_account(self):
        seller = {"seller_reviews_count": None, "seller_profile_status": "unavailable", "seller_joined_days": 3,
                  "seller_sold_count": 0, "seller_bought_count": 0}
        codes = _codes(fs.evaluate_seller_risk(seller, price=120, platform="WALLAPOP"))
        for c in ("SELLER_ZERO_REVIEWS", "SELLER_GHOST_PROFILE", "SELLER_NEW_ACCOUNT_ZERO_REVIEWS",
                  "HIGH_TICKET_ZERO_REP_SCAM_RISK"):
            self.assertNotIn(c, codes)
        self.assertIn("SELLER_PROFILE_UNKNOWN", codes)

    def test_milanuncios_not_applicable_no_reputation_hits(self):
        item = {"platform": "MILANUNCIOS", "seller": {"name": "vendedor"}}
        prof = fs.enrich_seller_profile(item)
        self.assertIsNone(prof["seller_reviews_count"])
        self.assertEqual(prof["seller_profile_status"], "not_applicable")
        sr = fs.evaluate_seller_risk(item["seller"], price=100, platform="MILANUNCIOS")
        self.assertEqual(_codes(sr), [])
        self.assertEqual(sr["risk_delta"], 0)

    def test_todocoleccion_and_ebay_without_feedback_not_applicable(self):
        for plat in ("TODOCOLECCION", "EBAY"):
            item = {"platform": plat, "seller": {"name": "x"}}
            prof = fs.enrich_seller_profile(item)
            self.assertEqual(prof["seller_profile_status"], "not_applicable", plat)
            self.assertIsNone(prof["seller_reviews_count"], plat)
            sr = fs.evaluate_seller_risk(item["seller"], price=50, platform=plat)
            self.assertEqual(_codes(sr), [], plat)

    def test_ebay_with_feedback_is_ok(self):
        item = {"platform": "EBAY", "seller": {"name": "x", "feedback_count": 120, "rating": 4.9}}
        prof = fs.enrich_seller_profile(item)
        self.assertEqual(prof["seller_profile_status"], "ok")
        self.assertEqual(prof["seller_reviews_count"], 120)

    def test_cash_converters_ok(self):
        item = {"platform": "CASHCONVERTERS", "seller": {}}
        prof = fs.enrich_seller_profile(item)
        self.assertEqual(prof["seller_profile_status"], "ok")
        self.assertEqual(prof["seller_reviews_count"], 999)

    def test_vinted_failure_unavailable(self):
        item = {"platform": "VINTED", "seller": {"id": "555", "name": "v"}}
        sess = mock.Mock()
        sess.get.side_effect = OSError("sin red")
        with mock.patch("builtins.print"):
            prof = fs.enrich_seller_profile(item, http_session=sess)
        self.assertIsNone(prof["seller_reviews_count"])
        self.assertEqual(prof["seller_profile_status"], "unavailable")

    # --- evaluate_fraud_risk (6.5) ---
    def test_unknown_reviews_do_not_hard_block_below_cex(self):
        seller = {"id": "abc123xyz", "name": "pepe", "seller_reviews_count": None,
                  "seller_profile_status": "unavailable"}
        res = fs.evaluate_fraud_risk("Playstation 5 con mando", "Buen estado", 100.0, cex_cash=300.0,
                                     platform="WALLAPOP", seller=seller)
        self.assertNotIn("ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER", [h["code"] for h in res["hits"]])
        self.assertFalse(res.get("should_block"))

    def test_confirmed_zero_reviews_still_hard_blocks_below_cex(self):
        seller = {"id": "abc123xyz", "name": "pepe", "seller_reviews_count": 0, "seller_profile_status": "ok"}
        res = fs.evaluate_fraud_risk("Playstation 5 con mando", "Buen estado", 100.0, cex_cash=300.0,
                                     platform="WALLAPOP", seller=seller)
        self.assertIn("ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER", [h["code"] for h in res["hits"]])
        self.assertTrue(res.get("should_block"))

    # --- core_engine ---
    def test_format_seller_line_unknown(self):
        line = ce.format_seller_line({"name": "pepe", "seller_reviews_count": None,
                                      "seller_profile_status": "unavailable"})
        self.assertIn("valoraciones no disponibles", line)
        self.assertNotIn("0 valoraciones", line)

    def test_format_seller_line_zero_confirmed(self):
        line = ce.format_seller_line({"name": "pepe", "seller_reviews_count": 0})
        self.assertIn("**0 valoraciones**", line)

    def test_push_none_not_silenced_zero_is_silenced(self):
        pol = ce.get_notification_policy()
        self.assertFalse(pol.get("push_zero_review_sellers"))
        self.assertTrue(ce.decide_alert_push(self.conn, "WALLAPOP", "direct_chollo", 90,
                                             seller={"seller_reviews_count": None})[0])
        self.assertFalse(ce.decide_alert_push(self.conn, "WALLAPOP", "direct_chollo", 90,
                                              seller={"seller_reviews_count": 0})[0])


if __name__ == "__main__":
    unittest.main()
