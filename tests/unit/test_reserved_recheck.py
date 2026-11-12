import tests.unit._harness  # noqa: F401
import sqlite3
import unittest
from unittest.mock import MagicMock


class TestReservedRecheck(unittest.TestCase):
    def setUp(self):
        import core_engine

        self.conn = sqlite3.connect(":memory:")
        core_engine.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_init_db_adds_state_column_and_migrates_legacy_table(self):
        import core_engine

        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(market_seen_items)").fetchall()]
        self.assertIn("state", cols)

        legacy = sqlite3.connect(":memory:")
        try:
            legacy.execute("""
                CREATE TABLE market_seen_items (
                    platform TEXT,
                    item_id TEXT,
                    title TEXT,
                    price REAL,
                    seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (platform, item_id)
                )
            """)
            legacy.execute(
                "INSERT INTO market_seen_items (platform, item_id, title, price) VALUES ('WALLAPOP', 'old1', 'PS5', 300.0)"
            )
            legacy.commit()
            core_engine.init_db(legacy)

            lcols = [r[1] for r in legacy.execute("PRAGMA table_info(market_seen_items)").fetchall()]
            self.assertIn("state", lcols)
            row = legacy.execute(
                "SELECT state FROM market_seen_items WHERE platform='WALLAPOP' AND item_id='old1'"
            ).fetchone()
            self.assertEqual(row[0], "seen")
        finally:
            legacy.close()

    def test_check_item_seen_or_dropped_reserved_transitions(self):
        import core_engine

        core_engine.mark_item_seen(self.conn, "WALLAPOP", "w1", "Switch OLED", 180.0, state="reserved")
        row = self.conn.execute(
            "SELECT state FROM market_seen_items WHERE platform='WALLAPOP' AND item_id='w1'"
        ).fetchone()
        self.assertEqual(row[0], "reserved")

        # While still reserved -> treated as already seen
        seen_still, drop_still = core_engine.check_item_seen_or_dropped(
            self.conn, "WALLAPOP", "w1", 180.0, is_reserved_now=True
        )
        self.assertTrue(seen_still)
        self.assertIsNone(drop_still)

        # Reservation dropped -> re-enters funnel
        seen_unres, drop_unres = core_engine.check_item_seen_or_dropped(
            self.conn, "WALLAPOP", "w1", 180.0, is_reserved_now=False
        )
        self.assertFalse(seen_unres)
        self.assertIsNone(drop_unres)

        # Once evaluated and marked seen (default state='seen') -> row updated to 'seen'
        core_engine.mark_item_seen(self.conn, "WALLAPOP", "w1", "Switch OLED", 180.0)
        row2 = self.conn.execute(
            "SELECT state FROM market_seen_items WHERE platform='WALLAPOP' AND item_id='w1'"
        ).fetchone()
        self.assertEqual(row2[0], "seen")

        seen_after, _ = core_engine.check_item_seen_or_dropped(
            self.conn, "WALLAPOP", "w1", 180.0, is_reserved_now=False
        )
        self.assertTrue(seen_after)

        # Price drops still reopen a 'seen' item
        seen_drop, drop_info = core_engine.check_item_seen_or_dropped(
            self.conn, "WALLAPOP", "w1", 140.0, min_drop_pct=15, min_drop_eur=10, is_reserved_now=False
        )
        self.assertFalse(seen_drop)
        self.assertIsNotNone(drop_info)
        self.assertEqual(drop_info["new_price"], 140.0)

    def test_sniper_reserved_then_unreserved_flow(self):
        import core_engine
        import sniper

        ai_mock = MagicMock(return_value={"is_valid_deal": False})

        def _run_pass(item):
            item_id = item["id"]
            title = item["title"]
            price = float(item["price"])
            is_reserved_now = bool((item.get("reserved") or {}).get("flag", False))
            should_skip, _ = sniper.handle_seen_and_reserved(
                self.conn, item_id, title, price, is_reserved_now
            )
            if should_skip:
                return "skipped"
            ai_mock(item_id)
            core_engine.mark_item_seen(self.conn, "WALLAPOP", item_id, title, price)
            return "evaluated"

        # Pass 1: arrives reserved -> saved as 'reserved', AI not called
        res1 = _run_pass({"id": "w2", "title": "Steam Deck", "price": 250.0, "reserved": {"flag": True}})
        self.assertEqual(res1, "skipped")
        self.assertEqual(ai_mock.call_count, 0)
        st1 = self.conn.execute(
            "SELECT state FROM market_seen_items WHERE platform='WALLAPOP' AND item_id='w2'"
        ).fetchone()[0]
        self.assertEqual(st1, "reserved")

        # Pass 2: still reserved -> skipped, AI not called
        res2 = _run_pass({"id": "w2", "title": "Steam Deck", "price": 250.0, "reserved": {"flag": True}})
        self.assertEqual(res2, "skipped")
        self.assertEqual(ai_mock.call_count, 0)

        # Pass 3: reservation dropped -> enters funnel, AI called, state becomes 'seen'
        res3 = _run_pass({"id": "w2", "title": "Steam Deck", "price": 250.0, "reserved": {"flag": False}})
        self.assertEqual(res3, "evaluated")
        self.assertEqual(ai_mock.call_count, 1)
        st3 = self.conn.execute(
            "SELECT state FROM market_seen_items WHERE platform='WALLAPOP' AND item_id='w2'"
        ).fetchone()[0]
        self.assertEqual(st3, "seen")

        # Pass 4: seen at same price -> skipped, AI not called again
        res4 = _run_pass({"id": "w2", "title": "Steam Deck", "price": 250.0, "reserved": {"flag": False}})
        self.assertEqual(res4, "skipped")
        self.assertEqual(ai_mock.call_count, 1)

    def test_reserved_over_48h_unreserved_with_and_without_price_drop_not_discarded_as_stale(self):
        import core_engine
        import sniper

        # Item A: reserved >48h ago at 150€, reappears unreserved at 100€ -> keeps price_drop_info, not stale
        core_engine.mark_item_seen(self.conn, "WALLAPOP", "w_res_drop", "Switch OLED", 150.0, state="reserved")
        self.conn.execute(
            "UPDATE market_seen_items SET seen_at = datetime('now', '-50 hours') "
            "WHERE platform='WALLAPOP' AND item_id='w_res_drop'"
        )
        # Item B: reserved >48h ago at 150€, reappears unreserved at 150€ -> price_drop_info=None, still not stale
        core_engine.mark_item_seen(self.conn, "WALLAPOP", "w_res_same", "Switch OLED 2", 150.0, state="reserved")
        self.conn.execute(
            "UPDATE market_seen_items SET seen_at = datetime('now', '-50 hours') "
            "WHERE platform='WALLAPOP' AND item_id='w_res_same'"
        )
        self.conn.commit()

        skip_a, drop_a = sniper.handle_seen_and_reserved(
            self.conn, "w_res_drop", "Switch OLED", 100.0, is_reserved_now=False, drop_pct=15, drop_eur=10
        )
        self.assertFalse(skip_a)
        self.assertIsNotNone(drop_a)
        self.assertEqual(drop_a["old_price"], 150.0)
        self.assertEqual(drop_a["new_price"], 100.0)
        self.assertEqual(drop_a["drop_eur"], 50.0)
        self.assertEqual(drop_a["drop_pct"], 33)
        self.assertFalse(
            sniper.should_skip_stale_listing(self.conn, "w_res_drop", age_days=2.2, price_drop_info=drop_a)
        )

        skip_b, drop_b = sniper.handle_seen_and_reserved(
            self.conn, "w_res_same", "Switch OLED 2", 150.0, is_reserved_now=False, drop_pct=15, drop_eur=10
        )
        self.assertFalse(skip_b)
        self.assertIsNone(drop_b)
        self.assertFalse(
            sniper.should_skip_stale_listing(self.conn, "w_res_same", age_days=2.2, price_drop_info=drop_b)
        )

        # Control: once marked 'seen', an old item without price drop IS discarded by age > 1.0
        core_engine.mark_item_seen(self.conn, "WALLAPOP", "w_res_same", "Switch OLED 2", 150.0, state="seen")
        self.assertTrue(
            sniper.should_skip_stale_listing(self.conn, "w_res_same", age_days=2.2, price_drop_info=None)
        )


if __name__ == "__main__":
    unittest.main()
