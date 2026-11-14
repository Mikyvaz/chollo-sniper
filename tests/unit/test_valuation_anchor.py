"""T3.3: valoraciones acotadas y confianza separada de la inspección visual."""
import _harness  # noqa: F401
import json
import sqlite3
import unittest
from unittest import mock

import core_engine as ce


def ai(price=400, model="Modelo de prueba"):
    return {"is_valid_deal": True, "visual_confidence": "alta", "identified_model": model,
            "estimated_resale_price": price}


class TestValuationAnchor(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        with mock.patch.object(ce.threading, "Thread"):
            ce.init_db(self.conn)
        self.addCleanup(self.conn.close)
        self.target = {"id": "test", "max_price": 500, "offer_sniping": True}

    def evaluate(self, data, cex=None, price=100, landed=100, target=None):
        return ce.evaluate_deal_math_and_score(price, landed, target or self.target, data, cex,
                                               config={"min_net_profit_eur": 35}, conn=self.conn)

    def test_ai_400_cex_300_is_capped_at_255(self):
        data = ai()
        result = self.evaluate(data, {"sell": 300, "cash": 170})
        self.assertEqual(result[2], 255)
        self.assertEqual(data["valuation"]["anchor"], "cex_sell")
        self.assertEqual(data["valuation_confidence"], "alta")
        self.assertEqual(data["valuation"]["ai_est"], 400)

    def test_without_anchors_keeps_ai_with_low_confidence(self):
        data = ai(200)
        self.assertEqual(self.evaluate(data)[2], 200)
        self.assertEqual(data["valuation_confidence"], "baja")
        self.assertEqual(data["valuation"]["anchor"], "ai_only")

    def test_uses_p75_only_with_five_recent_submodel_observations(self):
        for value in (100, 120, 140, 160, 180):
            self.conn.execute("INSERT INTO price_history (radar_name, price) VALUES ('test:modelo de prueba', ?)", (value,))
        # Otro radar y datos viejos no deben inflar el percentil.
        self.conn.execute("INSERT INTO price_history (radar_name, price) VALUES ('other:modelo de prueba', 999)")
        self.conn.execute("INSERT INTO price_history (radar_name, price, recorded_at) "
                          "VALUES ('test:modelo de prueba', 999, datetime('now','-15 days'))")
        self.conn.commit()
        data = ai()
        self.assertEqual(self.evaluate(data)[2], 184)
        self.assertEqual(data["valuation_confidence"], "media")
        self.assertEqual(data["valuation"]["anchor"], "asking_p75")
        self.conn.execute("DELETE FROM price_history WHERE price=100")
        self.conn.commit()
        data = ai()
        self.assertEqual(self.evaluate(data)[2], 400)
        self.assertEqual(data["valuation_confidence"], "baja")

    def test_switch_oled_offer_still_has_at_least_35_profit(self):
        data = ai(145, "Nintendo Switch OLED")
        target = {"id": "switch_oled", "direct_buy_max_price": 98, "offer_sniping": True}
        result = self.evaluate(data, {"cash": 94, "voucher": 110, "sell": 165},
                               price=110, landed=118.99, target=target)
        self.assertTrue(result[0])
        self.assertEqual(result[-1], "OPORTUNIDAD_OFERTA")
        self.assertGreaterEqual(result[3], 35)

    def test_low_confidence_push_requires_extra_margin_preserving_offer_floor(self):
        with mock.patch.object(ce, "get_notification_policy", return_value={"push_min_profit_eur": 40,
                              "push_offer_min_profit_eur": 80, "push_max_per_hour": 4}):
            args = dict(conn=self.conn, platform="WALLAPOP", seller={"seller_reviews_count": 10},
                        valuation_confidence="baja")
            self.assertFalse(ce.decide_alert_push(deal_type="direct_chollo", net_profit=59, **args)[0])
            self.assertTrue(ce.decide_alert_push(deal_type="direct_chollo", net_profit=60, **args)[0])
            self.assertFalse(ce.decide_alert_push(deal_type="offer_opportunity", net_profit=79, **args)[0])

    def test_context_persists_inputs_and_result(self):
        data = ai()
        self.evaluate(data, {"sell": 300, "cash": 170})
        self.conn.execute("INSERT INTO deals (deal_id) VALUES ('anchor-test')")
        ce.save_alert_context(self.conn, "anchor-test", ai_data=data)
        context = json.loads(self.conn.execute("SELECT alert_context_json FROM deals WHERE deal_id='anchor-test'").fetchone()[0])
        self.assertEqual(context["valuation"], {"ai_est": 400, "cex_sell": 300, "cex_cash": 170,
                                              "anchor": "cex_sell", "optimal": 255, "confidence": "alta"})

    def test_personal_savings_policy_is_preserved(self):
        data = ai(400)
        result = self.evaluate(data, {"sell": 300, "cash": 170}, target=dict(self.target, mode="personal"))
        self.assertEqual(result[2], 400)

    def test_discord_shows_valuation_confidence_and_capped_price(self):
        import test_alert_payload as alert_fixture
        case = alert_fixture.TestAlertPayload()
        case.setUp()
        try:
            payload = case._send_discord(ai_data=dict(ai(), valuation_confidence="baja"), optimal_price=255)
        finally:
            case.doCleanups()
        fields = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
        self.assertIn("Valoración P2P: confianza baja", fields["Veredicto IA"])
        self.assertIn("255 €", fields["CeX"])
        self.assertNotIn("400 €", fields["CeX"])
