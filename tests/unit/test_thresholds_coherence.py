import tests.unit._harness  # noqa: F401
import inspect
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import core_engine as ce


def _make_mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ce.init_db(conn)
    return conn


class TestResolveMinProfit(unittest.TestCase):
    def test_target_overrides_config_and_default(self):
        cfg = {"min_net_profit_eur": 35}
        self.assertEqual(ce.resolve_min_profit({}, config=cfg), 35.0)
        self.assertEqual(ce.resolve_min_profit({"min_profit_eur": 50}, config=cfg), 50.0)
        self.assertEqual(ce.resolve_min_profit({"min_net_profit_eur": 45}, config=cfg), 45.0)
        self.assertEqual(ce.resolve_min_profit({"min_profit": 22}, config=cfg), 22.0)
        self.assertEqual(ce.resolve_min_profit({}, config={}), 35.0)

    def test_reads_config_when_config_is_none(self):
        with patch.object(ce, "load_raw_config", return_value={"min_net_profit_eur": 42}):
            self.assertEqual(ce.resolve_min_profit({}), 42.0)


class TestThresholdsCoherence(unittest.TestCase):
    def test_pregate_and_math_use_global_35_when_target_has_no_override(self):
        conn = _make_mem_db()
        conn.execute(
            """
            INSERT INTO cex_catalog_mirror (box_id, box_name, category_name, cash_price, voucher_price, sell_price)
            VALUES ('SW_TEST_1', 'Nintendo Switch OLED Blanca', 'consolas', 100.0, 125.0, 165.0)
            """
        )
        conn.commit()

        target = {
            "id": "switch_oled",
            "direct_buy_max_price": 80.0,
            "max_price": 120.0,
            "offer_sniping": True,
        }
        item = {
            "title": "Nintendo Switch OLED Blanca",
            "description": "Consola en buen estado",
            "price": 100.0,
            "platform": "WALLAPOP",
        }
        cfg = {"min_net_profit_eur": 35.0, "max_haggle_discount_pct": 0.25}

        # min_achievable_buy = 75 -> landed ~ 84 -> max_exit (100) - landed (~84) = ~16 < 35*0.7 (24.5)
        # Con el antiguo 15.0 hardcoded habría pasado (16 >= 10.5); con 35.0 debe rechazarse.
        with patch.object(ce, "load_raw_config", return_value=cfg):
            ok_pg, reason_pg = ce.pre_gate_cex_offer_margin(conn, item, target, config=cfg)
            self.assertFalse(ok_pg, f"pre_gate debe usar min_profit=35 de config, devolvió {reason_pg}")

            # En math engine, sin min_profit explícito en target ni parámetro, debe exigir 35 €
            # optimal_price=100 -> net_p2p=92. Con req_margin=35 -> target_offer=57 (43% dto > 35% max) -> rechazado
            # (con 15 habría sido target_offer=77, 23% dto -> aprobado).
            ai_data = {
                "is_valid_deal": True,
                "is_pro_seller": False,
                "has_defect_or_missing_parts": False,
                "visual_confidence": "alta",
                "identified_model": "Nintendo Switch OLED",
                "estimated_resale_price": 100.0,
            }
            approved, *_ = ce.evaluate_deal_math_and_score(
                price=100.0,
                landed_cost=100.0,
                target=target,
                ai_data=ai_data,
                cex_data={"cash": 70.0, "voucher": 90.0, "sell": 130.0},
            )
            self.assertFalse(approved, "evaluate_deal_math_and_score debe exigir min_net_profit_eur=35 de config")

    def test_target_min_profit_eur_50_overrides_global_in_both_gates(self):
        conn = _make_mem_db()
        conn.execute(
            """
            INSERT INTO cex_catalog_mirror (box_id, box_name, category_name, cash_price, voucher_price, sell_price)
            VALUES ('SW_TEST_2', 'Nintendo Switch OLED Negra', 'consolas', 112.0, 140.0, 175.0)
            """
        )
        conn.commit()

        target = {
            "id": "switch_oled",
            "direct_buy_max_price": 80.0,
            "max_price": 120.0,
            "min_profit_eur": 50.0,
            "offer_sniping": True,
        }
        item = {
            "title": "Nintendo Switch OLED Negra",
            "description": "Consola sola",
            "price": 100.0,
            "platform": "WALLAPOP",
        }
        cfg = {"min_net_profit_eur": 35.0, "max_haggle_discount_pct": 0.25}

        # 112 - ~84 = ~28: pasaría con 35 (28 >= 24.5), pero con 50*0.7=35 debe rechazarse
        with patch.object(ce, "load_raw_config", return_value=cfg):
            ok_pg, reason_pg = ce.pre_gate_cex_offer_margin(conn, item, target, config=cfg)
            self.assertFalse(ok_pg, f"pre_gate debe respetar min_profit_eur=50 del target: {reason_pg}")

            # En math engine: optimal_price=120 -> net_p2p=110.4 -> con 35 daría offer=75 (25% dto, ok),
            # pero con target.min_profit_eur=50 exige offer=60 (40% dto > 35% max) -> rechazado.
            ai_data = {
                "is_valid_deal": True,
                "is_pro_seller": False,
                "has_defect_or_missing_parts": False,
                "visual_confidence": "alta",
                "identified_model": "Nintendo Switch OLED",
                "estimated_resale_price": 120.0,
            }
            approved, *_ = ce.evaluate_deal_math_and_score(
                price=100.0,
                landed_cost=100.0,
                target=target,
                ai_data=ai_data,
                cex_data={"cash": 70.0, "voucher": 90.0, "sell": 150.0},
            )
            self.assertFalse(approved, "evaluate_deal_math_and_score debe respetar min_profit_eur=50 del target")

    def test_passes_filters_uses_load_config_multiplier_not_hardcoded_130(self):
        cfg_data = {
            "offer_scan_multiplier": 1.20,
            "targets": [
                {
                    "id": "switch_oled",
                    "name": "Switch OLED",
                    "min_price": 50,
                    "max_price": 100,
                    "offer_sniping": True,
                    "enabled": True,
                }
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(cfg_data, f)
            tmp_cfg = f.name

        try:
            with patch.object(ce, "CONFIG_PATH", tmp_cfg):
                loaded = ce.load_config(expand_for_offers=True)
            t = loaded["targets"][0]
            self.assertEqual(t["max_price"], 120)
            self.assertTrue(
                ce.passes_filters("Consola Nintendo Switch OLED blanca", "En buen estado", 115.0, t, [])
            )
            self.assertFalse(
                ce.passes_filters("Consola Nintendo Switch OLED blanca", "En buen estado", 125.0, t, [])
            )
        finally:
            os.remove(tmp_cfg)

    def test_no_duplicate_130_outside_load_config(self):
        passes_src = inspect.getsource(ce.passes_filters)
        self.assertNotIn("1.30", passes_src)

        core_path = inspect.getsourcefile(ce)
        with open(core_path, encoding="utf-8") as f:
            lines = f.readlines()
        load_cfg_src = inspect.getsource(ce.load_config)
        _, load_start = inspect.getsourcelines(ce.load_config)
        load_end = load_start + len(load_cfg_src.splitlines()) - 1
        bad_lines = [
            (i, ln.strip())
            for i, ln in enumerate(lines, start=1)
            if "1.30" in ln and not (load_start <= i <= load_end)
        ]
        self.assertEqual(bad_lines, [], f"1.30 solo debe aparecer en load_config, encontrado en: {bad_lines}")


if __name__ == "__main__":
    unittest.main()
