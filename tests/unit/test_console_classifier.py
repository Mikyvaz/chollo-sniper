"""T1.2: clasificador determinista consola / juego suelto / accesorio suelto."""
import json
import os
import unittest

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce
from text_rules import classify_console_listing

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REJECT = ("game_only", "accessory_only", "out_of_scope")
CFG = ce.load_config()
TARGETS = {t["id"]: t for t in CFG["targets"]}
GB = CFG["global_blacklist"]


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


class TestClassifier(unittest.TestCase):
    def test_casos_fijos(self):
        c = classify_console_listing
        self.assertEqual(c("Mario Kart 8 Deluxe Nintendo Switch", "switch_std_lite"), "game_only")
        self.assertEqual(c("Joycon Nintendo switch", "switch_std_lite"), "accessory_only")
        self.assertNotIn(c("Nintendo switch et 3 jeux", "switch_std_lite"), REJECT)
        self.assertNotIn(c("Ps5 plus 2 manette", "ps5_consoles_gta6"), REJECT)
        self.assertEqual(c("Ps5 édition digitale", "ps5_consoles_gta6"), "console")
        self.assertEqual(c("Nintendo Switch 2", "switch_oled"), "out_of_scope")
        self.assertNotIn(c("Nintendo Switch 2", "ps_vita"), ("out_of_scope",))

    def test_dataset_negativos(self):
        neg = _load("console_neg.json")
        rej = sum(1 for t in neg if classify_console_listing(t) in REJECT)
        self.assertGreaterEqual(rej / len(neg), 0.55, f"{rej}/{len(neg)}")

    def test_dataset_positivos_solo_pierde_basura_conocida(self):
        pos = _load("console_pos.json")
        with open(os.path.join(DATA, "known_junk_alerts.txt"), encoding="utf-8") as f:
            junk = {ln.strip() for ln in f if ln.strip()}
        self.assertEqual(len(junk), 7)
        lost = [t for t in pos if classify_console_listing(t) in REJECT and t not in junk]
        self.assertEqual(lost, [])


class TestIntegracion(unittest.TestCase):
    def _p(self, radar, title, desc, price):
        return ce.passes_filters(title, desc, price, TARGETS[radar], GB)

    def test_juegos_y_accesorios_se_rechazan(self):
        self.assertFalse(self._p("switch_std_lite", "Mario Kart 8 Deluxe Nintendo Switch", "Juego en perfecto estado", 28))
        self.assertFalse(self._p("switch_std_lite", "Luigi's mansion 3 Nintendo switch", "", 35))
        self.assertFalse(self._p("switch_std_lite", "Joycon Nintendo switch", "Funcionan bien", 41))

    def test_consolas_legitimas_pasan(self):
        self.assertTrue(self._p("switch_std_lite", "Nintendo Switch V2 completa", "Incluye joy-con, dock y cargador original. Funciona perfecta.", 60))
        self.assertTrue(self._p("switch_std_lite", "Switch neón 32gb", "Sin golpes ni rayadas, con caja", 60))
        self.assertTrue(self._p("new_3ds_family", "New 3DS XL azul", "Pantalla superior e inferior sin rayadas, funciona perfecta", 95))
        self.assertTrue(self._p("switch_oled", "Nintendo Switch OLED", "Joy-con sin problemas con drift, como nueva", 95))
        self.assertTrue(self._p("steam_deck_handhelds", "Steam Deck 512GB", "Funciona perfecto, luz azul de carga ok", 240))

    def test_consola_que_dice_incluye_juegos_en_descripcion_pasa(self):
        # Antes: la descripción con «juegos» descartaba consolas sin «hardware» en el título.
        self.assertTrue(self._p("switch_oled", "Nintendo Switch OLED blanca", "Incluye 2 juegos y funda", 95))
        self.assertTrue(self._p("ps5_consoles_gta6", "PS5 Slim digital", "Con 2 mandos y 3 juegos ps5. No acepto bizum", 280))


if __name__ == "__main__":
    unittest.main()
