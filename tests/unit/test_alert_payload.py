import tests.unit._harness  # noqa: F401
import json
import sqlite3
import unittest
from unittest import mock

import core_engine as ce


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"id": "msg-1"}
        self.headers = {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _collect_urls(obj):
    urls = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("url", "click") and isinstance(v, str) and v:
                urls.append(v)
            else:
                urls.extend(_collect_urls(v))
    elif isinstance(obj, list):
        for item in obj:
            urls.extend(_collect_urls(item))
    return urls


def _embed_total_chars(embed):
    total = len(embed.get("title", "") or "")
    total += len(embed.get("description", "") or "")
    total += len((embed.get("footer") or {}).get("text", "") or "")
    for field in embed.get("fields", []):
        total += len(field.get("name", "") or "")
        total += len(field.get("value", "") or "")
    return total


class TestAlertPayload(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE deals (deal_id TEXT PRIMARY KEY, alert_context_json TEXT)")
        self.conn.execute("INSERT INTO deals (deal_id, alert_context_json) VALUES ('WAL_101', '')")
        self.conn.execute("INSERT INTO deals (deal_id, alert_context_json) VALUES ('VIN_202', '')")
        self.conn.commit()
        self.patches = [
            mock.patch.object(ce, "BOT_TOKEN", "bot-token-test"),
            mock.patch.object(ce, "ALERTS_CHANNEL_ID", "999888"),
            mock.patch.object(ce, "WEBHOOK_URL", ""),
            mock.patch.object(ce, "ALERT_USER_ID", "424242"),
            mock.patch.object(ce, "NTFY_TOPIC_URL", "https://ntfy.sh/test-topic"),
            mock.patch.object(ce, "NTFY_TOKEN", "ntfy-tok"),
            mock.patch.object(ce, "register_deal_in_db"),
            mock.patch.object(ce, "generate_multilingual_haggle_msg", return_value="Hola, te ofrezco 110 euros ahora mismo."),
            mock.patch.object(ce.time, "sleep"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.conn.close)

    def _send_discord(self, *, platform="WALLAPOP", item_id="101", title="Consola Switch",
                      description=None, price=85.0, landed_cost=90.0, net_profit=45.0,
                      optimal_price=135.0, ai_data=None, cex_data=None, seller=None,
                      item_url="https://es.wallapop.com/item/switch-oled-101?utm_source=x",
                      checkout_url="https://es.wallapop.com/checkout/101",
                      image_url="https://cdn.wallapop.com/images/101.jpg",
                      seller_city="Sevilla", country_tag="ES", should_push=True, quiet_reason=""):
        if description is None:
            description = "Consola en perfecto estado con caja original y todos sus accesorios. " * 8
        target = {"name": "Radar Switch", "mode": "resale"}
        ai = ai_data or {
            "deal_score": 9,
            "deal_type": "direct_chollo",
            "identified_model": "Nintendo Switch OLED Blanca Edicion Especial Muy Larga Para Probar El Limite De Caracteres",
            "visual_confidence": "alta",
            "visual_notes": "Precintos intactos, sin marcas ni arañazos en pantalla y con dock original incluido en la caja.",
            "cex_cash_net": 20.0,
            "cex_coverage_pct": 122.0,
            "p2p_net_profit": net_profit,
            "p2p_roi_pct": 50.0,
            "estimated_resale_price": optimal_price,
        }
        cex = cex_data if cex_data is not None else {
            "box_id": "CSWIOLEDW",
            "box_name": "Nintendo Switch OLED White Boxed",
            "cash": 110.0,
            "voucher": 145.0,
            "sell": 220.0,
        }
        seller_obj = seller if seller is not None else {
            "name": "Carlos",
            "reviews": 18,
            "rating": 4.9,
            "sales": 24,
            "account_age": "3 años",
        }
        captured = []

        def _fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return _FakeResp(200, {"id": "disc-msg-1"})

        with mock.patch.object(ce, "decide_alert_push", return_value=(should_push, quiet_reason)), \
             mock.patch.object(ce.HTTP_SESSION, "post", side_effect=_fake_post):
            ce.send_discord_alert(
                self.conn, platform, item_id, title, description,
                price, landed_cost, 5.0, target, ai, cex,
                optimal_price=optimal_price, net_profit=net_profit,
                item_url=item_url, checkout_url=checkout_url, image_url=image_url,
                seller_city=seller_city, country_tag=country_tag, seller=seller_obj,
            )
        self.assertEqual(len(captured), 1)
        return captured[0]

    def _assert_common_discord_snapshot(self, payload, expected_model_substr, expected_price_str, expected_profit_str):
        raw_json = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("wallapop://", raw_json)
        self.assertNotIn("vinted://", raw_json)

        lines = payload["content"].splitlines()
        self.assertGreaterEqual(len(lines), 2)
        line1 = lines[0]
        self.assertFalse(line1.startswith("🔕"), f"Línea 1 no debe empezar por 🔕: {line1!r}")
        self.assertLessEqual(len(line1), 110, f"Línea 1 excede 110 chars ({len(line1)}): {line1!r}")
        self.assertIn(expected_model_substr, line1)
        self.assertIn(expected_price_str, line1)
        self.assertIn(expected_profit_str, line1)
        self.assertTrue(lines[1].startswith("https://"), f"Línea 2 debe ser URL https limpia: {lines[1]!r}")

        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], line1)
        self.assertLessEqual(len(embed.get("description", "")), 200)
        self.assertLessEqual(len(embed.get("fields", [])), 4)
        allowed_names = {"Veredicto IA", "CeX", "Vendedor", "Acción"}
        for f in embed.get("fields", []):
            self.assertIn(f["name"], allowed_names)
        self.assertLessEqual(_embed_total_chars(embed), 2500)

        urls = _collect_urls(payload)
        self.assertTrue(urls)
        for u in urls:
            self.assertTrue(u.startswith("https://"), f"URL sin https:// encontrada: {u!r}")
        return lines, embed

    def test_snapshot_direct_alert(self):
        payload = self._send_discord(should_push=True)
        lines, embed = self._assert_common_discord_snapshot(payload, "Nintendo Switch OLED", "85 €", "+45 €")
        self.assertIn("WALLAPOP", lines[0])
        self.assertIn("Sevilla", lines[0])
        self.assertEqual(lines[1], "https://es.wallapop.com/item/switch-oled-101")
        self.assertIn("<@424242>", lines[2])
        self.assertNotIn("flags", payload)
        self.assertEqual([f["name"] for f in embed["fields"]], ["Veredicto IA", "CeX", "Vendedor", "Acción"])

    def test_snapshot_offer_alert(self):
        ai = {
            "deal_score": 8,
            "deal_type": "offer_opportunity",
            "identified_model": "PS5 Slim Digital",
            "recommended_offer": 110,
            "offer_landed_cost": 116.0,
            "offer_discount_pct": 21,
            "visual_confidence": "alta",
            "visual_notes": "Caja original y mando DualSense incluidos.",
            "estimated_resale_price": 180.0,
        }
        payload = self._send_discord(
            platform="VINTED", item_id="202", title="PS5", price=140.0, landed_cost=146.0,
            net_profit=50.0, optimal_price=180.0, ai_data=ai,
            item_url="https://www.vinted.es/items/202-ps5?ref=1",
            checkout_url="", seller_city="Francia", country_tag="FR", should_push=True,
        )
        lines, _embed = self._assert_common_discord_snapshot(payload, "PS5 Slim Digital", "140 €", "+50 €")
        self.assertTrue(lines[0].startswith("🤝"))
        self.assertIn("ofertar 110 €", lines[0])
        self.assertIn("pide 140 €", lines[0])
        self.assertIn("<@424242>", lines[2])

    def test_snapshot_high_risk_alert(self):
        ai = {
            "deal_score": 7,
            "deal_type": "high_risk_opportunity",
            "is_anomalously_low": True,
            "identified_model": "Steam Deck OLED 512GB",
            "visual_confidence": "media",
            "visual_notes": "Solo una foto borrosa.",
        }
        payload = self._send_discord(
            price=120.0, landed_cost=125.0, net_profit=95.0, ai_data=ai, should_push=True,
        )
        lines, _embed = self._assert_common_discord_snapshot(payload, "Steam Deck OLED 512GB", "120 €", "+95 €")
        self.assertTrue(lines[0].startswith("⚠️"))

    def test_snapshot_silent_alert_puts_reason_at_end(self):
        payload = self._send_discord(
            ai_data={
                "deal_score": 8,
                "deal_type": "direct_chollo",
                "identified_model": "Nintendo Switch Lite",
                "visual_confidence": "alta",
                "visual_notes": "Buen estado general.",
            },
            price=60.0, landed_cost=65.0, net_profit=30.0,
            should_push=False, quiet_reason="Vendedor con 0 valoraciones",
        )
        lines, _embed = self._assert_common_discord_snapshot(payload, "Nintendo Switch Lite", "60 €", "+30 €")
        self.assertEqual(payload.get("flags"), ce.DISCORD_SILENT_FLAG)
        self.assertIn("🔕", lines[-1])
        self.assertIn("Vendedor con 0 valoraciones", lines[-1])
        self.assertNotIn("<@424242>", payload["content"])

    def test_flash_discord_alert_no_deeplink_and_https(self):
        captured = []

        def _fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return _FakeResp(200, {"id": "flash-1"})

        with mock.patch.object(ce, "get_notification_policy", return_value={"flash_mode": "on"}), \
             mock.patch.object(ce.HTTP_SESSION, "post", side_effect=_fake_post):
            ce.send_flash_discord_alert(
                "WALLAPOP", "555", "Nintendo Switch OLED Blanca", 90.0,
                landed_cost=95.0, item_url="https://es.wallapop.com/item/switch-555?utm_source=a",
                seller_city="Madrid",
            )
        self.assertEqual(len(captured), 1)
        payload = captured[0]
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("wallapop://", raw)
        self.assertNotIn("vinted://", raw)
        lines = payload["content"].splitlines()
        self.assertLessEqual(len(lines[0]), 110)
        self.assertIn("Nintendo Switch OLED Blanca", lines[0])
        self.assertIn("90 €", lines[0])
        self.assertTrue(lines[1].startswith("https://"))
        for u in _collect_urls(payload):
            self.assertTrue(u.startswith("https://"))

    def test_ntfy_uses_same_headline_and_https_click(self):
        captured = []

        def _fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return _FakeResp(200, {"id": "ntfy-1"})

        item = {
            "title": "Switch OLED nueva",
            "price": 85.0,
            "platform": "WALLAPOP",
            "url": "https://es.wallapop.com/item/switch-101?utm_medium=share",
            "seller_city": "Sevilla",
            "seller": {"seller_trust_badge": "⭐ 4.9 (18)"},
        }
        analysis = {
            "deal_score": 9,
            "deal_type": "direct_chollo",
            "identified_model": "Nintendo Switch OLED",
            "p2p_net_profit": 45.0,
            "cex_cash": 110.0,
        }
        with mock.patch.object(ce.HTTP_SESSION, "post", side_effect=_fake_post):
            ok = ce.send_one_tap_ntfy_alert(item, analysis_result=analysis, is_flash=False)
        self.assertTrue(ok)
        self.assertEqual(len(captured), 1)
        payload = captured[0]
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("wallapop://", raw)
        self.assertNotIn("vinted://", raw)
        self.assertLessEqual(len(payload["title"]), 110)
        self.assertIn("Nintendo Switch OLED", payload["title"])
        self.assertIn("85 €", payload["title"])
        self.assertIn("+45 €", payload["title"])
        self.assertTrue(payload["click"].startswith("https://"))
        self.assertTrue(payload["actions"][0]["url"].startswith("https://"))

    def test_ntfy_scanner_payload_builder_offer_opportunity_headline(self):
        captured = []

        def _fake_post(url, **kwargs):
            captured.append(kwargs.get("json", {}))
            return _FakeResp(200, {"id": "ntfy-offer-1"})

        item = {
            "title": "Consola PS5 anuncio bruto",
            "price": 140.0,
            "platform": "WALLAPOP",
            "url": "https://es.wallapop.com/item/ps5-slim-999",
            "seller_city": "Sevilla",
            "seller": {"seller_trust_badge": "⭐ 4.9 (18)"},
        }
        ai_data = {
            "deal_score": 8,
            "deal_type": "offer_opportunity",
            "identified_model": "PS5 Slim Digital",
            "recommended_offer": 110,
            "deal_tier": 2,
            "_alert_push": True,
        }
        cex_data = {"cash": 150.0}
        target = {"mode": "resale"}
        analysis_payload = ce.build_ntfy_analysis_payload(
            ai_data, cex_data, 50.0, target=target, score=8
        )
        with mock.patch.object(ce.HTTP_SESSION, "post", side_effect=_fake_post):
            ok = ce.send_one_tap_ntfy_alert(item, analysis_result=analysis_payload, is_flash=False)
        self.assertTrue(ok)
        self.assertEqual(len(captured), 1)
        headline = captured[0]["title"]
        self.assertTrue(headline.startswith("🤝"), f"Titular inesperado: {headline!r}")
        self.assertIn("PS5 Slim Digital", headline)
        self.assertIn("ofertar 110 €", headline)
        self.assertIn("pide 140 €", headline)
        self.assertIn("+50 €", headline)


if __name__ == "__main__":
    unittest.main()
