"""T1.5: Discord sin pérdidas silenciosas, payload válido y guardado de message_id."""
import contextlib
import io
import json
import sqlite3
import unittest
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce


class FakeResp:
    def __init__(self, status, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("sin JSON")
        return self._payload


def _target():
    return {"name": "Radar test", "mode": "resale"}


def _ai():
    return {"deal_score": 8, "deal_type": "direct_chollo", "identified_model": "Consola X"}


class DiscordTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE deals (deal_id TEXT PRIMARY KEY, alert_context_json TEXT)")
        self.conn.execute("INSERT INTO deals (deal_id, alert_context_json) VALUES ('WAL_999', '')")
        self.conn.execute("INSERT INTO deals (deal_id, alert_context_json) VALUES ('MIL_999', '')")
        self.conn.commit()
        patches = [
            mock.patch.object(ce, "BOT_TOKEN", "tok-secreto-123"),
            mock.patch.object(ce, "ALERTS_CHANNEL_ID", "555"),
            mock.patch.object(ce, "WEBHOOK_URL", ""),
            mock.patch.object(ce, "ALERT_USER_ID", ""),
            mock.patch.object(ce, "register_deal_in_db"),
            mock.patch.object(ce, "generate_multilingual_haggle_msg", return_value="hola"),
            mock.patch.object(ce, "decide_alert_push", return_value=(True, "")),
            mock.patch.object(ce.time, "sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.conn.close)

    def send(self, item_url="https://es.wallapop.com/item/abc-999", platform="WALLAPOP", **kw):
        return ce.send_discord_alert(
            self.conn, platform, "999", "Consola X", "desc", 100.0, 105.0, 5.0,
            _target(), _ai(), None, item_url=item_url, **kw)

    def context(self):
        raw = self.conn.execute("SELECT alert_context_json FROM deals WHERE deal_id='WAL_999'").fetchone()[0]
        return json.loads(raw) if raw else {}


class TestDiscordSend(DiscordTestBase):
    def test_400_then_content_only_fallback_and_logged(self):
        buf = io.StringIO()
        with mock.patch.object(ce.HTTP_SESSION, "post",
                               side_effect=[FakeResp(400, {"message": "Invalid Form Body"}), FakeResp(200, {"id": "123"})]) as post, \
                contextlib.redirect_stdout(buf):
            self.send()
        self.assertEqual(post.call_count, 2)
        first = post.call_args_list[0].kwargs["json"]
        second = post.call_args_list[1].kwargs["json"]
        self.assertIn("embeds", first)
        self.assertNotIn("embeds", second)
        self.assertNotIn("components", second)
        self.assertTrue(second["content"])
        self.assertIn("[DISCORD ERROR] 400", buf.getvalue())
        self.assertEqual(self.context().get("discord_message_id"), "123")

    def test_429_retry_after(self):
        with mock.patch.object(ce.HTTP_SESSION, "post",
                               side_effect=[FakeResp(429, {"retry_after": 0.01}), FakeResp(200, {"id": "7"})]) as post:
            ok, status, mid = ce._discord_send("POST", "https://discord.com/api/v10/channels/1/messages",
                                               json_payload={"content": "x"})
        self.assertEqual((ok, status, mid), (True, 200, "7"))
        self.assertEqual(post.call_count, 2)

    def test_429_retry_after_header(self):
        with mock.patch.object(ce.HTTP_SESSION, "post",
                               side_effect=[FakeResp(429, None, headers={"Retry-After": "0.01"}), FakeResp(200, {"id": "8"})]):
            ok, _, mid = ce._discord_send("POST", "https://x/y", json_payload={"content": "x"})
        self.assertTrue(ok)
        self.assertEqual(mid, "8")

    def test_5xx_retries_with_backoff_then_ok(self):
        with mock.patch.object(ce.HTTP_SESSION, "post",
                               side_effect=[FakeResp(502), OSError("reset"), FakeResp(200, {"id": "9"})]) as post:
            ok, status, mid = ce._discord_send("POST", "https://x/y", json_payload={"content": "x"})
        self.assertEqual((ok, mid), (True, "9"))
        self.assertEqual(post.call_count, 3)

    def test_4xx_not_retried(self):
        with mock.patch.object(ce.HTTP_SESSION, "post", return_value=FakeResp(403, {"message": "Missing Access"})) as post, \
                contextlib.redirect_stdout(io.StringIO()):
            ok, status, mid = ce._discord_send("POST", "https://x/y", json_payload={"content": "x"})
        self.assertEqual((ok, status, mid), (False, 403, None))
        self.assertEqual(post.call_count, 1)

    def test_error_log_redacts_secrets(self):
        resp = FakeResp(400, text="bad https://discord.com/api/webhooks/123456/AbCdEf_gh-IJ and Bot tok-secreto-123")
        buf = io.StringIO()
        with mock.patch.object(ce.HTTP_SESSION, "post", return_value=resp), contextlib.redirect_stdout(buf):
            ce._discord_send("POST", "https://x/y", json_payload={"content": "x"})
        self.assertIn("[DISCORD ERROR] 400", buf.getvalue())
        self.assertNotIn("AbCdEf_gh-IJ", buf.getvalue())

    def test_message_id_saved(self):
        with mock.patch.object(ce.HTTP_SESSION, "post", return_value=FakeResp(200, {"id": "123"})):
            self.send()
        ctx = self.context()
        self.assertEqual(ctx.get("discord_message_id"), "123")
        self.assertEqual(ctx.get("discord_channel_id"), "555")
        self.assertIn('"discord_message_id":', self.conn.execute(
            "SELECT alert_context_json FROM deals WHERE deal_id='WAL_999'").fetchone()[0].replace('": "', '":'))

    def test_message_id_preserves_existing_context(self):
        self.conn.execute("UPDATE deals SET alert_context_json = ? WHERE deal_id='WAL_999'", (json.dumps({"seller": {"x": 1}}),))
        self.conn.commit()
        with mock.patch.object(ce.HTTP_SESSION, "post", return_value=FakeResp(200, {"id": "42"})):
            self.send()
        ctx = self.context()
        self.assertEqual(ctx["discord_message_id"], "42")

    def test_empty_url_no_embed_url_nor_empty_buttons(self):
        with mock.patch.object(ce.HTTP_SESSION, "post", return_value=FakeResp(200, {"id": "1"})) as post:
            self.send(item_url="", platform="MILANUNCIOS")  # Wallapop/Vinted reconstruyen la URL; Milanuncios no
        body = post.call_args.kwargs["json"]
        self.assertNotIn("url", body["embeds"][0])
        for row in body["components"]:
            self.assertTrue(row["components"], "fila de botones vacía")
            for b in row["components"]:
                if b["style"] == 5:
                    self.assertTrue(b["url"].startswith(("http://", "https://")), b)

    def test_valid_url_keeps_embed_url(self):
        with mock.patch.object(ce.HTTP_SESSION, "post", return_value=FakeResp(200, {"id": "1"})) as post:
            self.send()
        body = post.call_args.kwargs["json"]
        self.assertTrue(body["embeds"][0]["url"].startswith("https://"))

    def test_bot_failure_falls_back_to_webhook(self):
        with mock.patch.object(ce, "WEBHOOK_URL", "https://discord.com/api/webhooks/1/abc"), \
                mock.patch.object(ce.HTTP_SESSION, "post",
                                  side_effect=[FakeResp(403, {"message": "x"}), FakeResp(200, {"id": "w1"})]) as post, \
                contextlib.redirect_stdout(io.StringIO()):
            self.send()
        self.assertEqual(post.call_count, 2)
        self.assertIn("discord.com/api/webhooks", post.call_args_list[1].args[0])
        self.assertEqual(self.context().get("discord_message_id"), "w1")


class TestFlash(DiscordTestBase):
    def test_flash_empty_url_no_empty_buttons(self):
        with mock.patch.object(ce, "get_notification_policy", return_value={"flash_mode": "on"}), \
                mock.patch.object(ce.HTTP_SESSION, "post", return_value=FakeResp(200, {"id": "f1"})) as post:
            res = ce.send_flash_discord_alert("WALLAPOP", "999", "Consola", 50.0, item_url="")
        self.assertEqual(res["message_id"], "f1")
        for row in post.call_args.kwargs["json"]["components"]:
            for b in row["components"]:
                if b["style"] == 5:
                    self.assertTrue(b["url"].startswith("http"))

    def test_flash_400_falls_back_to_content(self):
        with mock.patch.object(ce, "get_notification_policy", return_value={"flash_mode": "on"}), \
                mock.patch.object(ce.HTTP_SESSION, "post",
                                  side_effect=[FakeResp(400, {"message": "x"}), FakeResp(200, {"id": "f2"})]) as post, \
                contextlib.redirect_stdout(io.StringIO()):
            res = ce.send_flash_discord_alert("WALLAPOP", "999", "Consola", 50.0, item_url="https://es.wallapop.com/item/a-1")
        self.assertEqual(res["message_id"], "f2")
        self.assertNotIn("components", post.call_args_list[1].kwargs["json"])


if __name__ == "__main__":
    unittest.main()
