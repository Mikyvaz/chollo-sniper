import tests.unit._harness  # noqa: F401
import base64
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from PIL import Image

import core_engine as ce


def _make_jpeg_bytes(width=1200, height=900):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 180, 220)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class TestImageResolution(unittest.TestCase):
    def test_ab_live_sends_key_as_header_and_accounts_for_paid_call(self):
        from tools import ab_image_resolution as ab
        response = mock.Mock()
        response.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": '{"is_valid_deal": true}'}]}}],
            "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 10,
                              "promptTokensDetails": [{"modality": "IMAGE", "tokenCount": 150}]}}
        db = mock.Mock()
        with mock.patch.object(ce, "get_db", return_value=db), \
                mock.patch.object(ce, "_assert_ai_budget_available") as budget, \
                mock.patch.object(ce, "log_ai_cost") as accounting, \
                mock.patch.object(ce._AI_HTTP_SESSION, "post", return_value=response) as post:
            result = ab._default_gemini_vision_call("prompt", "AAAA", "gemini-test-lite", "fake-key")
        self.assertNotIn("key=", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "fake-key")
        budget.assert_called_once_with(db)
        self.assertEqual(accounting.call_args.kwargs["tokens_img"], 150)
        self.assertEqual(result["tokens_img"], 150)
        db.close.assert_called_once_with()

    def test_ab_live_obeys_monthly_budget_before_http(self):
        from tools import ab_image_resolution as ab
        with mock.patch.object(ce, "get_db", return_value=mock.Mock()), \
                mock.patch.object(ce, "_assert_ai_budget_available", side_effect=ce.BudgetExceededError("mes agotado")), \
                mock.patch.object(ce._AI_HTTP_SESSION, "post") as post, \
                self.assertRaises(ce.BudgetExceededError):
            ab._default_gemini_vision_call("prompt", "AAAA", "gemini-test-lite", "fake-key")
        post.assert_not_called()

    def test_resolve_image_max_dim_reads_config_and_handles_degraded(self):
        self.assertEqual(ce.resolve_image_max_dim({"ai": {"image_max_dim": 768}}, is_lite=True, budget_status="OK"), 768)
        self.assertEqual(ce.resolve_image_max_dim({"ai": {"image_max_dim": 384}}, is_lite=True, budget_status="OK"), 384)
        self.assertEqual(ce.resolve_image_max_dim({"ai": {"image_max_dim": 768}}, is_lite=True, budget_status="DEGRADED"), 384)
        self.assertEqual(ce.resolve_image_max_dim({}, is_lite=True, budget_status="OK"), 384)
        self.assertEqual(ce.resolve_image_max_dim({}, is_lite=False, budget_status="OK"), 512)

    def test_analyze_and_generate_kit_uses_configured_image_max_dim(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        ce.init_db(conn)
        raw_img = _make_jpeg_bytes(1200, 900)
        compressed_default = ce.compress_image_to_jpeg_640(raw_img, max_dim=1024)

        for configured_dim in (768, 384):
            seen_dims = []

            def _fake_gateway(prompt, images_b64=None, **_kwargs):
                for b64 in (images_b64 or []):
                    raw = base64.b64decode(b64)
                    with Image.open(io.BytesIO(raw)) as im:
                        seen_dims.append(max(im.size))
                return json.dumps({
                    "is_valid_deal": False,
                    "identified_model": "Switch Test",
                    "deal_score": 3,
                    "confidence": "alta",
                    "reject_reason": "test",
                })

            with mock.patch.object(ce, "GEMINI_API_KEY", "fake-key"), \
                 mock.patch.object(ce, "load_raw_config", return_value={"ai": {"image_max_dim": configured_dim}}), \
                 mock.patch.object(ce, "check_ai_budget_status", return_value=("OK", 0.05)), \
                 mock.patch.object(ce, "ai_gateway_call", side_effect=_fake_gateway):
                ce.analyze_and_generate_kit(
                    "WALLAPOP", f"Nintendo Switch {configured_dim}", "Consola completa",
                    90.0, 95.0, False, {"id": "switch", "name": "Switch", "mode": "resale"},
                    conn=conn, raw_images=[compressed_default],
                )

            self.assertEqual(seen_dims, [configured_dim], f"Esperado max_dim={configured_dim}, visto={seen_dims}")

    def test_ab_script_dry_run_makes_no_network_calls(self):
        from tools import ab_image_resolution as ab

        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                with open(os.path.join(tmp, f"foto_{i}.jpg"), "wb") as fh:
                    fh.write(_make_jpeg_bytes(1000, 800))

            with mock.patch.object(ce.HTTP_SESSION, "post", side_effect=AssertionError("Sin red en dry-run")):
                res = ab.run_ab(kits_dir=tmp, max_photos=20, live=False)

        self.assertTrue(res["dry_run"])
        self.assertEqual(res["photos_found"], 5)
        self.assertEqual(res["records"], [])

    def test_ab_script_evaluate_recommendations_and_mocked_live(self):
        from tools import ab_image_resolution as ab

        rec_same_cost = [
            {"path": "a.jpg", "tokens_384": 900, "tokens_768": 920, "verdict_384": True, "verdict_768": True},
            {"path": "b.jpg", "tokens_384": 910, "tokens_768": 910, "verdict_384": False, "verdict_768": True},
        ]
        s1 = ab.evaluate_ab_results(rec_same_cost)
        self.assertEqual(s1["recommendation"], "raise_image_max_dim_768")
        self.assertEqual(s1["agreement_count"], 1)
        self.assertEqual(s1["agreement_pct"], 50.0)

        rec_expensive = [
            {"path": "a.jpg", "tokens_384": 900, "tokens_768": 1800, "verdict_384": True, "verdict_768": True},
        ]
        s2 = ab.evaluate_ab_results(rec_expensive)
        self.assertEqual(s2["recommendation"], "test_media_resolution_low")

        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                with open(os.path.join(tmp, f"kit_{i}.jpg"), "wb") as fh:
                    fh.write(_make_jpeg_bytes(1200, 900))

            seen_calls = []

            def _fake_caller(prompt, img_b64, model, api_key):
                with Image.open(io.BytesIO(base64.b64decode(img_b64))) as im:
                    seen_calls.append((prompt, max(im.size), model))
                return {"tokens_img": 950, "is_valid_deal": True}

            res = ab.run_ab(kits_dir=tmp, max_photos=20, live=True, api_key="fake-key", caller=_fake_caller)

        self.assertFalse(res["dry_run"])
        self.assertEqual(len(res["records"]), 3)
        self.assertEqual(res["summary"]["recommendation"], "raise_image_max_dim_768")
        self.assertEqual(res["summary"]["agreement_pct"], 100.0)
        self.assertEqual(len({c[0] for c in seen_calls}), 1, "Debe usar exactamente el mismo prompt")
        self.assertEqual(sorted({c[1] for c in seen_calls}), [384, 768])


if __name__ == "__main__":
    unittest.main()
