"""T3.2: referencia CeX local anterior a la IA y decisiones conservadoras."""
import _harness  # noqa: F401
import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest import mock

import core_engine as ce

PS5 = {"id": "ps5_consoles_gta6", "direct_buy_max_price": 350, "mode": "resale"}


class TestPreCex(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        with mock.patch.object(ce.threading, "Thread"):
            ce.init_db(self.conn)
        self.addCleanup(self.conn.close)
        self.add("ps5", "Playstation 5 Slim, 1TB, Blanca, Sin Caja", "PS5 Consolas", 300, 450)
        self.add("digital", "Playstation 5 Slim Digital, 825GB, Blanca, Sin Caja", "PS5 Consolas", 230, 380)
        self.add("switch", "Nintendo Switch OLED Blanca", "Switch Consolas", 100, 180)

    def add(self, box, name, category, cash, sell):
        self.conn.execute("INSERT INTO cex_catalog_mirror "
                          "(box_id, box_name, category_name, cash_price, voucher_price, sell_price) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (box, name, category, cash, cash+20, sell))
        self.conn.commit()

    def estimate(self, title, desc="", target=PS5):
        with mock.patch.object(ce, "fetch_cex_live", side_effect=AssertionError("sin red")):
            return ce.estimate_cex_from_title(self.conn, title, desc, target)

    def test_aliases_and_commercial_words(self):
        for title in ("Vendo PS5 slim como nueva urgente envío", "PlayStation 5 Slim perfecto estado",
                      "Sony PlayStation 5 Slim 1 TB con 2 mandos"):
            r = self.estimate(title)
            self.assertIn(r["confidence"], ("media", "alta"))
            self.assertEqual(r["cash"], 300)
        self.assertEqual(ce.cex_product_family("Deck 512GB"), "steam_deck")
        r = self.estimate("Switch OLED como nueva", target={"id": "switch_oled"})
        self.assertEqual(r["cash"], 100)

    def test_context_digital_and_missing_fresh_data(self):
        self.assertEqual(self.estimate("PS5 Slim digital")["cash"], 230)
        self.conn.execute("UPDATE cex_catalog_mirror SET last_synced=datetime('now','-37 hours')")
        self.conn.commit()
        self.assertEqual(self.estimate("PS5 slim")["confidence"], "baja")

    def test_ambiguous_family_or_one_model_token_fail_open(self):
        self.add("ps4", "Playstation 4 Slim, 1TB, Sin Caja", "PS4 Consolas", 80, 150)
        self.assertEqual(self.estimate("Playstation Slim", target={})["confidence"], "baja")
        self.assertEqual(self.estimate("OLED perfecto", target={})["confidence"], "baja")

    def test_ninety_euro_ps5_new_seller_triggers_before_ai(self):
        ref = self.estimate("PS5 slim")
        risk = ce.evaluate_fraud_risk("PS5 slim", "Funciona perfecto", 90, radar_id=PS5["id"],
                                     conn=self.conn, platform="WALLAPOP", item_id="test",
                                     seller={"seller_reviews_count": 0, "seller_profile_status": "ok"},
                                     cex_cash=ref["cash"])
        self.assertIn("ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER", [h["code"] for h in risk["hits"]])
        self.assertTrue(risk["should_block"])

    def test_direct_zone_requires_both_bad_margins_and_actual_landed_cost(self):
        target = {"id": "switch_oled", "direct_buy_max_price": 300}
        item = {"title": "Switch OLED", "price": 120, "landed_cost": 145}
        self.assertFalse(ce.pre_gate_cex_offer_margin(self.conn, item, target, {"min_net_profit_eur": 35})[0])
        item["landed_cost"] = 115
        self.assertTrue(ce.pre_gate_cex_offer_margin(self.conn, item, target, {})[0])
        item["landed_cost"] = 145
        for extra in ({"description": "lote con dos juegos"}, {}):
            t = dict(target, mode="personal") if not extra else target
            self.assertTrue(ce.pre_gate_cex_offer_margin(self.conn, dict(item, **extra), t, {})[0])

    def test_prompt_reference_also_changes_evaluation_cache_key(self):
        def capture(cash):
            ref = {"confidence": "media", "cash": cash, "sell": 450, "box_name": "PS5 Slim"}
            with mock.patch.object(ce, "GEMINI_API_KEY", "fake-key"), \
                    mock.patch.object(ce, "estimate_cex_from_title", return_value=ref), \
                    mock.patch.object(ce, "ai_gateway_call", return_value={"is_valid_deal": False}) as gateway, \
                    mock.patch.object(ce, "build_robust_cache_key", wraps=ce.build_robust_cache_key) as key, \
                    redirect_stdout(io.StringIO()):
                ce.analyze_and_generate_kit("WALLAPOP", "PS5 Slim", "perfecta", 90, 100, False, PS5)
            return gateway.call_args.kwargs["prompt"], key.call_args
        prompt, key1 = capture(300)
        _, key2 = capture(310)
        self.assertIn("Referencia CeX: efectivo 300", prompt)
        self.assertNotEqual(key1, key2)
