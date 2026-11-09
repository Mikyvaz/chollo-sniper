import core_engine as ce

t = {'id': 'switch_std_lite', 'max_price': 95, 'direct_buy_max_price': 95}
m1 = ce.select_multimodal_model({'title': 'Nintendo Switch Lite azul', 'price': 75}, t, {})
m2 = ce.select_multimodal_model({'title': 'Lote PS5 Disco + 6 juegos + 2 mandos', 'price': 260}, t, {})
c_lite = ce.calculate_ai_cost_usd('gemini-3.5-flash-lite', 1000, 200, 0)
c_flash = ce.calculate_ai_cost_usd('gemini-3.8-flash', 1000, 200, 500)
print('m1=', m1, 'm2=', m2, 'c_lite=', c_lite, 'c_flash=', c_flash)
assert 'lite' in m1 and '2.5' not in m1 and '2.5' not in m2 and c_flash > c_lite
print("TASK_01_SPECIFIC_OK")

