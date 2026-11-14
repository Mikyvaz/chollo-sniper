"""T1.6: Gemini MAX_TOKENS (truncado), finish_reason en ai_cost_log y presupuesto de salida por modelo."""
import io
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce

TRUNCATED = {
    "candidates": [{"finishReason": "MAX_TOKENS",
                    "content": {"parts": [{"text": "{\"is_valid_deal\": tr"}]}}],
    "usageMetadata": {"promptTokenCount": 1800, "candidatesTokenCount": 40, "thoughtsTokenCount": 410},
}
GOOD = {
    "candidates": [{"finishReason": "STOP",
                    "content": {"parts": [{"text": "{\"is_valid_deal\": true, \"visual_notes\": \"ok\"}"}]}}],
    "usageMetadata": {"promptTokenCount": 1800, "candidatesTokenCount": 60, "thoughtsTokenCount": 0},
}


def _resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


class TestAiGateway(unittest.TestCase):
    def setUp(self):
        breaker = mock.patch.object(ce, "_MODEL_BREAKER", ce.ModelBreaker())
        breaker.start()
        self.addCleanup(breaker.stop)
        self.conn = sqlite3.connect(":memory:")
        ce.init_db(self.conn)
        self.calls = []
        self._n = 0

    def tearDown(self):
        self.conn.close()

    def _run(self, responses, models, call_type="vision", max_tokens=450, **kw):
        def fake_post(url, json=None, **k):
            model = url.split("/models/")[1].split(":")[0]
            self.calls.append((model, json))
            return responses[model]
        self._n += 1
        buf = io.StringIO()
        with mock.patch.object(ce, "GEMINI_API_KEY", "test-key"), \
                mock.patch.object(ce, "OPENROUTER_API_KEY", ""), \
                mock.patch("core_engine._AI_HTTP_SESSION.post", side_effect=fake_post), \
                redirect_stdout(buf):
            res = ce.ai_gateway_call(
                prompt=f"prompt de prueba {self._n} {call_type}", candidate_models=models,
                openrouter_models=[], max_tokens=max_tokens, conn=self.conn, platform="WALLAPOP",
                radar_id="t", call_type=call_type, enforce_budget=False, **kw)
        return res, buf.getvalue()

    def test_flash_truncated_falls_to_lite(self):
        res, out = self._run({"gemini-3.5-flash": _resp(TRUNCATED), "gemini-3.5-flash-lite": _resp(GOOD)},
                             ["gemini-3.5-flash", "gemini-3.5-flash-lite"])
        self.assertIsNotNone(res)
        self.assertIs(res.get("is_valid_deal"), True)
        self.assertEqual([m for m, _ in self.calls], ["gemini-3.5-flash", "gemini-3.5-flash-lite"])
        self.assertIn("[AI_GATEWAY] MAX_TOKENS gemini-3.5-flash out=40 thoughts=410", out)

    def test_cost_log_records_finish_reason_and_thoughts(self):
        self._run({"gemini-3.5-flash": _resp(TRUNCATED), "gemini-3.5-flash-lite": _resp(GOOD)},
                  ["gemini-3.5-flash", "gemini-3.5-flash-lite"])
        rows = self.conn.execute(
            "SELECT model, finish_reason, tokens_thoughts, tokens_out FROM ai_cost_log ORDER BY id").fetchall()
        self.assertEqual(rows[0][0:3], ("gemini-3.5-flash", "MAX_TOKENS", 410))
        self.assertEqual(rows[0][3], 450)  # out + thoughts, como antes
        self.assertEqual(rows[1][0:3], ("gemini-3.5-flash-lite", "STOP", 0))

    def test_partial_response_not_parsed_even_if_repairable(self):
        # Una respuesta parcial reparable no debe aceptarse si no hay otro modelo.
        res, _ = self._run({"gemini-3.5-flash": _resp(TRUNCATED)}, ["gemini-3.5-flash"],
                           schema_defaults=None)
        self.assertIsNone(res)

    def test_output_budget_per_model(self):
        self._run({"gemini-3.5-flash": _resp(TRUNCATED), "gemini-3.5-flash-lite": _resp(GOOD)},
                  ["gemini-3.5-flash", "gemini-3.5-flash-lite"])
        by_model = {m: p for m, p in self.calls}
        self.assertEqual(by_model["gemini-3.5-flash"]["generationConfig"]["maxOutputTokens"], 2048)
        self.assertLessEqual(by_model["gemini-3.5-flash-lite"]["generationConfig"]["maxOutputTokens"], 500)

    def test_prescreen_lite_keeps_200(self):
        self._run({"gemini-3.5-flash-lite": _resp(GOOD)}, ["gemini-3.5-flash-lite"],
                  call_type="prescreen", max_tokens=900)
        self.assertEqual(self.calls[0][1]["generationConfig"]["maxOutputTokens"], 200)

    def test_no_thinking_config_ever(self):
        self._run({"gemini-3.5-flash": _resp(TRUNCATED), "gemini-3.5-flash-lite": _resp(GOOD)},
                  ["gemini-3.5-flash", "gemini-3.5-flash-lite"])
        self.assertTrue(self.calls)
        for _, payload in self.calls:
            self.assertNotIn("thinkingConfig", payload["generationConfig"])

    def test_init_db_columns_idempotent_and_migrates_old_table(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE ai_cost_log (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT,
            radar_id TEXT, model TEXT, call_type TEXT, tokens_in INTEGER, tokens_out INTEGER, usd REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        ce.init_db(conn)
        ce.init_db(conn)  # segunda vez: columna duplicada tolerada
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ai_cost_log)").fetchall()]
        self.assertIn("finish_reason", cols)
        self.assertIn("tokens_thoughts", cols)
        conn.close()

    def test_log_ai_cost_backwards_compatible_signature(self):
        ce.log_ai_cost(self.conn, "P", "r", "gemini-3.5-flash-lite", "x", 10, 5)
        row = self.conn.execute("SELECT finish_reason, tokens_thoughts FROM ai_cost_log").fetchone()
        self.assertEqual(row, (None, 0))

    def test_pro_model_switch_off_uses_lite(self):
        kw = dict(price=200.0, title="Lote PS5 con mandos", desc="", target={})
        on = ce.select_multimodal_model(config={}, **kw)
        self.assertEqual(on.gemini_model, "gemini-3.5-flash")
        on2 = ce.select_multimodal_model(config={"ai": {"pro_model_enabled": True}}, **kw)
        self.assertEqual(on2.gemini_model, "gemini-3.5-flash")
        off = ce.select_multimodal_model(config={"ai": {"pro_model_enabled": False}}, **kw)
        self.assertEqual(off.gemini_model, "gemini-3.5-flash-lite")


if __name__ == "__main__":
    unittest.main()
