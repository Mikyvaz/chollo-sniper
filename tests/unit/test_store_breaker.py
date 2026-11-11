"""T1.10: circuit breaker exponencial por tienda (store_breaker.StoreBreaker)."""
import json
import os
import tempfile
import unittest

import _harness  # noqa: F401  (debe ir primero)
import store_breaker
from store_breaker import StoreBreaker


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class TestStoreBreaker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "store_breakers.json")
        self.clock = Clock()

    def mk(self):
        return StoreBreaker(self.path, clock=self.clock)

    def test_cooldowns_exponenciales_con_tope(self):
        b = self.mk()
        got = []
        for _ in range(9):
            cd, _n = b.record_block("milanuncios")
            got.append(cd)
            self.assertFalse(b.allow("milanuncios"))
            self.clock.t += cd + 1
            self.assertTrue(b.allow("milanuncios"))
        self.assertEqual(got, [900, 1800, 3600, 7200, 14400, 28800, 43200, 43200, 43200])

    def test_exito_reinicia(self):
        b = self.mk()
        b.record_block("milanuncios")
        b.record_block("milanuncios")
        self.clock.t += 5000
        self.assertTrue(b.record_success("milanuncios"))
        self.assertTrue(b.allow("milanuncios"))
        cd, _ = b.record_block("milanuncios")
        self.assertEqual(cd, 900)

    def test_exito_sin_estado_abierto_no_notifica(self):
        b = self.mk()
        self.assertFalse(b.record_success("milanuncios"))
        self.assertFalse(os.path.exists(self.path))

    def test_estado_sobrevive_a_nueva_instancia(self):
        b = self.mk()
        b.record_block("milanuncios")
        cd, _ = b.record_block("milanuncios")
        self.assertEqual(cd, 1800)
        b2 = self.mk()
        self.assertFalse(b2.allow("milanuncios"))
        self.assertAlmostEqual(b2.open_until("milanuncios"), self.clock.t + 1800)
        cd3, _ = b2.record_block("milanuncios")
        self.assertEqual(cd3, 3600)
        self.assertTrue(b2.allow("cashconverters"))

    def test_tiendas_independientes(self):
        b = self.mk()
        b.record_block("milanuncios")
        self.assertFalse(b.allow("milanuncios"))
        self.assertTrue(b.allow("ebay"))

    def test_notificaciones_acotadas(self):
        b = self.mk()
        notes = 0
        for _ in range(6):
            cd, notify = b.record_block("milanuncios")
            notes += bool(notify)
            self.clock.t += cd + 1
        if b.record_success("milanuncios"):
            notes += 1
        self.assertLessEqual(notes, 3)
        self.assertEqual(notes, 2)  # primer bloqueo + recuperación

    def test_notifica_al_alcanzar_el_tope_una_sola_vez(self):
        b = self.mk()
        flags = []
        for _ in range(10):
            cd, notify = b.record_block("milanuncios")
            flags.append(notify)
            self.clock.t += cd + 1
        self.assertTrue(flags[0])
        self.assertTrue(flags[6])  # 7.º bloqueo: 43200 s = CAP
        self.assertEqual(sum(flags), 2)

    def test_escritura_atomica_y_json_valido(self):
        b = self.mk()
        b.record_block("milanuncios")
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["milanuncios"]["failures"], 1)
        self.assertFalse([n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")])

    def test_archivo_corrupto_no_rompe(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{no es json")
        b = self.mk()
        self.assertTrue(b.allow("milanuncios"))
        cd, _ = b.record_block("milanuncios")
        self.assertEqual(cd, 900)

    def test_store_enabled_en_config(self):
        self.assertTrue(store_breaker.store_enabled({}, "milanuncios"))
        self.assertTrue(store_breaker.store_enabled({"stores": {}}, "milanuncios"))
        cfg = {"stores": {"milanuncios": {"enabled": False}}}
        self.assertFalse(store_breaker.store_enabled(cfg, "milanuncios"))
        self.assertTrue(store_breaker.store_enabled(cfg, "ebay"))


if __name__ == "__main__":
    unittest.main()
