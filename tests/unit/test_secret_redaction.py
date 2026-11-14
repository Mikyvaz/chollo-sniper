"""T1.7: la API key de Gemini va en cabecera, no en la URL, y los logs redactan secretos."""
import io
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce

GOOD = {
    "candidates": [{"finishReason": "STOP",
                    "content": {"parts": [{"text": "{\"is_valid_deal\": true}"}]}}],
    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
}


class TestSecretRedaction(unittest.TestCase):
    def test_redact_key_param(self):
        self.assertEqual(ce._redact("https://x/y?key=AIzaXYZ"), "https://x/y?key=***")

    def test_redact_bearer_and_bot(self):
        self.assertNotIn("abc123", ce._redact("Authorization: Bearer abc123"))
        self.assertNotIn("abc123", ce._redact("Bot abc123"))
        self.assertNotIn("tok_en", ce._redact("https://discord.com/api/webhooks/123/tok_en"))

    def test_shared_helper_in_text_rules(self):
        import text_rules
        self.assertEqual(text_rules.redact_secrets("a?key=SECRET&b=1"), "a?key=***&b=1")

    def test_gemini_key_in_header_not_url(self):
        conn = sqlite3.connect(":memory:")
        ce.init_db(conn)
        resp = mock.Mock(status_code=200)
        resp.json.return_value = GOOD
        resp.text = json.dumps(GOOD)
        with mock.patch.object(ce, "GEMINI_API_KEY", "SECRETKEY"), \
                mock.patch.object(ce, "OPENROUTER_API_KEY", ""), \
                mock.patch("core_engine._AI_HTTP_SESSION.post", return_value=resp) as post, \
                redirect_stdout(io.StringIO()):
            ce.ai_gateway_call(prompt="p t17", candidate_models=["gemini-3.5-flash-lite"],
                               openrouter_models=[], max_tokens=200, conn=conn, platform="WALLAPOP",
                               radar_id="t", call_type="prescreen", enforce_budget=False)
        url = post.call_args[0][0]
        self.assertNotIn("key=", url)
        self.assertNotIn("SECRETKEY", url)
        self.assertEqual(post.call_args[1]["headers"]["x-goog-api-key"], "SECRETKEY")
        conn.close()

    def test_exception_log_redacted(self):
        conn = sqlite3.connect(":memory:")
        ce.init_db(conn)
        err = Exception("MaxRetries url: /v1beta/models/m:generateContent?key=SECRETKEY")
        buf = io.StringIO()
        with mock.patch.object(ce, "GEMINI_API_KEY", "SECRETKEY"), \
                mock.patch.object(ce, "OPENROUTER_API_KEY", ""), \
                mock.patch("core_engine._AI_HTTP_SESSION.post", side_effect=err), \
                redirect_stdout(buf):
            ce.ai_gateway_call(prompt="p t17b", candidate_models=["gemini-3.5-flash-lite"],
                               openrouter_models=[], max_tokens=200, conn=conn, platform="WALLAPOP",
                               radar_id="t", call_type="prescreen", enforce_budget=False)
        self.assertNotIn("SECRETKEY", buf.getvalue())
        conn.close()


if __name__ == "__main__":
    unittest.main()
