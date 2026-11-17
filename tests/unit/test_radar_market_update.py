"""Radares revisados el 17/11/2026: recarga de config con zona de regateo, topes nuevos y radares nuevos."""
import tests.unit._harness  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import core_engine as ce

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def targets():
    cfg = ce.load_config()
    return cfg, {t["id"]: t for t in cfg["targets"]}


class TestReloadKeepsOfferZone(unittest.TestCase):
    def test_reload_with_load_config_keeps_expanded_max_price(self):
        from antiban import QueryScheduler, TokenBucket

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            shutil.copyfile(os.path.join(ROOT, "config.json"), path)
            os.utime(path, (1000, 1000))
            with mock.patch.object(ce, "CONFIG_PATH", path):
                clock = [0.0]
                bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=lambda: clock[0], sleeper=lambda s: None)
                sched = QueryScheduler.from_config(ce.load_config(), platform="wallapop", bucket=bucket,
                                                   config_path=path, clock=lambda: clock[0], sleeper=lambda s: None,
                                                   now_fn=lambda: datetime(2026, 11, 17, 12), config_loader=ce.load_config)
                before = {t.query: t.max_price for t in sched.all_tasks if t.radar_id == "ps5_consoles_gta6"}
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg["_iter"] = int(cfg.get("_iter", 0)) + 1          # una edición cualquiera (p. ej. desde el bot)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                os.utime(path, (2000, 2000))
                self.assertTrue(sched.reload_if_changed())
                after = {t.query: t.max_price for t in sched.all_tasks if t.radar_id == "ps5_consoles_gta6"}
                self.assertEqual(before, after)
                self.assertEqual(set(after.values()), {390.0}, "300 € directos ×1,30 de zona de regateo")
                ps5 = [t for t in sched.all_tasks if t.radar_id == "ps5_consoles_gta6"][0].target
                self.assertEqual(ps5["direct_buy_max_price"], 300)
                self.assertIn("bizum", sched.config["global_blacklist"])

    def test_scanners_pass_load_config_as_loader(self):
        for fname in ("sniper.py", "vinted_sniper.py"):
            with open(os.path.join(ROOT, fname), encoding="utf-8") as fh:
                self.assertIn("config_loader=load_config", fh.read(), fname)


class TestRepoConfig(unittest.TestCase):
    def test_new_and_adjusted_radars(self):
        _cfg, T = targets()
        self.assertEqual(T["switch_std_lite"]["tier"], "hot")
        self.assertEqual(T["switch_std_lite"]["direct_buy_max_price"], 70)
        self.assertEqual(T["switch_oled"]["direct_buy_max_price"], 130)
        self.assertFalse(T["kindle_ereaders"]["enabled"])
        for tid in ("digicams_premium", "mirrorless_cameras", "pokemon_ds_gba"):
            self.assertEqual(T[tid]["tier"], "standard", tid)
        caps = {r["match_any"][0]: r["max_price"] for r in T["new_3ds_family"]["submodel_price_caps"]}
        self.assertEqual(caps["3ds xl"], 95)
        self.assertIn("ipad air", T["apple_high_liquidity"]["wallapop_keywords"])
        self.assertIn("ipad air", T["apple_high_liquidity"]["vinted_keywords"])
        for tid, tier in (("switch2_consoles", "hot"), ("psp_consoles", "standard"), ("meta_quest_vr", "standard")):
            self.assertTrue(T[tid]["enabled"])
            self.assertEqual(T[tid]["tier"], tier)
            self.assertIn(tid, ce.RADAR_ALLOWED_CEX_CATEGORIES)
        self.assertIn("switch2_consoles", ce.CONSOLE_RADAR_IDS)
        self.assertIn("psp_consoles", ce.CONSOLE_RADAR_IDS)
        self.assertNotIn("meta_quest_vr", ce.CONSOLE_RADAR_IDS)

    def test_every_enabled_radar_is_well_formed(self):
        _cfg, T = targets()
        for tid, t in T.items():
            if not t.get("enabled", True):
                continue
            with self.subTest(radar=tid):
                self.assertLess(t["min_price"], t["direct_buy_max_price"])
                self.assertTrue(t.get("must_contain_any"))
                self.assertTrue(t.get("ai_valuation_guide"))
                for plat in t["platforms"]:
                    field = {"wallapop": "wallapop_keywords", "vinted": "vinted_keywords"}.get(plat, "multi_keywords")
                    kws = t.get(field) or t.get("keywords")
                    self.assertTrue(kws, f"{tid} sin búsqueda para {plat}")
                for rule in t.get("submodel_price_caps", []):
                    self.assertLessEqual(rule["max_price"], t["direct_buy_max_price"], rule)


class TestNewRadarFilters(unittest.TestCase):
    def setUp(self):
        self.cfg, self.T = targets()

    def ok(self, tid, title, desc, price):
        return ce.passes_filters(title, desc, price, self.T[tid], self.cfg["global_blacklist"])

    def test_switch2(self):
        self.assertTrue(self.ok("switch2_consoles", "Nintendo Switch 2 + Mario Kart World", "Como nueva, con caja y dock", 300))
        self.assertTrue(self.ok("switch2_consoles", "Consola Switch 2 negra", "Incluye joy-con 2 y dock", 280))
        self.assertFalse(self.ok("switch2_consoles", "Mario Kart World para Nintendo Switch 2", "Juego precintado", 210))
        self.assertFalse(self.ok("switch2_consoles", "Nintendo Switch OLED blanca", "Perfecta", 250))
        self.assertFalse(self.ok("switch2_consoles", "Funda Nintendo Switch 2", "", 210))
        self.assertFalse(self.ok("switch_std_lite", "Nintendo Switch 2", "", 65))   # sigue fuera de los radares de Switch 1

    def test_psp(self):
        self.assertTrue(self.ok("psp_consoles", "PSP 3000 con 3 juegos", "Funciona perfecta, con cargador", 45))
        self.assertFalse(self.ok("psp_consoles", "God of War Chains of Olympus PSP", "", 25))
        self.assertFalse(self.ok("psp_consoles", "PS Vita 2000 blanca", "", 45))
        self.assertFalse(self.ok("psp_consoles", "PSP Street E1000 negra", "", 40))     # tope E1000: 25 €
        self.assertTrue(self.ok("psp_consoles", "PSP Street E1000 negra", "funciona", 22))

    def test_meta_quest(self):
        self.assertTrue(self.ok("meta_quest_vr", "Meta Quest 3 512GB con mandos", "Como nuevas", 220))
        # Tope 3S: 130 € directos; el filtro deja pasar hasta ×1,30 (169 €) como candidato a regateo
        self.assertTrue(self.ok("meta_quest_vr", "Meta Quest 3S 128GB", "", 150))
        self.assertFalse(self.ok("meta_quest_vr", "Meta Quest 3S 128GB", "", 175))
        self.assertTrue(self.ok("meta_quest_vr", "Meta Quest 3S 128GB", "", 120))
        self.assertFalse(self.ok("meta_quest_vr", "Meta Quest 2 128GB", "", 150))
        self.assertFalse(self.ok("meta_quest_vr", "Correa con batería para Meta Quest 3", "", 120))

    def test_raised_caps(self):
        # Topes directos nuevos; el filtro admite hasta ×1,30 como candidato a regateo
        self.assertTrue(self.ok("new_3ds_family", "Nintendo 3DS XL azul", "con cargador", 90))
        self.assertTrue(self.ok("new_3ds_family", "Nintendo 3DS XL azul", "con cargador", 120))   # antes: >99 fuera
        self.assertFalse(self.ok("new_3ds_family", "Nintendo 3DS XL azul", "con cargador", 125))
        self.assertTrue(self.ok("switch_oled", "Nintendo Switch OLED blanca", "con dock y joy-con", 125))
        self.assertTrue(self.ok("switch_std_lite", "Nintendo Switch V2 completa", "con dock y joy-con", 68))
        self.assertTrue(self.ok("apple_high_liquidity", "iPad Air 5 M1 64GB wifi", "perfecto estado", 210))
        self.assertFalse(self.ok("apple_high_liquidity", "iPad Air 5 M1 64GB wifi", "perfecto estado", 290))

    def test_direct_buy_uses_the_new_caps(self):
        """En el cálculo, una 3DS XL a 95 € es compra directa (antes el tope directo era 76 €)."""
        _cfg, T = targets()
        ai = {"is_valid_deal": True, "visual_confidence": "alta", "identified_model": "Nintendo 3DS XL",
              "cex_search_query": "Nintendo 3DS XL", "estimated_resale_price": 150}
        cex = {"cash": 151.0, "voucher": 190.0, "sell": 275.0, "box_name": "Nintendo 3DS XL (Sin Cargador), Roja, Caja"}
        approved, score, _opt, profit, _pen, _msg = ce.evaluate_deal_math_and_score(
            price=95.0, landed_cost=103.0, target=T["new_3ds_family"], ai_data=dict(ai), cex_data=cex, min_profit=35)
        self.assertTrue(approved)
        self.assertGreaterEqual(profit, 40)


class TestCexMatchingNewFamilies(unittest.TestCase):
    def m(self, bname, cat, q, tid, ctx=""):
        return ce._box_matches_query_and_radar(bname, cat, clean_q=q, target={"id": tid}, context_text=ctx or q)

    def test_switch2_never_crosses_with_switch1(self):
        s2 = "Switch 2 Consola, 256GB + Joy-Con 2 Negro, (Sin DLC) Rebajada"
        v2 = "Nintendo Switch, HAC-001-01 32GB + Joy-Con Gris, Caja"
        self.assertTrue(self.m(s2, "Switch 2 Consolas", "Nintendo Switch 2 Consola", "switch2_consoles", "Nintendo Switch 2 con caja"))
        self.assertFalse(self.m(v2, "Switch Consolas", "Nintendo Switch 2 Consola", "switch2_consoles", "Nintendo Switch 2"))
        self.assertFalse(self.m(s2, "Switch 2 Consolas", "Nintendo Switch Consola", "switch_std_lite", "Nintendo Switch V2 completa"))
        self.assertTrue(self.m(v2, "Switch Consolas", "Nintendo Switch Consola", "switch_std_lite", "Nintendo Switch V2 completa"))

    def test_psp_models(self):
        self.assertTrue(self.m("PSP Slim & Lite 3000 Lila, Sin Caja", "PSP Consolas", "PSP 3000", "psp_consoles"))
        self.assertFalse(self.m('PSP E1000 "Street" Negra, Rebajada', "PSP Consolas", "PSP 3000", "psp_consoles"))
        self.assertTrue(self.m('PSP E1000 "Street" Negra, Rebajada', "PSP Consolas", "PSP E1000 Street", "psp_consoles"))
        self.assertFalse(self.m("PS Vita Slim Negro Wifi, Sin Caja", "PS Vita Consolas", "PSP 3000", "psp_consoles"))

    def test_quest_headset_only_and_3_vs_3s(self):
        q3 = "Gafas de realidad virtual Meta Quest 3 (Con Controladores) 512GB, B"
        self.assertTrue(self.m(q3, "VR Tech", "Meta Quest 3 512GB", "meta_quest_vr"))
        self.assertFalse(self.m("Kiwi SPC Battery Strap for Meta Quest 3S/3", "VR Tech", "Meta Quest 3", "meta_quest_vr"))
        self.assertFalse(self.m("Gafas de realidad virtual Meta Quest 3S 128GB (Con Controladores)", "VR Tech",
                                "Meta Quest 3 512GB", "meta_quest_vr"))

    def test_product_families(self):
        self.assertEqual(ce.cex_product_family("Nintendo Switch 2 256GB"), "switch2")
        self.assertEqual(ce.cex_product_family("Nintendo Switch OLED"), "switch")
        self.assertEqual(ce.cex_product_family("PSP 3000 Slim"), "psp")
        self.assertEqual(ce.cex_product_family("Meta Quest 3S"), "meta_quest")
        for q in ("Nintendo Switch 2 Consola", "PSP Consola", "Meta Quest 3"):
            self.assertIn(q, ce.CEX_MASTER_QUERIES)


if __name__ == "__main__":
    unittest.main()
