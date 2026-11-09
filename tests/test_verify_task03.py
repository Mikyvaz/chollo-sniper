import sqlite3
import core_engine as ce

conn = ce.get_db('data/sniper.db')
t = {'id': 'switch_std_lite', 'max_price': 123.5, 'direct_buy_max_price': 95.0, 'min_profit': 15.0}

ok_direct, r1 = ce.pre_gate_cex_offer_margin(conn, {'title': 'Nintendo Switch Lite Coral', 'price': 85.0, 'platform': 'WALLAPOP'}, t, {})
ok_dict_price, r_dict = ce.pre_gate_cex_offer_margin(conn, {'title': 'Nintendo Switch Lite Coral', 'price': {'amount': 85.0, 'currency': 'EUR'}, 'platform': 'WALLAPOP'}, t, {})
ok_str_price, r_str = ce.pre_gate_cex_offer_margin(conn, {'title': 'Nintendo Switch Lite Coral', 'price': '85.0', 'platform': 'WALLAPOP'}, t, {})
assert ok_dict_price is True and r_dict == 'direct_zone'
assert ok_str_price is True and r_str == 'direct_zone'
ok_bundle, r2 = ce.pre_gate_cex_offer_margin(conn, {'title': 'Nintendo Switch Lite + 4 juegos Pokemon', 'price': 120.0, 'platform': 'WALLAPOP'}, t, {})
ok_over, r3 = ce.pre_gate_cex_offer_margin(conn, {'title': 'Nintendo Switch Lite Gris consola suelta', 'price': 123.0, 'platform': 'WALLAPOP'}, t, {'max_haggle_discount_pct': 0.15})

print('direct=', ok_direct, r1, '| bundle=', ok_bundle, r2, '| overpriced=', ok_over, r3)
assert ok_direct is True and ok_bundle is True

# Test adicional con BD en memoria para validar descarte cuando el espejo CeX hace match:
mem_conn = sqlite3.connect(':memory:')
mem_conn.row_factory = sqlite3.Row
ce.init_db(mem_conn)
c = mem_conn.cursor()
c.execute("""
    INSERT INTO cex_catalog_mirror (box_id, box_name, category_name, cash_price, voucher_price, sell_price)
    VALUES ('SW_LITE_GRIS', 'Switch Lite Grey Console', 'consolas', 55.0, 70.0, 105.0)
""")
mem_conn.commit()

ok_over_match, r_match = ce.pre_gate_cex_offer_margin(
    mem_conn,
    {'title': 'Switch Lite Grey Console', 'price': 123.0, 'platform': 'WALLAPOP'},
    t,
    {'max_haggle_discount_pct': 0.15}
)
print('overpriced_with_match=', ok_over_match, r_match)
assert ok_over_match is False, f"Debe descartar cuando el margen es imposible: {r_match}"
assert "pre_gate_margin_impossible" in r_match

print("TASK_03_SPECIFIC_OK")
