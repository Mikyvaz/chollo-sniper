import tests.unit._harness  # noqa: F401
import json
import os
import unittest


class TestVintedShipping(unittest.TestCase):
    def test_config_json_has_vinted_shipping_eur(self):
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.json",
        )
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)

        table = cfg.get("vinted_shipping_eur")
        self.assertIsInstance(table, dict)
        expected = {
            "ES": 3.29,
            "PT": 5.5,
            "FR": 7.5,
            "IT": 7.5,
            "BE": 8.5,
            "NL": 8.5,
            "default": 8.0,
        }
        for k, v in expected.items():
            self.assertAlmostEqual(float(table[k]), v, places=2)

    def test_parse_vinted_price_by_country(self):
        import vinted_sniper

        item = {"price": 100.0}
        # buyer_fee for 100€ = 100*0.05 + 0.70 = 5.70
        cases = [
            ("FR", 100.0 + 5.70 + 7.5),
            ("fr", 100.0 + 5.70 + 7.5),
            ("ES", 100.0 + 5.70 + 3.29),
            ("PT", 100.0 + 5.70 + 5.5),
            ("IT", 100.0 + 5.70 + 7.5),
            ("BE", 100.0 + 5.70 + 8.5),
            ("NL", 100.0 + 5.70 + 8.5),
            ("DE", 100.0 + 5.70 + 8.0),
            ("", 100.0 + 5.70 + 8.0),
            (None, 100.0 + 5.70 + 8.0),
        ]
        for country, expected_landed in cases:
            base, landed, fees = vinted_sniper.parse_vinted_price(item, country=country)
            self.assertAlmostEqual(base, 100.0, places=2)
            self.assertAlmostEqual(landed, expected_landed, places=2, msg=f"country={country!r}")
            self.assertAlmostEqual(fees, round(expected_landed - 100.0, 2), places=2)

    def test_custom_shipping_table_override_and_item_country_fallback(self):
        import vinted_sniper

        custom = {"ES": 2.99, "FR": 9.0, "default": 10.0}
        _, landed_fr, _ = vinted_sniper.parse_vinted_price({"price": 100.0}, country="FR", shipping_table=custom)
        self.assertAlmostEqual(landed_fr, 100.0 + 5.70 + 9.0, places=2)

        # Fallback to item["country_code"] when country arg omitted
        _, landed_it, _ = vinted_sniper.parse_vinted_price({"price": 100.0, "country_code": "IT"})
        self.assertAlmostEqual(landed_it, 100.0 + 5.70 + 7.5, places=2)

    def test_vinted_sniper_recalculates_after_fetch_item_details(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "vinted_sniper.py"), encoding="utf-8") as f:
            src = f.read()

        idx_details = src.index("vinted.fetch_item_details(")
        idx_recalc = src.index("parse_vinted_price(", idx_details)
        idx_prescreen = src.index("prescreen_item_text(", idx_details)
        self.assertLess(idx_details, idx_recalc)
        self.assertLess(idx_recalc, idx_prescreen)
        self.assertIn("country=_country_code", src[idx_recalc:idx_prescreen])


if __name__ == "__main__":
    unittest.main()
