"""Tope solicitado: 0,10 USD al mes, compartido por todos los consumidores del gateway."""
import _harness  # noqa: F401
import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

import core_engine as ce


class TestMonthlyBudget(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        with mock.patch.object(ce.threading, "Thread"):
            ce.init_db(self.conn)
        self.addCleanup(self.conn.close)
        ce._today_cost_cache.clear()
        for name, value in (("AI_DAILY_BUDGET_USD", 1.0), ("AI_MONTHLY_BUDGET_USD", 0.10)):
            p = mock.patch.object(ce, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)

    def insert(self, cost, timestamp="CURRENT_TIMESTAMP"):
        self.conn.execute(f"INSERT INTO ai_cost_log (usd, created_at) VALUES (?, {timestamp})", (cost,))
        self.conn.commit()

    def test_previous_day_spend_can_pause_current_day(self):
        self.insert(.10, "datetime('now','localtime','start of month','utc')")
        with mock.patch.object(ce, "get_today_ai_cost", return_value=0), redirect_stdout(io.StringIO()):
            self.assertEqual(ce.check_ai_budget_status(self.conn)[0], "PAUSED")

    def test_month_uses_madrid_boundary_and_rolls_without_waiting_for_cache(self):
        self.insert(.04, "'2026-10-31 23:30:00'")  # 00:30 en Madrid, noviembre
        self.insert(.90, "'2026-10-31 22:30:00'")  # octubre
        with mock.patch.object(ce, "_ai_budget_now", return_value=datetime(2026, 11, 1, 0, 45, tzinfo=timezone.utc)):
            self.assertAlmostEqual(ce.get_month_ai_cost(self.conn), .04)
        with mock.patch.object(ce, "_ai_budget_now", return_value=datetime(2026, 12, 1, 0, 45, tzinfo=timezone.utc)):
            self.assertEqual(ce.get_month_ai_cost(self.conn), 0)

    def test_refresh_observes_spend_after_cached_read(self):
        self.assertEqual(ce.get_month_ai_cost(self.conn), 0)
        self.insert(.10)
        self.assertAlmostEqual(ce.get_month_ai_cost(self.conn, refresh=True), .10)

    def test_month_warning_at_eighty_percent_and_daily_limit_still_applies(self):
        self.insert(.085)
        hm = mock.Mock()
        with mock.patch.object(ce, "_last_budget_check_day", None), redirect_stdout(io.StringIO()):
            status, ratio = ce.check_ai_budget_status(self.conn, hm)
            ce.check_ai_budget_status(self.conn, hm)
        self.assertEqual(status, "NORMAL")
        self.assertAlmostEqual(ratio, .85)
        self.assertEqual(hm.send_health_embed.call_count, 1)
        self.assertIn("mes", hm.send_health_embed.call_args.args[1])
        with mock.patch.object(ce, "AI_DAILY_BUDGET_USD", .01), redirect_stdout(io.StringIO()):
            self.assertEqual(ce.check_ai_budget_status(self.conn)[0], "PAUSED")

    def test_no_request_at_monthly_cap_even_with_stale_cache(self):
        ce.get_month_ai_cost(self.conn)
        self.insert(.10)
        with mock.patch.object(ce, "GEMINI_API_KEY", "fake-key"), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post") as post, \
                redirect_stdout(io.StringIO()), self.assertRaises(ce.BudgetExceededError):
            ce.ai_gateway_call("monthly cap", conn=self.conn)
        post.assert_not_called()

    def test_rechecks_monthly_budget_before_fallback(self):
        r = mock.Mock(status_code=200)
        r.json.return_value = {"candidates": [{"finishReason": "MAX_TOKENS"}], "usageMetadata": {}}
        r.headers = {}
        with mock.patch.object(ce, "GEMINI_API_KEY", "fake-key"), \
                mock.patch.object(ce, "OPENROUTER_API_KEY", "fake-key"), \
                mock.patch.object(ce, "calculate_ai_cost_usd", return_value=.10), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=r) as post, \
                redirect_stdout(io.StringIO()), self.assertRaises(ce.BudgetExceededError):
            ce.ai_gateway_call("cost before fallback", conn=self.conn,
                               candidate_models=["budget-test-lite"], openrouter_models=["fallback-test"])
        self.assertEqual(post.call_count, 1)

    def test_missing_accounting_database_blocks_paid_requests(self):
        with mock.patch.object(ce, "get_db", side_effect=sqlite3.OperationalError("unavailable")), \
                mock.patch.object(ce, "GEMINI_API_KEY", "fake-key"), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post") as post, \
                self.assertRaises(ce.BudgetExceededError):
            ce.ai_gateway_call("no accounting", conn=None)
        post.assert_not_called()

    def test_unpersisted_cost_pauses_following_calls_even_after_table_recovers(self):
        self.conn.execute("DROP TABLE ai_cost_log")
        with redirect_stdout(io.StringIO()):
            ce.log_ai_cost(self.conn, "TEST", "budget", "gemini-test-lite", "test", 10, 10)
        self.conn.execute("CREATE TABLE ai_cost_log (usd REAL, created_at TIMESTAMP)")
        with self.assertRaises(ce.BudgetExceededError):
            ce._assert_ai_budget_available(self.conn)

    def test_zero_monthly_budget_stops_all_paid_calls(self):
        with mock.patch.object(ce, "AI_MONTHLY_BUDGET_USD", 0), self.assertRaises(ce.BudgetExceededError):
            ce._assert_ai_budget_available(self.conn)
