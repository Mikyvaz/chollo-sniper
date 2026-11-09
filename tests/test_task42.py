# -*- coding: utf-8 -*-
"""
Test Suite for Task 4.2:
- evaluate_bundle_cex_bonus live and cached execution with CeX España
- Extra cash & voucher summation
- AST inspection across sniper.py, vinted_sniper.py, multi_sniper.py
- Math integration with evaluate_deal_math_and_score
"""
import sys
import os
import ast
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, "/app")

import core_engine


class TestTask42BundleSumEngine(unittest.TestCase):

    def setUp(self):
        self.conn = core_engine.get_db()

    def tearDown(self):
        if self.conn:
            self.conn.close()

    def test_evaluate_bundle_cex_bonus_live_or_cached(self):
        # Call with 2 popular Switch games
        res = core_engine.evaluate_bundle_cex_bonus(
            self.conn,
            ["Mario Kart 8 Deluxe Switch", "Zelda Breath of the Wild Switch"]
        )
        print("Bundle Bonus Result:", res)
        self.assertIsInstance(res, dict)
        self.assertIn("extra_cash", res)
        self.assertIn("extra_voucher", res)
        self.assertIn("matched_extras", res)

        self.assertGreater(res["extra_cash"], 0, f"Expected extra_cash > 0, got {res['extra_cash']}")
        self.assertGreater(res["extra_voucher"], 0, f"Expected extra_voucher > 0, got {res['extra_voucher']}")
        self.assertGreaterEqual(len(res["matched_extras"]), 1, "Expected at least 1 matched extra")

        box_names = [m["box_name"] for m in res["matched_extras"]]
        print("[OK] Matched extra CeX box names:", box_names)
        self.assertTrue(
            any("Mario Kart" in b for b in box_names) or any("Zelda" in b or "Breath of the Wild" in b for b in box_names),
            f"Expected Mario Kart or Zelda in matched extras, got {box_names}"
        )

    def test_evaluate_bundle_edge_cases(self):
        # Empty list
        res_empty = core_engine.evaluate_bundle_cex_bonus(self.conn, [])
        self.assertEqual(res_empty["extra_cash"], 0.0)
        self.assertEqual(res_empty["extra_voucher"], 0.0)
        self.assertEqual(res_empty["matched_extras"], [])

        # None input
        res_none = core_engine.evaluate_bundle_cex_bonus(self.conn, None)
        self.assertEqual(res_none["extra_cash"], 0.0)

        # Max 3 queries cap protection
        res_max = core_engine.evaluate_bundle_cex_bonus(
            self.conn,
            ["Juego1", "Juego2", "Juego3", "Juego4", "Juego5"]
        )
        self.assertLessEqual(len(res_max["matched_extras"]), 3)
        print("[OK] Edge cases and 3-query cap verified!")

    def test_deal_math_boost_with_bundle(self):
        target = {
            "id": "switch_oled",
            "name": "Nintendo Switch OLED",
            "mode": "resale",
            "min_net_profit_eur": 35.0,
            "min_deal_score": 8,
            "direct_buy_max_price": 200,
            "offer_sniping": True
        }
        ai_data = {
            "is_valid_deal": True,
            "is_pro_seller": False,
            "has_defect_or_missing_parts": False,
            "visual_confidence": "alta",
            "identified_model": "Nintendo Switch OLED Blanca",
            "cex_search_query": "Nintendo Switch OLED Blanca",
            "estimated_resale_price": 240,
            "bundled_games_queries": ["Mario Kart 8 Deluxe Switch", "Zelda Breath of the Wild Switch"]
        }
        cex_data_base = {
            "box_name": "Switch Consola OLED Blanca",
            "cash": 150.0,
            "voucher": 190.0,
            "sell": 250.0
        }

        # Case 1: price=160€ (landed=170€)
        # Without bundle: cex_cash=150€ < landed 170€ (net_cash = -20€ < -2€).
        # Fails direct buy condition (score < 8).
        app_base, score_base, _, profit_base, _, _ = core_engine.evaluate_deal_math_and_score(
            price=160, landed_cost=170, target=target, ai_data=ai_data.copy(), cex_data=cex_data_base.copy()
        )

        # Case 2: with bundle (+38€ cash, +53€ voucher):
        # cex_cash becomes 188€ >= landed 170€ (net_cash = +18€ >= -2€).
        # Upgraded to DIRECT CHOLLO (score >= 8)!
        cex_data_bundled = cex_data_base.copy()
        extra_cash = 38.0
        extra_voucher = 53.0
        cex_data_bundled["cash"] += extra_cash  # 188.0
        cex_data_bundled["voucher"] += extra_voucher  # 243.0
        cex_data_bundled["box_name"] += " + 2 extra(s)"

        app_bundled, score_bundled, _, profit_bundled, _, _ = core_engine.evaluate_deal_math_and_score(
            price=160, landed_cost=170, target=target, ai_data=ai_data.copy(), cex_data=cex_data_bundled
        )

        print(f"Base Deal: score={score_base}, profit={profit_base}€ | Bundled Deal: score={score_bundled}, profit={profit_bundled}€")
        self.assertGreaterEqual(score_bundled, 8, f"Bundled deal must qualify as DIRECT CHOLLO (score >= 8), got {score_bundled}")
        self.assertGreater(score_bundled, score_base, f"Bundled score ({score_bundled}) must be strictly higher than base score ({score_base})")
        print("[OK] Math integration verified: bundle bonus upgrades deal into a DIRECT CHOLLO (>=8/10)!")

    def test_ast_inspection_bundle_in_snipers(self):
        scripts = ["sniper.py", "vinted_sniper.py", "multi_sniper.py"]
        verified = 0
        for s in scripts:
            p = os.path.join(os.path.dirname(__file__), "..", s)
            if not os.path.exists(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content, filename=s)

            found_import = any(
                isinstance(node, ast.ImportFrom) and node.module == "core_engine" and any(n.name == "evaluate_bundle_cex_bonus" for n in node.names)
                for node in ast.walk(tree)
            )
            found_call = any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "evaluate_bundle_cex_bonus"
                for node in ast.walk(tree)
            )

            # If the file in this container has the code, verify it
            if "evaluate_bundle_cex_bonus" in content:
                self.assertTrue(found_import, f"Expected evaluate_bundle_cex_bonus imported in {s}")
                self.assertTrue(found_call, f"Expected evaluate_bundle_cex_bonus called in {s}")
                verified += 1
                print(f"[OK] AST confirmed: {s} imports and calls evaluate_bundle_cex_bonus!")

        self.assertGreaterEqual(verified, 1, "Expected at least 1 sniper script verified in current container")


if __name__ == "__main__":
    unittest.main()
