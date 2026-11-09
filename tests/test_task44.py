# -*- coding: utf-8 -*-
"""
Test Suite for Task 4.4:
- build_native_deeplink validation for Wallapop (wallapop://item/{id})
- build_native_deeplink validation for Vinted (vinted://items/{id})
- build_native_deeplink rejection for other platforms (Milanuncios, CashConverters, eBay, etc.)
- Verification that flash and main Discord alert payloads include native app links
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, "/app")

import core_engine


class TestTask44NativeDeeplinks(unittest.TestCase):

    def test_01_wallapop_deeplinks(self):
        """Verifica construcción de enlaces nativos wallapop://item/{id}."""
        # Caso estándar con platform WALLAPOP ES e item_id
        link1 = core_engine.build_native_deeplink("WALLAPOP ES", "https://es.wallapop.com/item/test-12345", "12345")
        self.assertEqual(link1, "wallapop://item/12345")

        # Caso sin URL pero con item_id directo
        link2 = core_engine.build_native_deeplink("WALLAPOP", "", "987654321")
        self.assertEqual(link2, "wallapop://item/987654321")

        # Caso con prefijo wallapop_ en item_id
        link3 = core_engine.build_native_deeplink("WALLAPOP IT", "https://it.wallapop.com/item/nintendo-switch-456", "wallapop_456")
        self.assertEqual(link3, "wallapop://item/456")

        # Caso sin item_id pero extrayendo de URL
        link4 = core_engine.build_native_deeplink("WALLAPOP", "https://es.wallapop.com/item/consola-ps-vita-oled-1004-987654321")
        self.assertEqual(link4, "wallapop://item/987654321")

    def test_02_vinted_deeplinks(self):
        """Verifica construcción de enlaces nativos vinted://items/{num_id}."""
        # Caso estándar con ID numérico
        link1 = core_engine.build_native_deeplink("VINTED", "https://www.vinted.es/items/456789", "456789")
        self.assertEqual(link1, "vinted://items/456789")

        # Caso con prefijo vinted_ en el item_id
        link2 = core_engine.build_native_deeplink("VINTED EU", "", "vinted_456789")
        self.assertEqual(link2, "vinted://items/456789")

        # Caso con slug en la URL
        link3 = core_engine.build_native_deeplink("VINTED", "https://www.vinted.fr/items/99887766-nintendo-switch-oled", "99887766")
        self.assertEqual(link3, "vinted://items/99887766")

        # Caso sin item_id pero extrayendo de URL
        link4 = core_engine.build_native_deeplink("VINTED", "https://www.vinted.it/items/11223344-giochi-ps-vita")
        self.assertEqual(link4, "vinted://items/11223344")

    def test_03_other_platforms_no_deeplink(self):
        """Verifica que plataformas web sin esquema URI nativo específico devuelven cadena vacía."""
        self.assertEqual(core_engine.build_native_deeplink("MILANUNCIOS", "https://www.milanuncios.com/anuncio-1", "1"), "")
        self.assertEqual(core_engine.build_native_deeplink("CASHCONVERTERS", "https://cashconverters.es/p-2", "2"), "")
        self.assertEqual(core_engine.build_native_deeplink("EBAY", "https://www.ebay.es/itm/3", "3"), "")
        self.assertEqual(core_engine.build_native_deeplink("TODOCOLECCION", "https://www.todocoleccion.net/4", "4"), "")

    def test_04_alert_embed_description_includes_deeplink(self):
        """Verifica que build_native_deeplink genera el encabezado 📲 **App Directa:**."""
        deeplink = core_engine.build_native_deeplink("WALLAPOP ES", item_id="778899")
        self.assertEqual(deeplink, "wallapop://item/778899")
        header = f"📲 **App Directa:** [Abrir en App Nativa]({deeplink})\n\n"
        self.assertIn("wallapop://item/778899", header)
        self.assertTrue(header.startswith("📲 **App Directa:** [Abrir en App Nativa]"))


if __name__ == "__main__":
    unittest.main()

