"""Valoraciones reales del vendedor en la ficha de Vinted y compra asistida (nota 10)."""
import tests.unit._harness  # noqa: F401
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

import core_engine as ce

# Fragmentos reales de una ficha de Vinted (17/11/2026): el bloque de datos con comillas escapadas
# y el icono de estrella cuyo «width:12px» producía el falso «12 valoraciones».
ESCAPED_BLOCK = (r'\"name\":\"seller_badges_info\",\"section\":\"sidebar\",\"type\":\"seller_badges_info\"},'
                 r'{\"data\":{\"badges\":[],\"business\":false,\"feedback_count\":7,\"feedback_reputation\":1,'
                 r'\"follow_action_visible\":false,\"is_favourite\":false}')
PRO_BLOCK = (r'{\"data\":{\"badges\":[\"Pro\"],\"business\":true,\"feedback_count\":203,'
             r'\"feedback_reputation\":0.98,\"follow_action_visible\":false}')
STAR_ICON = ('<div class="web_ui__Rating__rating web_ui__Rating__small" role="group" '
             'aria-label="Este miembro tiene una valoración total de 5 sobre 5"><div class="web_ui__Rating__stars">'
             '<div class="web_ui__Rating__star web_ui__Rating__full"><div class="web_ui__Rating__star-content" '
             'aria-hidden="true"><span class="web_ui__Rating__star-first-half"><span class="web_ui__Icon__icon" '
             'style="width:12px"><svg fill="none" viewBox="0 0 12 12"></svg></span></span></div></div></div></div>')
ZERO_BLOCK = '<div data-testid="profile-ratings"><span>Aún no hay valoraciones</span></div>'
FAR_TRANSLATIONS = ("x" * 2000) + r'\"user.feedback.no_reviews\":\"Aún no hay valoraciones\"'


class Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload or {}
        self.headers = {}

    def json(self):
        return self._payload


class Sess:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.responses.pop(0) if self.responses else Resp(500)


def _client(session):
    import vinted_sniper
    client = vinted_sniper.VintedClient.__new__(vinted_sniper.VintedClient)
    client.session = session
    client.health_monitor = mock.Mock()
    client.health_monitor.is_in_cooldown.return_value = False
    return client


class TestVintedFeedbackParsing(unittest.TestCase):
    def test_escaped_block_gives_real_count_and_reputation(self):
        import vinted_sniper
        self.assertEqual(vinted_sniper.parse_vinted_seller_feedback(STAR_ICON + ESCAPED_BLOCK), (7, 1.0, False))
        self.assertEqual(vinted_sniper.parse_vinted_seller_feedback(PRO_BLOCK), (203, 0.98, True))
        plain = '{"business":false,"feedback_count":32,"feedback_reputation":0.98}'
        self.assertEqual(vinted_sniper.parse_vinted_seller_feedback(plain), (32, 0.98, False))
        self.assertEqual(vinted_sniper.parse_vinted_seller_feedback(STAR_ICON), (None, None, None))

    def test_zero_reviews_only_next_to_the_profile_block(self):
        import vinted_sniper
        self.assertEqual(vinted_sniper.parse_vinted_zero_reviews(ZERO_BLOCK), 0)
        self.assertIsNone(vinted_sniper.parse_vinted_zero_reviews(STAR_ICON))
        # La traducción «Aún no hay valoraciones» del JSON de la página no debe convertir en 0 a nadie
        self.assertIsNone(vinted_sniper.parse_vinted_zero_reviews(
            '<div data-testid="profile-ratings"><span>4.9</span></div>' + FAR_TRANSLATIONS))

    def test_fetch_item_details_no_longer_reads_the_star_icon_width(self):
        page = ('<meta name="description" content="Nintendo 2DS blanca y roja"/>'
                '<a href="/member/186193279">x</a>' + STAR_ICON + ESCAPED_BLOCK)
        desc, photos, upload, seller = _client(Sess([Resp(200, page)])).fetch_item_details("https://www.vinted.es/items/1-x")
        self.assertEqual(seller["sales"], 7)
        self.assertEqual(seller["feedback_count"], 7)
        self.assertEqual(seller["rating"], 5.0)
        self.assertIs(seller["is_business"], False)

    def test_unknown_count_stays_none_so_enrichment_asks_the_api(self):
        import fraud_signals
        page = '<a href="/member/555">x</a>' + STAR_ICON
        _d, _p, _u, seller = _client(Sess([Resp(200, page)])).fetch_item_details("https://www.vinted.es/items/2-x")
        self.assertIsNone(seller["sales"], "sin bloque de datos no se inventa el número de valoraciones")
        self.assertEqual(seller["rating"], 5.0)

        fraud_signals._SELLER_PROFILE_CACHE.pop(("VINTED", "555"), None)
        api = Sess([Resp(200, payload={"user": {"feedback_count": 3, "feedback_reputation": 0.9,
                                                "business": False, "given_item_count": 4}})])
        item = {"platform": "VINTED", "seller": dict(seller, id="555")}
        try:
            prof = fraud_signals.enrich_seller_profile(item, http_session=api, platform="VINTED")
        finally:
            fraud_signals._SELLER_PROFILE_CACHE.pop(("VINTED", "555"), None)
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(prof["seller_reviews_count"], 3)
        self.assertIs(prof["is_business"], False)

    def test_zero_reviews_profile_is_detected(self):
        page = '<a href="/member/9">x</a>' + ZERO_BLOCK
        _d, _p, _u, seller = _client(Sess([Resp(200, page)])).fetch_item_details("https://www.vinted.es/items/3-x")
        self.assertEqual(seller["sales"], 0)

    def test_catalog_seller_id_and_business_flag_take_precedence(self):
        import vinted_sniper
        page_seller = {"name": "", "id": "111", "sales": 7, "rating": 5.0}
        item = {"country_code": "FR",
                "seller": {"name": "claraaa_lb", "id": "186193279", "is_business": True}}
        merged = vinted_sniper.merge_catalog_seller(page_seller, item)
        self.assertEqual(merged["id"], "186193279")
        self.assertEqual(merged["name"], "claraaa_lb")
        self.assertIs(merged["is_business"], True)
        self.assertEqual(merged["country_code"], "FR")
        # Un id no numérico del catálogo (login de respaldo) no sustituye al de la ficha
        merged2 = vinted_sniper.merge_catalog_seller({"id": "111"}, {"seller": {"id": "login_sin_id"}})
        self.assertEqual(merged2["id"], "111")

    def test_professional_seller_is_visible_and_stored(self):
        line = ce.format_seller_line({"name": "ernepana", "seller_reviews_count": 203, "seller_rating": 4.9,
                                      "is_business": True})
        self.assertIn("vendedor profesional", line)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE deals (deal_id TEXT PRIMARY KEY, alert_context_json TEXT)")
        conn.execute("INSERT INTO deals VALUES ('VIN_1', '')")
        ce.save_alert_context(conn, "VIN_1", seller={"name": "x", "is_business": True, "seller_reviews_count": 9},
                              extra={"top_deal": True})
        ctx = json.loads(conn.execute("SELECT alert_context_json FROM deals").fetchone()[0])
        self.assertIs(ctx["seller"]["is_business"], True)
        self.assertIs(ctx["top_deal"], True)
        conn.close()


class TestAssistedBuyPush(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE deals (deal_id TEXT PRIMARY KEY, created_at TEXT, alert_context_json TEXT)")
        for i in range(4):   # tope por hora ya alcanzado
            self.conn.execute("INSERT INTO deals VALUES (?, datetime('now'), ?)", (f"D{i}", json.dumps({"push": True})))
        self.conn.commit()
        self.policy = dict(ce.NOTIFICATION_DEFAULTS)
        p = mock.patch.object(ce, "get_notification_policy", side_effect=lambda: self.policy)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.conn.close)
        self.seller = {"seller_reviews_count": 25}

    def push(self, **kw):
        args = dict(deal_type="direct_chollo", net_profit=60, seller=self.seller, score=10)
        args.update(kw)
        return ce.decide_alert_push(self.conn, args.pop("platform", "VINTED"), args.pop("deal_type"),
                                    args.pop("net_profit"), **args)

    def test_top_deal_ignores_hourly_cap(self):
        self.assertEqual(self.push(), (True, ""))
        self.assertEqual(self.push(score=9)[0], False)
        self.assertIn("avisos/hora", self.push(score=9)[1])

    def test_top_deal_has_its_own_profit_floor(self):
        self.assertTrue(self.push(net_profit=30)[0])           # antes: «margen < 40€»
        self.assertEqual(self.push(net_profit=20), (False, "margen < 25€"))
        self.assertFalse(self.push(net_profit=30, score=9)[0])

    def test_safety_rules_still_apply(self):
        self.assertEqual(self.push(seller={"seller_reviews_count": 0}), (False, "vendedor sin valoraciones"))
        self.assertFalse(self.push(deal_type="high_risk_opportunity")[0])
        self.assertFalse(self.push(deal_type="offer_opportunity", net_profit=60)[0])
        self.assertFalse(ce.is_top_deal("direct_chollo", 10, mode="personal"))

    def test_switch_off_restores_previous_behaviour(self):
        self.policy["push_top_deals_always"] = False
        self.assertFalse(self.push()[0])                       # vuelve a mandar el tope por hora
        self.assertFalse(self.push(net_profit=30)[0])

    def test_ntfy_top_deal_says_buy_now_with_max_priority(self):
        captured = []

        def fake_post(url, **kw):
            captured.append(kw.get("json"))
            return Resp(200)

        item = {"title": "Nintendo 3DS XL", "price": 50.0, "platform": "VINTED",
                "url": "https://www.vinted.es/items/10794120741-3ds"}
        with mock.patch.object(ce, "NTFY_TOPIC_URL", "https://ntfy.example/t"), \
             mock.patch.object(ce.HTTP_SESSION, "post", side_effect=fake_post):
            top = ce.build_ntfy_analysis_payload({"deal_type": "direct_chollo", "deal_tier": 1,
                                                  "identified_model": "Nintendo 3DS XL"}, {"cash": 110}, 75.0, score=10)
            normal = ce.build_ntfy_analysis_payload({"deal_type": "direct_chollo", "deal_tier": 3,
                                                     "identified_model": "Nintendo 3DS XL"}, None, 45.0, score=8)
            self.assertTrue(ce.send_one_tap_ntfy_alert(item, top))
            self.assertTrue(ce.send_one_tap_ntfy_alert(item, normal))
        self.assertEqual(captured[0]["priority"], 5)
        self.assertEqual(captured[0]["actions"][0]["label"], "⚡ Comprar ya (50 €)")
        self.assertTrue(captured[0]["actions"][0]["url"].startswith("https://www.vinted.es/items/"))
        self.assertIn("rotating_light", captured[0]["tags"])
        self.assertEqual(captured[1]["priority"], 4)
        self.assertEqual(captured[1]["actions"][0]["label"], "Abrir anuncio")

    def test_repo_config_enables_assisted_buy(self):
        cfg = ce.load_raw_config()
        notif = cfg.get("notifications", {})
        self.assertTrue(notif.get("push_top_deals_always"))
        self.assertEqual(notif.get("push_top_deal_min_profit_eur"), 25)


class TestTopDealsReport(unittest.TestCase):
    def test_report_runs_and_treats_old_vinted_12_as_unknown(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys.path.insert(0, os.path.join(root, "tools"))
        try:
            import top_deals_report as rep
        finally:
            sys.path.pop(0)
        self.assertIsNone(rep.seller_reviews("VINTED", {"seller": {"reviews": 12}, "pipeline_version": "2026.11.11-f1"}))
        self.assertEqual(rep.seller_reviews("VINTED", {"seller": {"reviews": 12}, "pipeline_version": "2026.11.17-v1"}), 12)
        self.assertEqual(rep.seller_reviews("WALLAPOP", {"seller": {"reviews": 12}}), 12)
        out = subprocess.run([sys.executable, os.path.join(root, "tools", "top_deals_report.py"),
                              "--db", os.environ["DB_PATH"], "--days", "60"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Clase candidata a compra automática", out.stdout)


if __name__ == "__main__":
    unittest.main()
