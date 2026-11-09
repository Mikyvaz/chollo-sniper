# -*- coding: utf-8 -*-
"""
Test Suite for Task 4.1:
- Multilingual Haggle Message (FR, BE, IT, PT, NL, ES) with dual signatures
- VintedScraper / VintedClient class structure (refresh_cookies, fetch_items, search_items)
- API JSON mapping & RSC fallback extraction
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, "/app")
import unittest
from unittest.mock import MagicMock, patch

import core_engine
from vinted_sniper import VintedClient, VintedScraper


class TestTask41MultilingualHaggle(unittest.TestCase):

    def test_multilingual_haggle_dual_signature_and_languages(self):
        # 1. Test 3-argument signature: (title, price, country_tag)
        msg_fr = core_engine.generate_multilingual_haggle_msg('Switch OLED', 80, 'FR')
        self.assertIn("Bonjour", msg_fr)
        self.assertIn("80€", msg_fr)

        msg_be = core_engine.generate_multilingual_haggle_msg('Switch OLED', 85, 'BE')
        self.assertIn("Bonjour", msg_be)
        self.assertIn("85€", msg_be)

        msg_it = core_engine.generate_multilingual_haggle_msg('Switch OLED', 75, 'IT')
        self.assertIn("Ciao", msg_it)
        self.assertIn("75€", msg_it)

        msg_pt = core_engine.generate_multilingual_haggle_msg('Switch OLED', 70, 'PT')
        self.assertIn("Olá", msg_pt)
        self.assertIn("70€", msg_pt)

        msg_nl = core_engine.generate_multilingual_haggle_msg('Switch OLED', 90, 'NL')
        self.assertIn("Hallo", msg_nl)
        self.assertIn("90€", msg_nl)

        msg_es = core_engine.generate_multilingual_haggle_msg('Switch OLED', 65, 'ES')
        self.assertIn("¡Hola!", msg_es)
        self.assertIn("65€", msg_es)

        # 2. Test 5-argument classic signature: (title, description, country_tag, offer_price, model_name)
        msg_5_fr = core_engine.generate_multilingual_haggle_msg('Switch', 'Tres bon etat', 'FR', 77, 'Switch OLED')
        self.assertIn("Bonjour", msg_5_fr)
        self.assertIn("77€", msg_5_fr)

        msg_5_it = core_engine.generate_multilingual_haggle_msg('Switch', 'Ottimo stato', 'IT', 72, 'Switch OLED')
        self.assertIn("Ciao", msg_5_it)
        self.assertIn("72€", msg_5_it)

        msg_5_es = core_engine.generate_multilingual_haggle_msg('Switch', 'Muy buen estado', 'ES', 60, 'Switch OLED')
        self.assertIn("¡Hola!", msg_5_es)
        self.assertIn("60€", msg_5_es)

        # 3. Test text heuristic when country_tag is EU / blank
        msg_heur_fr = core_engine.generate_multilingual_haggle_msg('Console envoi rapide avec boîte', 50, 'EU')
        self.assertIn("Bonjour", msg_heur_fr)

        msg_heur_it = core_engine.generate_multilingual_haggle_msg('Vendo console perfettamente funzionante', 50, '')
        self.assertIn("Ciao", msg_heur_it)

        msg_heur_pt = core_engine.generate_multilingual_haggle_msg('Vendo com comandos e caixa', 50, 'EU')
        self.assertIn("Olá", msg_heur_pt)

        msg_heur_nl = core_engine.generate_multilingual_haggle_msg('In zeer goede staat met doos', 50, '')
        self.assertIn("Hallo", msg_heur_nl)

        print("[OK] Test multilingual haggle passed for all countries and dual signatures!")


class TestTask41VintedScraperAPI(unittest.TestCase):

    def test_vinted_scraper_alias_and_methods(self):
        self.assertIs(VintedScraper, VintedClient)
        self.assertTrue(hasattr(VintedScraper, "refresh_cookies"))
        self.assertTrue(hasattr(VintedScraper, "fetch_items"))
        self.assertTrue(hasattr(VintedScraper, "search_items"))
        print("[OK] VintedScraper alias and method signatures verified!")

    def test_vinted_api_json_parsing(self):
        client = VintedClient.__new__(VintedClient)
        client.health_monitor = MagicMock()
        client.health_monitor.is_in_cooldown.return_value = False
        client.ensure_fresh_session = MagicMock()
        client.session = MagicMock()

        # Mock API response returning 200 with JSON items
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": 987654321,
                    "title": "Nintendo Switch OLED Blanca",
                    "price": {"amount": "140.00", "currencyCode": "EUR"},
                    "total_item_price": {"amount": "147.70", "currencyCode": "EUR"},
                    "photos": [
                        {"url": "https://images1.vinted.net/thumb.jpg", "full_size_url": "https://images1.vinted.net/hd_photo1.jpg"},
                        {"url": "https://images1.vinted.net/hd_photo2.jpg"}
                    ],
                    "user": {
                        "id": 554433,
                        "login": "gamer_france",
                        "country_code": "FR"
                    },
                    "url": "/items/987654321-nintendo-switch-oled-blanca"
                }
            ]
        }
        client.session.get.return_value = mock_resp

        items = client.fetch_items({"keywords": ["switch", "oled"], "max_price": 180})
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "987654321")
        self.assertEqual(item["title"], "Nintendo Switch OLED Blanca")
        self.assertEqual(item["price"], 140.0)
        self.assertEqual(item["total_item_price"], 147.70)
        self.assertEqual(item["photo"], "https://images1.vinted.net/hd_photo1.jpg")
        self.assertEqual(item["photos"], ["https://images1.vinted.net/hd_photo1.jpg", "https://images1.vinted.net/hd_photo2.jpg"])
        self.assertEqual(item["country_code"], "FR")
        self.assertEqual(item["seller"]["country_code"], "FR")
        self.assertEqual(item["seller"]["name"], "gamer_france")
        print("[OK] API JSON direct mapping verified with HD photos and FR country code!")

    def test_vinted_fallback_to_rsc_catalog(self):
        client = VintedClient.__new__(VintedClient)
        client.health_monitor = MagicMock()
        client.health_monitor.is_in_cooldown.return_value = False
        client.ensure_fresh_session = MagicMock()
        client.session = MagicMock()

        # Step 1: API returns 404
        api_resp = MagicMock()
        api_resp.status_code = 404

        # Step 2: Catalog returns 200 with RSC array
        catalog_resp = MagicMock()
        catalog_resp.status_code = 200
        # Build RSC payload snippet
        rsc_json = (
            '\\"items\\":['
            '{\\"id\\":101010,\\"productItem\\":{'
            '\\"id\\":101010,\\"title\\":\\"PS Vita Slim\\",'
            '\\"url\\":\\"/items/101010-ps-vita\\",'
            '\\"price\\":{\\"amount\\":\\"75.00\\"},'
            '\\"totalItemPrice\\":{\\"amount\\":\\"79.45\\"},'
            '\\"photos\\":[{\\"url\\":\\"https://images1.vinted.net/vita_hd.jpg\\"}],'
            '\\"user\\":{\\"id\\":777,\\"login\\":\\"luigi_roma\\",\\"country_code\\":\\"IT\\"},'
            '\\"itemBox\\":{\\"firstLine\\":\\"Sony\\",\\"secondLine\\":\\"Muy bueno\\"}'
            '}}'
            ']'
        )
        catalog_resp.text = f'<html><body><!-- {rsc_json} --></body></html>'

        # Set session.get to return api_resp on first call, catalog_resp on second call
        client.session.get.side_effect = [api_resp, catalog_resp]

        items = client.fetch_items({"keywords": ["ps", "vita"], "max_price": 90})
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "101010")
        self.assertEqual(item["title"], "PS Vita Slim")
        self.assertEqual(item["price"], 75.0)
        self.assertEqual(item["photo"], "https://images1.vinted.net/vita_hd.jpg")
        self.assertEqual(item["country_code"], "IT")
        self.assertEqual(item["seller"]["country_code"], "IT")
        self.assertEqual(item["brand_title"], "Sony")
        print("[OK] Fallback to RSC catalog verified with IT country code and itemBox parsing!")

    def test_live_vinted_fetch_execution(self):
        scraper = VintedScraper()
        # Query for Nintendo Switch OLED
        items = scraper.fetch_items({"keywords": ["switch", "oled"], "max_price": 200})
        self.assertIsInstance(items, list)
        print(f"[OK] Live Vinted fetch returned {len(items)} items without errors!")
        if items:
            sample = items[0]
            print(f"[OK] Sample item: ID={sample.get('id')} Title='{sample.get('title')[:30]}...' Price={sample.get('price')}€ Photos={len(sample.get('photos', []))}")


if __name__ == "__main__":
    unittest.main()
