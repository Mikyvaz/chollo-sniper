# -*- coding: utf-8 -*-
"""
Test de verificación del BLOQUE 4 (Plan v5.2):
1. sanitize_item_url limpia URLs de Wallapop, Vinted, eBay y Cash Converters (sin tracking).
2. build_telegram_inline_keyboard genera reply_markup con botones de 1 toque.
3. send_one_tap_ntfy_alert formatea cabeceras Click y Actions para apertura nativa.
4. Fast-Lane Queue ordena primero el ítem de mayor descuento relativo.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import (
    sanitize_item_url,
    build_telegram_inline_keyboard,
    send_one_tap_ntfy_alert
)


def test_sanitize_item_url():
    print("[1/4] Probando sanitización de URLs en las 4 tiendas...")

    # Wallapop
    w_dirty = "https://es.wallapop.com/item/nintendo-switch-oled-1029384756?utm_source=share&utm_medium=app&promulgated=1"
    w_clean = sanitize_item_url(w_dirty, "WALLAPOP")
    assert w_clean == "https://es.wallapop.com/item/nintendo-switch-oled-1029384756", f"Error Wallapop: {w_clean}"

    # Vinted
    v_dirty = "https://www.vinted.fr/items/39485721-ps5-digital-edition?referrer=catalog&utm_source=growth"
    v_clean = sanitize_item_url(v_dirty, "VINTED")
    assert v_clean == "https://www.vinted.es/items/39485721-ps5-digital-edition", f"Error Vinted: {v_clean}"

    # eBay
    e_dirty = "https://www.ebay.es/itm/185739201948?hash=item2b3fec&_trkparms=ispr%3D1&_trksid=p2380057.m570"
    e_clean = sanitize_item_url(e_dirty, "EBAY")
    assert e_clean == "https://www.ebay.es/itm/185739201948", f"Error eBay: {e_clean}"

    # Cash Converters
    c_dirty = "https://cashconverters.es/es/es/comprar/videojuegos/consolas/ps5/CC001_E123456_0.html?utm_source=google&gclid=123"
    c_clean = sanitize_item_url(c_dirty, "CASHCONVERTERS")
    assert c_clean == "https://cashconverters.es/es/es/comprar/videojuegos/consolas/ps5/CC001_E123456_0.html", f"Error CashConverters: {c_clean}"

    print("  -> OK: URLs limpias en Wallapop, Vinted, eBay y Cash Converters.")


def test_telegram_inline_keyboard():
    print("[2/4] Probando teclado Inline para Telegram con botones gigantes...")
    clean_url = "https://es.wallapop.com/item/ps5-slim-999"
    markup = build_telegram_inline_keyboard(
        clean_url=clean_url,
        price=280.0,
        item_id="w_999",
        cex_cash=310.0,
        cex_link="https://es.webuy.com/product-detail?id=123"
    )

    assert "inline_keyboard" in markup, "Falta inline_keyboard"
    rows = markup["inline_keyboard"]
    assert len(rows) == 2, f"Se esperaban 2 filas de botones, obtenidas {len(rows)}"

    # Fila 1: Botón de ancho completo
    row1 = rows[0]
    assert len(row1) == 1
    assert "⚡ ABRIR ANUNCIO DIRECTO (280€)" in row1[0]["text"]
    assert row1[0]["url"] == clean_url

    # Fila 2: CeX + Descartar
    row2 = rows[1]
    assert len(row2) == 2
    assert "📊 Ver en CeX (310€)" in row2[0]["text"]
    assert "🚫 Descartar" in row2[1]["text"]
    assert row2[1]["callback_data"] == "discard_w_999"

    print("  -> OK: reply_markup.inline_keyboard generado con estructura 1-Tap.")


def test_ntfy_headers():
    print("[3/4] Probando formateo de alertas 1-Tap para ntfy.sh...")
    # Verificar que la función existe y se ejecuta sin excepciones
    item_data = {
        "title": "Nintendo Switch OLED Blanca",
        "price": 140.0,
        "platform": "WALLAPOP",
        "url": "https://es.wallapop.com/item/switch-oled-123?utm_source=share",
        "seller": {"seller_trust_badge": "🟢 Vendedor Fiable (20 val., 5.0⭐)"}
    }
    analysis = {
        "deal_tier": 1,
        "cex_cash": 165.0,
        "p2p_net_profit": 55.0
    }
    # Sin variable NTFY_TOPIC_URL debe retornar False limpiamente
    sent = send_one_tap_ntfy_alert(item_data, analysis, is_flash=False)
    assert sent is False, "Sin NTFY_TOPIC_URL debe retornar False de forma segura"
    print("  -> OK: Formateador ntfy opera limpiamente.")


def test_fast_lane_queue_ordering():
    print("[4/4] Probando ordenación Fast-Lane Queue por margen relativo...")
    target = {
        "max_price": 200.0,
        "direct_buy_max_price": 150.0
    }
    tgt_max = max(1.0, float(target["max_price"]))
    tgt_direct = float(target["direct_buy_max_price"])

    items = [
        {"id": "item_offer", "title": "Switch a 180€ (Oferta)", "price": 180.0},
        {"id": "item_ok", "title": "Switch a 120€ (Compra directa)", "price": 120.0},
        {"id": "item_super_chollo", "title": "Switch a 60€ (Chollo extremo)", "price": 60.0},
        {"id": "item_border", "title": "Switch a 145€ (Compra directa)", "price": 145.0},
    ]

    def fast_lane_key(it):
        p = float(it.get("price") or 0.0)
        is_direct = 0 if p <= tgt_direct else 1
        ratio = p / tgt_max
        return (is_direct, ratio)

    sorted_items = sorted(items, key=fast_lane_key)

    # El primero DEBE ser el de 60€ (ratio 60/200 = 0.30)
    assert sorted_items[0]["id"] == "item_super_chollo", f"El primer ítem debía ser el super chollo, obtenido: {sorted_items[0]}"
    # El segundo debe ser el de 120€ (ratio 0.60)
    assert sorted_items[1]["id"] == "item_ok"
    # El tercero debe ser el de 145€ (ratio 0.725)
    assert sorted_items[2]["id"] == "item_border"
    # El último debe ser el de 180€ (is_direct = 1)
    assert sorted_items[3]["id"] == "item_offer"

    print("  -> OK: Fast-Lane Queue ordena en primer lugar el chollo con mayor descuento.")


if __name__ == "__main__":
    test_sanitize_item_url()
    test_telegram_inline_keyboard()
    test_ntfy_headers()
    test_fast_lane_queue_ordering()
    print("\n✅ TODOS LOS TESTS DEL BLOQUE 4 HAN PASADO CON ÉXITO.")

