import tests.unit._harness  # noqa: F401
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import core_engine as ce
import outcome_tracker as ot


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"id": "msg-100"}
        self.headers = {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _all_custom_ids(components):
    ids = []
    for row in components or []:
        for btn in row.get("components", []):
            if btn.get("custom_id"):
                ids.append(btn["custom_id"])
    return ids


class TestAlertLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.addCleanup(self.conn.close)
        ce.init_db(self.conn)
        ot.ensure_outcome_tables(self.conn)
        self.alert_at = datetime(2026, 11, 12, 10, 0, 0)

        p1 = mock.patch.object(ce, "BOT_TOKEN", "bot-tok-test")
        p2 = mock.patch.object(ce, "ALERTS_CHANNEL_ID", "ch-55")
        p3 = mock.patch.object(ce.time, "sleep")
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)

    def _seed_deal(self, deal_id="WAL_100", ctx=None):
        if ctx is None:
            ctx = {
                "discord_message_id": "msg-100",
                "discord_channel_id": "ch-55",
                "discord_via": "bot",
                "discord_content": "🔥 Switch OLED · 90 € → +45 € · WALLAPOP Madrid\nhttps://es.wallapop.com/item/switch-100",
                "discord_components": [
                    {"type": 1, "components": [{"type": 2, "style": 5, "label": "Comprar", "url": "https://es.wallapop.com/item/switch-100"}]},
                    {"type": 1, "components": [
                        {"type": 2, "style": 3, "label": "¡Comprado! -> Reventa", "custom_id": f"deal:buy:{deal_id}"},
                        {"type": 2, "style": 1, "label": "Mensajes", "custom_id": f"deal:msg:{deal_id}"},
                        {"type": 2, "style": 4, "label": "Descartar", "custom_id": f"deal:hide:{deal_id}"},
                    ]},
                ],
            }
        self.conn.execute(
            """
            INSERT INTO deals (deal_id, platform, item_id, title, identified_model, price, net_profit, item_url, alert_context_json)
            VALUES (?, 'WALLAPOP', '100', 'Nintendo Switch OLED', 'Nintendo Switch OLED', 90.0, 45.0,
                    'https://es.wallapop.com/item/switch-100', ?)
            """,
            (deal_id, json.dumps(ctx)),
        )
        self.conn.execute(
            """
            INSERT INTO alert_outcomes (deal_id, platform, item_id, item_url, alert_at, initial_price, last_price, next_check_at)
            VALUES (?, 'WALLAPOP', '100', 'https://es.wallapop.com/item/switch-100', ?, 90.0, 90.0, ?)
            """,
            (deal_id, ot._fmt(self.alert_at), ot._fmt(self.alert_at + timedelta(minutes=15))),
        )
        self.conn.commit()
        return (deal_id, "WALLAPOP", "100", ot._fmt(self.alert_at), 90.0, 0, None, None, None, 0, 0)

    def _res(self, status):
        return {
            "status": status, "http": 200, "price": 90.0,
            "views": 10, "favorites": 2, "conversations": 1, "detail": "",
        }

    def test_record_check_sold_patches_once_and_removes_buy_button(self):
        row = self._seed_deal()
        calls = []

        def _fake_patch(url, **kwargs):
            calls.append((url, kwargs.get("json", {}), kwargs.get("headers", {})))
            return _FakeResp(200)

        with mock.patch.object(ce.HTTP_SESSION, "patch", side_effect=_fake_patch), \
             mock.patch.object(ce.HTTP_SESSION, "get", side_effect=AssertionError("GET inesperado")):
            ot.record_check(self.conn, row, self._res("sold"), self.alert_at + timedelta(minutes=30))
            self.assertEqual(len(calls), 1)
            url, payload, headers = calls[0]
            self.assertEqual(url, "https://discord.com/api/v10/channels/ch-55/messages/msg-100")
            self.assertEqual(headers.get("Authorization"), "Bot bot-tok-test")
            self.assertTrue(payload["content"].startswith("❌ VENDIDO"))
            self.assertIn("https://es.wallapop.com/item/switch-100", payload["content"])
            cids = _all_custom_ids(payload.get("components"))
            self.assertFalse(any(c.startswith("deal:buy") for c in cids), cids)
            self.assertIn("deal:msg:WAL_100", cids)

            state = self.conn.execute("SELECT discord_state FROM alert_outcomes WHERE deal_id='WAL_100'").fetchone()[0]
            self.assertEqual(state, "sold")

            # Repetir sold -> 0 PATCH adicionales
            ot.record_check(self.conn, row, self._res("sold"), self.alert_at + timedelta(minutes=60))
            self.assertEqual(len(calls), 1)

    def test_reserved_then_sold_transitions_without_stacking_prefix(self):
        row = self._seed_deal("WAL_101")
        calls = []

        def _fake_patch(url, **kwargs):
            calls.append(kwargs.get("json", {}))
            return _FakeResp(200)

        with mock.patch.object(ce.HTTP_SESSION, "patch", side_effect=_fake_patch):
            ot.record_check(self.conn, row, self._res("reserved"), self.alert_at + timedelta(minutes=45))
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0]["content"].startswith("⏸️ RESERVADO (hace 45 min)"))
            self.assertFalse(any(c.startswith("deal:buy") for c in _all_custom_ids(calls[0].get("components"))))

            # Repetir reserved -> 0 PATCH adicionales
            ot.record_check(self.conn, row, self._res("reserved"), self.alert_at + timedelta(minutes=60))
            self.assertEqual(len(calls), 1)

            # Transición reserved -> sold -> 1 PATCH más y prefijo limpio
            ot.record_check(self.conn, row, self._res("sold"), self.alert_at + timedelta(minutes=90))
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[1]["content"].startswith("❌ VENDIDO"))
            self.assertNotIn("⏸️ RESERVADO", calls[1]["content"])

    def test_gone_marks_vendido(self):
        row = self._seed_deal("WAL_102")
        calls = []
        with mock.patch.object(ce.HTTP_SESSION, "patch", side_effect=lambda u, **kw: calls.append(kw["json"]) or _FakeResp(200)):
            ot.record_check(self.conn, row, self._res("gone"), self.alert_at + timedelta(minutes=15))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["content"].startswith("❌ VENDIDO"))
        state = self.conn.execute("SELECT discord_state FROM alert_outcomes WHERE deal_id='WAL_102'").fetchone()[0]
        self.assertEqual(state, "gone")

    def test_missing_discord_message_id_skips_patch(self):
        row = self._seed_deal("WAL_103", ctx={"push": True})
        with mock.patch.object(ce.HTTP_SESSION, "patch") as patch_mock:
            ot.record_check(self.conn, row, self._res("sold"), self.alert_at + timedelta(minutes=15))
        patch_mock.assert_not_called()

    def test_fallback_when_content_and_components_not_saved(self):
        row = self._seed_deal("WAL_104", ctx={"discord_message_id": "msg-404", "discord_channel_id": "ch-55"})
        calls = []
        with mock.patch.object(ce.HTTP_SESSION, "patch", side_effect=lambda u, **kw: calls.append(kw["json"]) or _FakeResp(200)):
            ot.record_check(self.conn, row, self._res("sold"), self.alert_at + timedelta(minutes=20))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["content"].startswith("❌ VENDIDO"))
        cids = _all_custom_ids(calls[0].get("components"))
        self.assertFalse(any(c.startswith("deal:buy") for c in cids))

    def test_missing_bot_token_returns_false_and_skips_patch(self):
        self._seed_deal("WAL_105")
        with mock.patch.object(ce, "BOT_TOKEN", ""), \
             mock.patch.object(ce.HTTP_SESSION, "patch") as patch_mock:
            ok = ce._discord_edit_message(self.conn, "WAL_105", "sold")
        self.assertFalse(ok)
        patch_mock.assert_not_called()

    def test_webhook_via_patches_webhook_url_without_components(self):
        row = self._seed_deal(
            "WAL_106",
            ctx={
                "discord_message_id": "wh-msg-9",
                "discord_channel_id": "",
                "discord_via": "webhook",
                "discord_content": "🔥 Switch · 80 € → +40 € · WALLAPOP\nhttps://es.wallapop.com/item/106",
            },
        )
        calls = []
        with mock.patch.object(ce, "WEBHOOK_URL", "https://discord.com/api/webhooks/123/tok?wait=true"), \
             mock.patch.object(ce.HTTP_SESSION, "patch", side_effect=lambda u, **kw: calls.append((u, kw["json"])) or _FakeResp(200)):
            ot.record_check(self.conn, row, self._res("sold"), self.alert_at + timedelta(minutes=15))
        self.assertEqual(len(calls), 1)
        url, payload = calls[0]
        self.assertEqual(url, "https://discord.com/api/webhooks/123/tok/messages/wh-msg-9")
        self.assertTrue(payload["content"].startswith("❌ VENDIDO"))
        self.assertNotIn("components", payload)
        state = self.conn.execute("SELECT discord_state FROM alert_outcomes WHERE deal_id='WAL_106'").fetchone()[0]
        self.assertEqual(state, "sold")


if __name__ == "__main__":
    unittest.main()
