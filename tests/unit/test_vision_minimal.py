"""T1.13: Puerta 2 con salida mínima y telemetría de tokens por modalidad."""
import io
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce

TARGET = {"id": "switch_std_lite", "name": "Switch", "mode": "resale"}


def _capture_prompt(platform="WALLAPOP", mode="resale"):
    seen = {}

    def fake_gateway(**kw):
        seen.update(kw)
        return {"is_valid_deal": False, "reject_reason": "x"}

    t = dict(TARGET, mode=mode)
    with mock.patch.object(ce, "GEMINI_API_KEY", "test-key"), \
            mock.patch.object(ce, "ai_gateway_call", side_effect=fake_gateway), \
            redirect_stdout(io.StringIO()):
        ce.analyze_and_generate_kit(platform, "Switch OLED completa", "buen estado", 90.0, 90.0,
                                    False, t, conn=None)
    return seen


class TestVisionPrompt(unittest.TestCase):
    def test_prompt_has_minimal_output_instruction(self):
        p = _capture_prompt()["prompt"]
        self.assertIn("Si is_valid_deal=false, rellena solo is_valid_deal", p)
        self.assertIn("15 palabras como máximo", p)
        self.assertIn("visual_notes: 25 palabras como máximo", p)

    def test_fast_buy_message_not_requested(self):
        for mode in ("resale", "personal"):
            p = _capture_prompt(mode=mode)["prompt"]
            self.assertNotIn("fast_buy_message", p, mode)

    def test_cache_prefix_v46(self):
        keys = []
        real = ce.build_robust_cache_key

        def spy(*a, **kw):
            keys.append(kw.get("prefix"))
            return real(*a, **kw)

        with mock.patch.object(ce, "build_robust_cache_key", side_effect=spy):
            _capture_prompt()
        self.assertIn("v46", keys)
        self.assertNotIn("v45", keys)


class TestTokensByModality(unittest.TestCase):
    def test_parse_details(self):
        usage = {"promptTokensDetails": [{"modality": "TEXT", "tokenCount": 760},
                                         {"modality": "IMAGE", "tokenCount": 980}]}
        self.assertEqual(ce._prompt_tokens_by_modality(usage), (980, 760))
        self.assertEqual(ce._prompt_tokens_by_modality({}), (0, 0))
        self.assertEqual(ce._prompt_tokens_by_modality({"promptTokensDetails": None}), (0, 0))

    def test_gateway_logs_tokens_img_and_text(self):
        conn = sqlite3.connect(":memory:")
        ce.init_db(conn)
        payload = {
            "candidates": [{"finishReason": "STOP",
                            "content": {"parts": [{"text": "{\"is_valid_deal\": false}"}]}}],
            "usageMetadata": {"promptTokenCount": 1750, "candidatesTokenCount": 40,
                              "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 770},
                                                      {"modality": "IMAGE", "tokenCount": 980}]},
        }
        r = mock.Mock(status_code=200)
        r.json.return_value = payload
        r.text = json.dumps(payload)
        with mock.patch.object(ce, "GEMINI_API_KEY", "test-key"), \
                mock.patch.object(ce, "OPENROUTER_API_KEY", ""), \
                mock.patch("core_engine._AI_HTTP_SESSION.post", return_value=r), \
                redirect_stdout(io.StringIO()):
            ce.ai_gateway_call(prompt="p t113", images_b64=["AAAA"],
                               candidate_models=["gemini-3.5-flash-lite"], openrouter_models=[],
                               max_tokens=450, conn=conn, platform="WALLAPOP", radar_id="t",
                               call_type="vision", enforce_budget=False)
        row = conn.execute("SELECT tokens_in, tokens_img, tokens_text FROM ai_cost_log").fetchone()
        self.assertEqual(row, (1750, 980, 770))

    def test_log_without_details_is_null_safe(self):
        conn = sqlite3.connect(":memory:")
        ce.init_db(conn)
        ce.log_ai_cost(conn, "X", "r", "gemini-3.5-flash-lite", "vision", 100, 10)
        row = conn.execute("SELECT tokens_img, tokens_text FROM ai_cost_log").fetchone()
        self.assertEqual(row, (0, 0))


if __name__ == "__main__":
    unittest.main()
