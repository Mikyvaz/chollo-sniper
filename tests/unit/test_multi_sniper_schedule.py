import tests.unit._harness  # noqa: F401
import copy
import json
import os
import random
import unittest


class TestMultiSniperSchedule(unittest.TestCase):
    def test_real_config_json_store_intervals_and_overrides(self):
        import multi_sniper

        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.json",
        )
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)

        stores_cfg = cfg.get("stores", {})
        self.assertTrue(stores_cfg["milanuncios"]["enabled"])
        self.assertTrue(stores_cfg["cashconverters"]["enabled"])
        self.assertEqual(stores_cfg["cashconverters"]["interval_s"], 600)
        self.assertTrue(stores_cfg["todocoleccion"]["enabled"])
        self.assertEqual(stores_cfg["todocoleccion"]["interval_s"], 1800)
        self.assertTrue(stores_cfg["ebay"]["enabled"])
        self.assertEqual(stores_cfg["ebay"]["interval_s"], 300)

        by_key = {st["key"]: st for st in multi_sniper.STORE_PIPELINE}
        self.assertEqual(multi_sniper.get_store_interval_s(cfg, by_key["cashconverters"]), 600.0)
        self.assertEqual(multi_sniper.get_store_interval_s(cfg, by_key["todocoleccion"]), 1800.0)
        self.assertEqual(multi_sniper.get_store_interval_s(cfg, by_key["ebay"]), 300.0)
        self.assertEqual(
            multi_sniper.get_store_interval_s(cfg, by_key["milanuncios"]),
            float(multi_sniper.SCAN_INTERVAL),
        )

        # Fallback to STORE_PIPELINE defaults when config.stores lacks interval_s
        empty_cfg = {"stores": {"cashconverters": {"enabled": True}}}
        self.assertEqual(multi_sniper.get_store_interval_s(empty_cfg, by_key["cashconverters"]), 600.0)
        self.assertEqual(multi_sniper.get_store_interval_s(empty_cfg, by_key["todocoleccion"]), 1800.0)
        self.assertEqual(multi_sniper.get_store_interval_s(empty_cfg, by_key["ebay"]), 300.0)

        # Override from config takes precedence over STORE_PIPELINE default
        override_cfg = {"stores": {"ebay": {"enabled": True, "interval_s": 420}}}
        self.assertEqual(multi_sniper.get_store_interval_s(override_cfg, by_key["ebay"]), 420.0)

    def test_compute_next_fetch_at_jitter_bounds(self):
        import multi_sniper

        rng = random.Random(42)
        now = 1000.0
        delays = []
        for _ in range(200):
            nxt, delay = multi_sniper.compute_next_fetch_at(now, 600.0, rng=rng)
            self.assertAlmostEqual(nxt - now, delay, places=9)
            self.assertGreaterEqual(delay, 600.0 * 0.85)
            self.assertLessEqual(delay, 600.0 * 1.15)
            delays.append(delay)
        self.assertGreater(max(delays) - min(delays), 50.0)

    def test_24h_fake_clock_simulation_round_counts(self):
        import multi_sniper

        cfg = {
            "stores": {
                "milanuncios": {"enabled": True},
                "cashconverters": {"enabled": True, "interval_s": 600},
                "ebay": {"enabled": True, "interval_s": 300},
                "todocoleccion": {"enabled": True, "interval_s": 1800},
            }
        }
        pipeline = copy.deepcopy(multi_sniper.STORE_PIPELINE)
        for st in pipeline:
            st["cooldown_until"] = 0
            st["next_fetch_at"] = 0

        rng = random.Random(123)
        start = 1_700_000_000.0
        now = start
        end = start + 86400.0

        fetch_times = {st["platform"]: [] for st in pipeline}
        while now < end:
            due_plats = {
                st["platform"]
                for st in pipeline
                if st.get("cooldown_until", 0) <= now and st.get("next_fetch_at", 0) <= now
            }
            fetched_plats = set(due_plats)
            for plat in fetched_plats:
                fetch_times[plat].append(now)

            multi_sniper.schedule_stores_after_round(
                pipeline, fetched_plats, due_plats, cfg, now, rng=rng
            )
            next_wake = min(st["next_fetch_at"] for st in pipeline)
            now = max(now + 5.0, next_wake)

        cc_times = fetch_times["CASHCONVERTERS"]
        tc_times = fetch_times["TODOCOLECCION"]
        eb_times = fetch_times["EBAY"]

        # Expected rounds in 24h: CC ~144 (>85% less than 1051), TC ~48 (>90% less than 592), eBay ~288
        self.assertGreaterEqual(len(cc_times), 120)
        self.assertLessEqual(len(cc_times), 170)
        self.assertLess(len(cc_times), 1051 * 0.15)

        self.assertGreaterEqual(len(tc_times), 40)
        self.assertLessEqual(len(tc_times), 58)
        self.assertLess(len(tc_times), 592 * 0.10)

        self.assertGreaterEqual(len(eb_times), 245)
        self.assertLessEqual(len(eb_times), 340)

        # Verify every inter-fetch interval stays within configured interval_s ±15%
        for times, base_s in ((cc_times, 600.0), (tc_times, 1800.0), (eb_times, 300.0)):
            deltas = [b - a for a, b in zip(times, times[1:])]
            self.assertTrue(deltas)
            for d in deltas:
                self.assertGreaterEqual(d, base_s * 0.85 - 1e-6)
                self.assertLessEqual(d, base_s * 1.15 + 1e-6)


if __name__ == "__main__":
    unittest.main()
