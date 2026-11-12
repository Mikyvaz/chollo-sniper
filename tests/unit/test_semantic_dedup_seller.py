import tests.unit._harness  # noqa: F401
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock


def _unit_vec(seed_idx=0, dims=64):
    vec = [0.0] * dims
    vec[seed_idx % dims] = 1.0
    return vec


def _simulate_pipeline_step(conn, analyze_mock, *, platform, item_id, title, desc, price, radar_id, seller_id, vector, image_hash_provider=None):
    import core_engine

    action, ai_data, rej_reason, _, _ = core_engine.evaluate_semantic_duplicate(
        conn,
        platform=platform,
        item_id=item_id,
        title=title,
        desc=desc,
        price=price,
        radar_id=radar_id,
        current_seller_id=seller_id,
        image_hash_provider=image_hash_provider,
        vector=vector,
    )
    if action == "reject":
        return "rejected_by_dedup", rej_reason
    if ai_data is None:
        ai_data = analyze_mock(platform, title, desc, price)
        return "evaluated_by_vision", ai_data
    return "reused_approved", ai_data


class TestSemanticDedupSeller(unittest.TestCase):
    def setUp(self):
        import core_engine

        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_dedup.db")
        self.conn = sqlite3.connect(self.db_path)
        core_engine.init_db(self.conn)
        self.vec = _unit_vec(3)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _seed_rejected_embedding(self, item_key="WALLAPOP:prev_1", seller_id="seller_A", reject_reason="Consola con pantalla rota", price=120.0, radar_id="switch_oled"):
        import core_engine

        verdict = {"is_valid_deal": False, "reject_reason": reject_reason}
        ok = core_engine.save_item_embedding(
            self.conn,
            item_key,
            radar_id,
            vector_or_text=self.vec,
            verdict_json=json.dumps(verdict, ensure_ascii=False),
            title="Nintendo Switch OLED completa",
            price=price,
            seller_id=seller_id,
            precomputed_vec=self.vec,
        )
        self.assertTrue(ok)

    def test_different_seller_without_identical_photo_does_not_inherit_rejection(self):
        self._seed_rejected_embedding(seller_id="seller_A", reject_reason="Consola con pantalla rota")
        analyze_mock = MagicMock(return_value={"is_valid_deal": True, "deal_score": 9})

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            outcome, _ = _simulate_pipeline_step(
                self.conn,
                analyze_mock,
                platform="WALLAPOP",
                item_id="cur_b",
                title="Nintendo Switch OLED completa",
                desc="Perfecto estado con caja",
                price=120.0,
                radar_id="switch_oled",
                seller_id="seller_B",
                vector=self.vec,
            )

        self.assertEqual(outcome, "evaluated_by_vision")
        analyze_mock.assert_called_once()
        out = buf.getvalue()
        self.assertIn("seller_B", out)
        self.assertIn("seller_A", out)

    def test_same_seller_non_price_rejection_is_inherited(self):
        self._seed_rejected_embedding(seller_id="seller_A", reject_reason="Consola con pantalla rota")
        analyze_mock = MagicMock(return_value={"is_valid_deal": True, "deal_score": 9})

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            outcome, rej = _simulate_pipeline_step(
                self.conn,
                analyze_mock,
                platform="WALLAPOP",
                item_id="cur_a",
                title="Nintendo Switch OLED completa",
                desc="Republicada",
                price=120.0,
                radar_id="switch_oled",
                seller_id="seller_A",
                vector=self.vec,
            )

        self.assertEqual(outcome, "rejected_by_dedup")
        self.assertEqual(rej, "Consola con pantalla rota")
        analyze_mock.assert_not_called()
        out = buf.getvalue()
        self.assertIn("DEDUP SEMÁNTICA RECHAZO", out)
        self.assertIn("seller_A", out)

    def test_same_seller_price_reason_never_inherited(self):
        import core_engine

        self._seed_rejected_embedding(
            seller_id="seller_A",
            reject_reason="El precio de 35€ es irrealmente bajo",
        )
        analyze_mock = MagicMock(return_value={"is_valid_deal": True, "deal_score": 9})

        outcome, _ = _simulate_pipeline_step(
            self.conn,
            analyze_mock,
            platform="WALLAPOP",
            item_id="cur_price",
            title="Nintendo Switch OLED completa",
            desc="Rebajado",
            price=35.0,
            radar_id="switch_oled",
            seller_id="seller_A",
            vector=self.vec,
        )
        self.assertEqual(outcome, "evaluated_by_vision")
        analyze_mock.assert_called_once()

        # Verify all price-dependent regex keywords are blocked even for same seller
        for reason in (
            "Precio sospechoso",
            "Margen insuficiente",
            "Demasiado caro para reventa",
            "Muy barato, sospechoso",
            "Oferta irreal",
            "Posible estafa",
            "Anuncio anzuelo",
        ):
            inherit, why = core_engine.should_inherit_semantic_rejection(
                self.conn,
                {"item_key": "WALLAPOP:prev_1", "similarity": 0.99, "seller_id": "seller_A"},
                {"is_valid_deal": False, "reject_reason": reason},
                current_seller_id="seller_A",
            )
            self.assertFalse(inherit, f"Reason '{reason}' should never be inherited")
            self.assertEqual(why, "price_dependent_reason")

    def test_different_seller_with_identical_photo_dhash_inherits_non_price_rejection(self):
        self._seed_rejected_embedding(
            item_key="WALLAPOP:prev_img",
            seller_id="seller_A",
            reject_reason="Falta cargador y dock",
        )
        self.conn.execute(
            "INSERT INTO seen_image_hashes (hash, platform, item_id, radar_id) VALUES (?, ?, ?, ?)",
            ("0123456789abcdef", "WALLAPOP", "prev_img", "switch_oled"),
        )
        self.conn.commit()

        analyze_mock = MagicMock(return_value={"is_valid_deal": True, "deal_score": 9})

        # Different photo -> must NOT inherit
        outcome_diff, _ = _simulate_pipeline_step(
            self.conn,
            analyze_mock,
            platform="WALLAPOP",
            item_id="cur_diff_img",
            title="Nintendo Switch OLED completa",
            desc="Foto nueva",
            price=120.0,
            radar_id="switch_oled",
            seller_id="seller_B",
            vector=self.vec,
            image_hash_provider=lambda: ["fedcba9876543210"],
        )
        self.assertEqual(outcome_diff, "evaluated_by_vision")
        self.assertEqual(analyze_mock.call_count, 1)

        # Identical photo dHash -> MUST inherit without calling vision
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            outcome_same, rej = _simulate_pipeline_step(
                self.conn,
                analyze_mock,
                platform="WALLAPOP",
                item_id="cur_same_img",
                title="Nintendo Switch OLED completa",
                desc="Misma foto republicada",
                price=120.0,
                radar_id="switch_oled",
                seller_id="seller_B",
                vector=self.vec,
                image_hash_provider=lambda: ["0123456789abcdef"],
            )
        self.assertEqual(outcome_same, "rejected_by_dedup")
        self.assertEqual(rej, "Falta cargador y dock")
        self.assertEqual(analyze_mock.call_count, 1)
        out = buf.getvalue()
        self.assertIn("seller_B", out)
        self.assertIn("seller_A", out)

    def test_empty_seller_ids_do_not_match_as_same_seller(self):
        self._seed_rejected_embedding(item_key="EBAY:prev_empty", seller_id="", reject_reason="Carcasa dañada")
        analyze_mock = MagicMock(return_value={"is_valid_deal": True})
        outcome, _ = _simulate_pipeline_step(
            self.conn,
            analyze_mock,
            platform="EBAY",
            item_id="cur_empty",
            title="Nintendo Switch OLED completa",
            desc="Sin seller_id",
            price=120.0,
            radar_id="switch_oled",
            seller_id="",
            vector=self.vec,
        )
        self.assertEqual(outcome, "evaluated_by_vision")
        analyze_mock.assert_called_once()

    def test_approved_match_still_reused_only_for_same_seller_and_lower_or_equal_price(self):
        import core_engine

        verdict = {"is_valid_deal": True, "deal_score": 9, "identified_model": "Switch OLED"}
        core_engine.save_item_embedding(
            self.conn,
            "WALLAPOP:prev_ok",
            "switch_oled",
            vector_or_text=self.vec,
            verdict_json=json.dumps(verdict),
            title="Nintendo Switch OLED",
            price=150.0,
            seller_id="seller_A",
            precomputed_vec=self.vec,
        )
        analyze_mock = MagicMock(return_value={"is_valid_deal": True})

        out_diff, _ = _simulate_pipeline_step(
            self.conn,
            analyze_mock,
            platform="WALLAPOP",
            item_id="cur_ok_b",
            title="Nintendo Switch OLED",
            desc="Mismo texto",
            price=145.0,
            radar_id="switch_oled",
            seller_id="seller_B",
            vector=self.vec,
        )
        self.assertEqual(out_diff, "evaluated_by_vision")
        self.assertEqual(analyze_mock.call_count, 1)

        out_same, ai_data = _simulate_pipeline_step(
            self.conn,
            analyze_mock,
            platform="WALLAPOP",
            item_id="cur_ok_a",
            title="Nintendo Switch OLED",
            desc="Mismo texto",
            price=145.0,
            radar_id="switch_oled",
            seller_id="seller_A",
            vector=self.vec,
        )
        self.assertEqual(out_same, "reused_approved")
        self.assertEqual(ai_data["identified_model"], "Switch OLED")
        self.assertEqual(analyze_mock.call_count, 1)

    def test_make_image_hash_prefetcher_and_scanners_use_shared_helper(self):
        from unittest.mock import patch
        import core_engine

        with patch.object(core_engine, "download_item_images", return_value=[b"imgbytes"]) as dl_mock, \
             patch.object(core_engine, "calculate_dhash", return_value="abcd1234abcd1234") as dh_mock:
            provider, state = core_engine.make_image_hash_prefetcher(["https://example.com/1.jpg"])
            h1 = provider()
            h2 = provider()
            self.assertEqual(h1, ["abcd1234abcd1234"])
            self.assertEqual(h2, ["abcd1234abcd1234"])
            self.assertEqual(state["photos"], [b"imgbytes"])
            self.assertEqual(state["dhash"], "abcd1234abcd1234")
            dl_mock.assert_called_once()
            dh_mock.assert_called_once()

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for fname in ("sniper.py", "vinted_sniper.py", "multi_sniper.py"):
            with open(os.path.join(root, fname), "r", encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn("evaluate_semantic_duplicate(", src, f"{fname} must call evaluate_semantic_duplicate")
            self.assertIn("make_image_hash_prefetcher(", src, f"{fname} must call make_image_hash_prefetcher")


if __name__ == "__main__":
    unittest.main()
