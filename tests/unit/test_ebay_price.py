"""T1.11: eBay, precios >= 1.000 EUR y precio tachado."""
import unittest

import _harness  # noqa: F401
import core_engine


def card(price_html, ship=""):
    return ('<ul><li data-listingid="123456789012" class="s-item">'
            '<img alt="Nintendo Switch OLED consola" src="x">'
            f'{price_html}{ship}</li></ul>')


class FakeResp:
    status_code = 200

    def __init__(self, html):
        self.text = html + "<!--" + "x" * 6000 + "-->"


class FakeSession:
    def __init__(self, html):
        self.html = html

    def get(self, url, timeout=0):
        return FakeResp(self.html)


class TestEbayPrice(unittest.TestCase):
    def run_fetch(self, html, lo, hi):
        orig = core_engine._ebay_mgr.get_session
        core_engine._ebay_mgr.get_session = lambda: FakeSession(html)
        try:
            return core_engine.fetch_ebay_es(None, "switch", lo, hi)
        finally:
            core_engine._ebay_mgr.get_session = orig

    def test_miles_rango_barato(self):
        h = card('<span class="s-item__price">1.234,56 EUR</span>')
        self.assertEqual(self.run_fetch(h, 50, 400), [])

    def test_miles_rango_caro(self):
        h = card('<span class="s-item__price">1.234,56 EUR</span>')
        r = self.run_fetch(h, 1000, 2000)
        self.assertEqual(len(r), 1)
        self.assertAlmostEqual(r[0]["price"], 1234.56)

    def test_coma(self):
        h = card('<span class="s-item__price">123,45 EUR</span>')
        self.assertAlmostEqual(self.run_fetch(h, 50, 400)[0]["price"], 123.45)

    def test_punto(self):
        h = card('<span class="s-item__price">99.99 €</span>')
        self.assertAlmostEqual(self.run_fetch(h, 50, 400)[0]["price"], 99.99)

    def test_tachado(self):
        h = card('<span class="STRIKETHROUGH">150,00 EUR</span>'
                 '<span class="s-item__price">120,00 EUR</span>')
        self.assertAlmostEqual(self.run_fetch(h, 50, 400)[0]["price"], 120.0)

    def test_tachado_anidado_y_previo_primero(self):
        h = card('<span class="s-item__price"><span class="s-item__was-price">150,00 EUR</span> 120,00 EUR</span>')
        self.assertAlmostEqual(self.run_fetch(h, 50, 400)[0]["price"], 120.0)

    def test_envio_miles(self):
        h = card('<span class="s-item__price">99,99 EUR</span>',
                 '<span class="s-item__shipping">+ 1.020,50 EUR</span>')
        self.assertAlmostEqual(self.run_fetch(h, 50, 400)[0]["fees"], 1020.50)


if __name__ == "__main__":
    unittest.main()
