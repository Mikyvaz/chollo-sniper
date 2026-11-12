import tests.unit._harness  # noqa: F401
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import core_engine as ce
import outcome_tracker as ot


class TestFraudSampling(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = os.path.join(self.tmpdir.name, "fraud_sample.db")
        self.conn = sqlite3.connect(self.db_path)
        self.addCleanup(self.conn.close)
        ce.init_db(self.conn)
        ot.ensure_outcome_tables(self.conn)

    def test_sampling_rate_10pct_over_1000_ids_and_deterministic(self):
        hits_first = [ce.should_sample_fraud_block("WALLAPOP", f"item_{i}") for i in range(1000)]
        hits_second = [ce.should_sample_fraud_block("wallapop", f"item_{i}") for i in range(1000)]
        self.assertEqual(hits_first, hits_second)
        count = sum(1 for h in hits_first if h)
        self.assertGreaterEqual(count, 80, f"Muestra muy baja: {count}/1000")
        self.assertLessEqual(count, 120, f"Muestra muy alta: {count}/1000")

    def test_record_fraud_sample_inserts_sampled_only_and_is_idempotent(self):
        sampled_id = next(f"id_{i}" for i in range(200) if ce.should_sample_fraud_block("VINTED", f"id_{i}"))
        skipped_id = next(f"id_{i}" for i in range(200) if not ce.should_sample_fraud_block("VINTED", f"id_{i}"))

        self.assertFalse(
            ce.record_fraud_sample(
                self.conn, "VINTED", skipped_id, seller_id="s1", risk=5,
                codes=[{"code": "ZERO_REP"}], item_url=f"https://www.vinted.es/items/{skipped_id}",
            )
        )
        self.assertTrue(
            ce.record_fraud_sample(
                self.conn, "VINTED", sampled_id, seller_id="s2", risk=6,
                codes=[{"code": "ZERO_REP"}, {"code": "NEW_ACCOUNT"}],
                item_url=f"https://www.vinted.es/items/{sampled_id}",
            )
        )
        # Idempotente si vuelve a entrar el mismo (platform, item_id)
        ce.record_fraud_sample(
            self.conn, "VINTED", sampled_id, seller_id="s2", risk=6,
            codes=[{"code": "ZERO_REP"}], item_url=f"https://www.vinted.es/items/{sampled_id}",
        )

        rows = self.conn.execute(
            "SELECT platform, item_id, seller_id, risk, codes, checked_at FROM fraud_samples"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:5], ("VINTED", sampled_id, "s2", 6, "ZERO_REP,NEW_ACCOUNT"))
        self.assertIsNone(rows[0][5])

    def test_outcome_tracker_checks_pending_fraud_samples_after_24h(self):
        self.conn.execute(
            """
            INSERT INTO fraud_samples (platform, item_id, seller_id, item_url, risk, codes, created_at)
            VALUES ('WALLAPOP', 'old_1', 'sell_1', 'https://es.wallapop.com/item/old_1', 6, 'ZERO_REP',
                    datetime('now', '-25 hours')),
                   ('WALLAPOP', 'new_1', 'sell_2', 'https://es.wallapop.com/item/new_1', 5, 'ZERO_REP',
                    datetime('now', '-2 hours'))
            """
        )
        self.conn.commit()

        fake_res = {"status": "gone", "http": 404, "price": None, "views": None, "favorites": None, "conversations": None, "detail": ""}
        with mock.patch.dict(ot.CHECKERS, {"WALLAPOP": mock.Mock(return_value=fake_res)}), \
             mock.patch.object(ot, "check_seller", return_value="banned") as seller_mock, \
             mock.patch.object(ot, "_wait_pace"):
            ot._backoff_until.clear()
            checked = ot.check_pending_fraud_samples(self.conn, limit=10)

        self.assertEqual(checked, 1)
        seller_mock.assert_called_once_with("WALLAPOP", "sell_1")
        old_row = self.conn.execute(
            "SELECT item_status, seller_status, checked_at FROM fraud_samples WHERE item_id='old_1'"
        ).fetchone()
        self.assertEqual(old_row[:2], ("gone", "banned"))
        self.assertIsNotNone(old_row[2])

        new_row = self.conn.execute(
            "SELECT checked_at FROM fraud_samples WHERE item_id='new_1'"
        ).fetchone()
        self.assertIsNone(new_row[0])

    def test_outcome_tracker_throttled_leaves_sample_pending(self):
        self.conn.execute(
            """
            INSERT INTO fraud_samples (platform, item_id, seller_id, risk, codes, created_at)
            VALUES ('WALLAPOP', 'thr_1', 'sell_9', 6, 'ZERO_REP', datetime('now', '-26 hours'))
            """
        )
        self.conn.commit()
        with mock.patch.dict(ot.CHECKERS, {"WALLAPOP": mock.Mock(side_effect=ot.Throttled("429"))}), \
             mock.patch.object(ot, "_wait_pace"):
            ot._backoff_until.clear()
            checked = ot.check_pending_fraud_samples(self.conn, limit=10)
        self.assertEqual(checked, 0)
        self.assertGreater(ot._backoff_until.get("WALLAPOP", 0), 0)
        chk = self.conn.execute("SELECT checked_at FROM fraud_samples WHERE item_id='thr_1'").fetchone()[0]
        self.assertIsNone(chk)

    def test_bot_summarize_fraud_samples_compares_blocked_vs_alerted(self):
        import bot as b
        for i, st in enumerate(("banned", "deleted", "banned", "active")):
            self.conn.execute(
                """
                INSERT INTO fraud_samples (platform, item_id, seller_id, risk, codes, item_status, seller_status, checked_at)
                VALUES ('WALLAPOP', ?, ?, 5, 'ZERO_REP', 'gone', ?, datetime('now', '-1 hour'))
                """,
                (f"blk_{i}", f"sb_{i}", st),
            )
        for i, st in enumerate(("banned", "active", "active", "active", "active")):
            self.conn.execute(
                """
                INSERT INTO alert_outcomes (deal_id, platform, item_id, seller_id, seller_status, seller_checked_at, alert_at)
                VALUES (?, 'WALLAPOP', ?, ?, ?, datetime('now', '-1 hour'), datetime('now', '-25 hours'))
                """,
                (f"al_{i}", f"ai_{i}", f"sa_{i}", st),
            )
        self.conn.commit()

        comp = b.summarize_fraud_samples(self.conn)
        self.assertEqual(comp["blocked"], {"total": 4, "checked": 4, "suspended": 3, "pct": 75.0})
        self.assertEqual(comp["alerted"], {"checked": 5, "suspended": 1, "pct": 20.0})
        lines = b.format_fraud_comparison_lines(comp)
        joined = "\n".join(lines)
        self.assertIn("Bloqueados", joined)
        self.assertIn("75%", joined)
        self.assertIn("20%", joined)

    def test_outcome_tracker_skips_backoff_platforms_in_sql_so_other_platforms_not_starved(self):
        import time as _t

        for i in range(10):
            self.conn.execute(
                """
                INSERT INTO fraud_samples (platform, item_id, seller_id, risk, codes, created_at)
                VALUES ('EBAY', ?, ?, 6, 'ZERO_REP', datetime('now', ?))
                """,
                (f"eb_{i}", f"eb_s_{i}", f"-{30 + i} hours"),
            )
        self.conn.execute(
            """
            INSERT INTO fraud_samples (platform, item_id, seller_id, risk, codes, created_at)
            VALUES ('WALLAPOP', 'wal_ok_1', 'wal_s_1', 6, 'ZERO_REP', datetime('now', '-25 hours'))
            """
        )
        self.conn.commit()

        fake_res = {"status": "available", "http": 200, "price": None, "views": None, "favorites": None, "conversations": None, "detail": ""}
        ebay_mock = mock.Mock(return_value=fake_res)
        wal_mock = mock.Mock(return_value=fake_res)
        with mock.patch.dict(ot.CHECKERS, {"EBAY": ebay_mock, "WALLAPOP": wal_mock}), \
             mock.patch.object(ot, "check_seller", return_value="active") as seller_mock, \
             mock.patch.object(ot, "_wait_pace"):
            ot._backoff_until.clear()
            ot._backoff_until["EBAY"] = _t.time() + 3600
            try:
                checked = ot.check_pending_fraud_samples(self.conn, limit=10)
            finally:
                ot._backoff_until.clear()

        self.assertEqual(checked, 1)
        ebay_mock.assert_not_called()
        wal_mock.assert_called_once_with("wal_ok_1")
        seller_mock.assert_called_once_with("WALLAPOP", "wal_s_1")
        wal_chk = self.conn.execute(
            "SELECT item_status, seller_status, checked_at FROM fraud_samples WHERE item_id='wal_ok_1'"
        ).fetchone()
        self.assertEqual(wal_chk[:2], ("available", "active"))
        self.assertIsNotNone(wal_chk[2])


if __name__ == "__main__":
    unittest.main()
