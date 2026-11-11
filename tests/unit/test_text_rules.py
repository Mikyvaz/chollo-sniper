"""T1.1: matcher con límites de palabra y negación (text_rules + Puerta 0 + blacklists)."""
import unittest

import _harness  # noqa: F401  (debe ir primero)
import core_engine as ce
from fraud_signals import evaluate_fraud_risk
from text_rules import has_token, norm

CFG = ce.load_config()
TARGETS = {t["id"]: t for t in CFG["targets"]}
GB = CFG["global_blacklist"]


def passes(radar, title, desc, price):
    return ce.passes_filters(title, desc, price, TARGETS[radar], GB)


class TestMatcher(unittest.TestCase):
    def test_limites_de_palabra(self):
        self.assertFalse(has_token(norm("sin golpes ni rayadas"), "pes "))
        self.assertFalse(has_token(norm("llego mañana"), "lego "))
        self.assertFalse(has_token(norm("no cargador original"), "no carga"))
        self.assertTrue(has_token(norm("fifa 24 para ps5"), "fifa "))

    def test_plurales_y_guiones(self):
        self.assertTrue(has_token(norm("acepto bizums"), "bizum"))
        self.assertTrue(has_token(norm("para despiece"), "despiece"))
        self.assertTrue(has_token(norm("lector  blu-ray roto"), "lector blu-ray"))

    def test_acentos_y_apostrofes(self):
        self.assertTrue(has_token(norm("Luigi's Mansion 3"), "luigis mansion"))
        self.assertTrue(has_token(norm("pantalla dañada"), "pantalla danada"))

    def test_acento_final_distingue_comprar_de_comprado(self):
        self.assertFalse(has_token(norm("Se compró nueva en el Corte Inglés"), "compro"))
        self.assertFalse(has_token(norm("Bon état acheté en 2025"), "achète"))
        self.assertTrue(has_token(norm("Compro consolas"), "compro"))
        self.assertTrue(has_token(norm("J'achète des consoles"), "achète"))

    def test_prefijo_para_identificar_modelo(self):
        self.assertTrue(has_token(norm("ipad 9th génération"), "ipad 9", negatable=False, prefix=True))
        self.assertFalse(has_token(norm("ipad 9th génération"), "ipad 9", negatable=False))
        self.assertFalse(has_token(norm("mac mini m10"), "m1", negatable=False, prefix=True))

    def test_negacion(self):
        self.assertFalse(has_token(norm("no acepto bizum"), "bizum"))
        self.assertFalse(has_token(norm("sin problemas con drift"), "con drift"))
        self.assertTrue(has_token(norm("sin problemas. con drift"), "con drift"))  # la puntuación corta
        self.assertTrue(has_token(norm("solo bizum"), "bizum"))
        self.assertTrue(has_token(norm("no enciende"), "no enciende"))             # token ya negativo
        self.assertTrue(has_token(norm("sin bizum nada"), "bizum", negatable=False))


class TestLegitimos(unittest.TestCase):
    """Filas 4-12 de §1.3: deben pasar."""
    CASES = [
        ("switch_std_lite", "Nintendo Switch V2 completa", "Incluye joy-con, dock y cargador original. Funciona perfecta.", 60),
        ("switch_std_lite", "Switch neón 32gb", "Sin golpes ni rayadas, con caja", 60),
        ("new_3ds_family", "New 3DS XL azul", "Pantalla superior e inferior sin rayadas, funciona perfecta", 95),
        ("new_3ds_family", "Nintendo 3DS XL", "Se vende sin caja, no cargador. Funciona.", 70),
        # 160 € de la sonda supera el tope de submodelo de iPad 9 (118 € x 1,3 = 153 €): se prueba dentro del tope.
        ("apple_high_liquidity", "iPad 9 64GB wifi", "Sin bloqueo iCloud, desvinculado. Batería 90%", 120),
        ("ps5_consoles_gta6", "PS5 Slim digital", "Con 2 mandos y 3 juegos ps5. No acepto bizum, solo envío por la app", 280),
        ("ps5_consoles_gta6", "PS5 slim lector", "Regalo mando de ps4. Perfecta", 290),
        ("switch_oled", "Nintendo Switch OLED", "Joy-con sin problemas con drift, como nueva", 95),
        ("steam_deck_handhelds", "Steam Deck 512GB", "Funciona perfecto, luz azul de carga ok", 240),
    ]

    def test_pasan(self):
        for radar, title, desc, price in self.CASES:
            with self.subTest(title=title):
                self.assertTrue(passes(radar, title, desc, price))

    def test_replay_regresiones_medidas(self):
        """Alertas reales que una primera versión del matcher bloqueaba por error."""
        self.assertTrue(passes("ps5_consoles_gta6", "Ps5 + manettes", "Bon état acheté en 2025. Pas de bug", 250))
        self.assertTrue(passes("ps5_consoles_gta6", "Play Station 5 Slim con lector",
                               "En perfecto estado. Se compró nueva en el Corte Inglés hace un año", 330))
        self.assertTrue(passes("apple_high_liquidity", "Ipad 9th génération 64go", "Modèle : iPad (9ème génération). État neuf", 110))

    def test_pagos_negados_pasan(self):
        self.assertTrue(passes("ps5_consoles_gta6", "PS5 Slim lector", "No acepto bizum ni transferencias", 280))
        self.assertTrue(passes("ps5_consoles_gta6", "PS5 Slim lector", "Envío sin coste, pago dentro de la app", 280))


class TestSiguenBloqueando(unittest.TestCase):
    CASES = [
        ("ps5_consoles_gta6", "PS5 Slim lector", "Solo bizum", 280),
        ("ps5_consoles_gta6", "PS5 Slim lector", "Pago por bizum", 280),
        ("new_3ds_family", "New 3DS XL azul", "Pantalla superior rota", 95),
        ("switch_oled", "Nintendo Switch OLED", "Joycon drift", 95),
        ("switch_oled", "Nintendo Switch OLED", "No enciende", 95),
        ("steam_deck_handhelds", "Steam Deck 512GB", "Luz azul parpadeando", 240),
        ("apple_high_liquidity", "iPad 9 64GB wifi", "iCloud bloqueado", 120),
        ("apple_high_liquidity", "iPad 9 64GB wifi", "Cuenta iCloud activa", 120),
        ("switch_oled", "Nintendo Switch OLED", "Se vende para piezas", 95),
        ("ps5_consoles_gta6", "Busco PS5", "", 280),
    ]

    def test_bloquean(self):
        for radar, title, desc, price in self.CASES:
            with self.subTest(desc=desc or title):
                self.assertFalse(passes(radar, title, desc, price))

    def test_fraude_contextual(self):
        r = evaluate_fraud_risk("New 3DS XL", "Pantalla inferior partida", 95, radar_id="new_3ds_family")
        self.assertTrue(r["is_fraud"])
        r = evaluate_fraud_risk("New 3DS XL", "Pantalla superior e inferior sin rayadas", 95, radar_id="new_3ds_family")
        self.assertFalse(r["is_fraud"])

    def test_contacto_frances_con_elision(self):
        for txt in ("Avant d'acheter, écrivez-moi", "avant d’acheter contactez-moi", "Avant d acheter"):
            with self.subTest(txt=txt):
                r = evaluate_fraud_risk("Switch OLED", txt, 200, radar_id="switch_oled")
                self.assertIn("CONTACT_EXTERNAL", [h["code"] for h in r["hits"]])
        self.assertTrue(has_token(norm("J'achète des consoles"), "achète"))
        self.assertTrue(has_token(norm("avant d'acheter"), "avant d'acheter"))

    def test_negaciones_del_pago_antiguo(self):
        """Todas las formas que cubría el negation_re anterior siguen negando el pago."""
        for txt in ("Nada de bizum, solo envío por la app", "No acepto bizum", "No hago bizum", "no uso bizum",
                    "No quiero transferencias", "no admito bizum", "no recibo bizum",
                    "Ni bizum ni transferencia", "ni hago bizum", "Nada de transferencias"):
            with self.subTest(txt=txt):
                r = evaluate_fraud_risk("PS5 Slim", txt, 280, radar_id="ps5_consoles_gta6")
                self.assertNotIn("PAYMENT_EXTERNAL", [h["code"] for h in r["hits"]])
        r = evaluate_fraud_risk("PS5 Slim", "Solo bizum, nada de envíos", 280, radar_id="ps5_consoles_gta6")
        self.assertIn("PAYMENT_EXTERNAL", [h["code"] for h in r["hits"]])

    def test_contacto_negado(self):
        r = evaluate_fraud_risk("PS5 Slim", "No tengo whatsapp, solo por la app", 280, radar_id="ps5_consoles_gta6")
        self.assertNotIn("CONTACT_EXTERNAL", [h["code"] for h in r["hits"]])
        r = evaluate_fraud_risk("PS5 Slim", "Escríbeme por whatsapp", 280, radar_id="ps5_consoles_gta6")
        self.assertIn("CONTACT_EXTERNAL", [h["code"] for h in r["hits"]])


if __name__ == "__main__":
    unittest.main()
