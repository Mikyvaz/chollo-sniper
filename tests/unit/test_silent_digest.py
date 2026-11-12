import tests.unit._harness  # noqa: F401
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import time as dt_time
from unittest import mock
from zoneinfo import ZoneInfo

import core_engine as ce
import outcome_tracker as ot


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"id": "d-1"}
        self.headers = {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class TestSilentDigest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = os.path.join(self.tmpdir.name, "digest.db")
        self.conn = sqlite3.connect(self.db_path)
        self.addCleanup(self.conn.close)
        ce.init_db(self.conn)
        ot.ensure_outcome_tables(self.conn)

    def _insert_deal(self, deal_id, profit, push=False, status="available", hours_ago=2):
        self.conn.execute(
            """
            INSERT INTO deals (
                deal_id, platform, item_id, title, identified_model, price, net_profit,
                item_url, seller_city, alert_context_json, created_at
            )
            VALUES (?, 'WALLAPOP', ?, ?, ?, 80.0, ?, ?, 'Madrid', ?, datetime('now', ?))
            """,
            (
                deal_id,
                deal_id,
                f"Consola {deal_id}",
                f"Modelo {deal_id}",
                float(profit),
                f"https://es.wallapop.com/item/{deal_id}?utm_source=test",
                json.dumps({"push": bool(push)}),
                f"-{int(hours_ago)} hours",
            ),
        )
        self.conn.execute(
            """
            INSERT INTO alert_outcomes (deal_id, platform, item_id, item_url, last_status)
            VALUES (?, 'WALLAPOP', ?, ?, ?)
            """,
            (deal_id, deal_id, f"https://es.wallapop.com/item/{deal_id}", status),
        )

    def test_12_silent_3_sold_returns_9_available_ordered_by_profit(self):
        avail_profits = [15, 45, 30, 80, 22, 60, 12, 95, 35]
        for idx, p in enumerate(avail_profits):
            self._insert_deal(f"avail_{idx}", p, push=False, status="available", hours_ago=3)
        for idx, p in enumerate((500, 120, 75)):
            self._insert_deal(f"sold_{idx}", p, push=False, status="sold", hours_ago=2)
        # Excluye también alertas sonoras (push=True) y antiguas (>24h)
        self._insert_deal("loud_1", 999, push=True, status="available", hours_ago=1)
        self._insert_deal("old_1", 888, push=False, status="available", hours_ago=30)
        self.conn.commit()

        rows = ce.get_daily_silent_digest_deals(self.conn, hours=24, limit=10)
        self.assertEqual(len(rows), 9)
        self.assertEqual([r["net_profit"] for r in rows], sorted(avail_profits, reverse=True))
        for r in rows:
            self.assertTrue(r["item_url"].startswith("https://"))
            self.assertNotIn("utm_source", r["item_url"])
            self.assertTrue(r["deal_id"].startswith("avail_"))

        msg = ce.format_silent_digest_message(rows)
        self.assertLessEqual(len(msg), 2000)
        self.assertIn("Modelo avail_7", msg)
        self.assertIn("https://es.wallapop.com/item/avail_7", msg)

    def test_respects_limit_10(self):
        for idx in range(12):
            self._insert_deal(f"top_{idx}", 10 + idx * 5, push=False, status="available", hours_ago=1)
        self.conn.commit()
        rows = ce.get_daily_silent_digest_deals(self.conn, hours=24, limit=10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["net_profit"], 65.0)
        self.assertEqual(rows[-1]["net_profit"], 20.0)

    def test_silent_mode_digest_skips_individual_discord_post(self):
        pol = {**ce.NOTIFICATION_DEFAULTS, "silent_mode": "digest"}
        with mock.patch.object(ce, "BOT_TOKEN", "bot-tok"), \
             mock.patch.object(ce, "ALERTS_CHANNEL_ID", "555"), \
             mock.patch.object(ce, "WEBHOOK_URL", ""), \
             mock.patch.object(ce, "decide_alert_push", return_value=(False, "margen < 40€")), \
             mock.patch.object(ce, "get_notification_policy", return_value=pol), \
             mock.patch.object(ce, "generate_multilingual_haggle_msg", return_value="hola"), \
             mock.patch.object(ce.HTTP_SESSION, "post") as post_mock:
            ok = ce.send_discord_alert(
                self.conn, "WALLAPOP", "777", "Switch Lite", "desc",
                65.0, 70.0, 5.0, {"name": "Radar", "mode": "resale"},
                {"deal_score": 8, "deal_type": "direct_chollo", "identified_model": "Switch Lite"},
                None, optimal_price=105.0, net_profit=35.0,
                item_url="https://es.wallapop.com/item/777",
            )
        self.assertFalse(ok)
        post_mock.assert_not_called()
        ctx_raw = self.conn.execute("SELECT alert_context_json FROM deals WHERE deal_id='WAL_777'").fetchone()[0]
        self.assertFalse(json.loads(ctx_raw)["push"])

    def test_silent_mode_individual_posts_with_silent_flag(self):
        pol = {**ce.NOTIFICATION_DEFAULTS, "silent_mode": "individual"}
        with mock.patch.object(ce, "BOT_TOKEN", "bot-tok"), \
             mock.patch.object(ce, "ALERTS_CHANNEL_ID", "555"), \
             mock.patch.object(ce, "WEBHOOK_URL", ""), \
             mock.patch.object(ce, "decide_alert_push", return_value=(False, "margen < 40€")), \
             mock.patch.object(ce, "get_notification_policy", return_value=pol), \
             mock.patch.object(ce, "generate_multilingual_haggle_msg", return_value="hola"), \
             mock.patch.object(ce.HTTP_SESSION, "post", return_value=_FakeResp()) as post_mock:
            ok = ce.send_discord_alert(
                self.conn, "WALLAPOP", "778", "Switch Lite", "desc",
                65.0, 70.0, 5.0, {"name": "Radar", "mode": "resale"},
                {"deal_score": 8, "deal_type": "direct_chollo", "identified_model": "Switch Lite"},
                None, optimal_price=105.0, net_profit=35.0,
                item_url="https://es.wallapop.com/item/778",
            )
        self.assertTrue(ok)
        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(post_mock.call_args.kwargs["json"].get("flags"), ce.DISCORD_SILENT_FLAG)

    def test_bot_daily_digest_loop_time_and_send(self):
        import bot as b
        loop_times = getattr(b.daily_silent_digest_loop, "time", [])
        self.assertEqual(loop_times, [dt_time(hour=21, minute=0, tzinfo=ZoneInfo("Europe/Madrid"))])

        self._insert_deal("dig_1", 55.0, push=False, status="available", hours_ago=2)
        self.conn.commit()
        sent = []

        class _FakeCh:
            async def send(self, content):
                sent.append(content)

        with mock.patch.object(b, "ALERTS_CHANNEL_ID", 555), \
             mock.patch.object(b, "get_db", side_effect=lambda: sqlite3.connect(self.db_path)), \
             mock.patch.object(b.bot, "get_channel", return_value=_FakeCh()):
            asyncio.run(b.daily_silent_digest_loop.coro())
        self.assertEqual(len(sent), 1)
        self.assertIn("Modelo dig_1", sent[0])
        self.assertIn("https://es.wallapop.com/item/dig_1", sent[0])


if __name__ == "__main__":
    unittest.main()
