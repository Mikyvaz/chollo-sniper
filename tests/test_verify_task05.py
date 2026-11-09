import fraud_signals as fs

conn = fs.sqlite3.connect(':memory:')
fs.init_fraud_tables(conn)
res = fs.evaluate_fraud_risk(
    conn,
    {
        'id': 'TEST_SCAM_1',
        'title': 'PlayStation 5 Edicion Disco nueva precintada no me va el chat',
        'description': 'Regalo de comunion no me va el chat',
        'price': 150.0,
        'platform': 'WALLAPOP',
        'seller_id': 'u_new_1',
        'seller_reviews_count': 0,
        'seller_joined_days': 3
    },
    {'id': 'ps5_consoles_gta6', 'max_price': 290},
    cex_cash=330.0
)
print('risk=', res['risk'], 'block=', res['should_block'], 'reasons=', res['reasons'])
assert res['risk'] >= 4 and res['should_block'] is True
print("TASK_05_SPECIFIC_OK")

