# Sonda aislada (sin red, BD copia): valida hipótesis de falsos positivos/negativos en Puerta 0/filtros.
import os, sys, time, json
sys.path.insert(0, "/src")
import core_engine as ce
from fraud_signals import evaluate_fraud_risk

cfg = ce.load_config()
T = {t["id"]: t for t in cfg["targets"]}
gb = cfg["global_blacklist"]

cases = [
    # (radar, title, desc, price, esperado_humano)
    ("switch_std_lite", "Mario Kart 8 Deluxe Nintendo Switch", "Juego en perfecto estado", 28, "RECHAZAR (juego suelto)"),
    ("switch_std_lite", "Luigi's mansion 3 Nintendo switch", "", 35, "RECHAZAR (juego suelto)"),
    ("switch_std_lite", "Joycon Nintendo switch", "Funcionan bien", 41, "RECHAZAR (mandos sueltos)"),
    ("switch_std_lite", "Nintendo Switch V2 completa", "Incluye joy-con, dock y cargador original. Funciona perfecta.", 60, "PASAR (consola completa)"),
    ("switch_std_lite", "Switch neón 32gb", "Sin golpes ni rayadas, con caja", 60, "PASAR (consola)"),
    ("new_3ds_family", "New 3DS XL azul", "Pantalla superior e inferior sin rayadas, funciona perfecta", 95, "PASAR"),
    ("new_3ds_family", "Nintendo 3DS XL", "Se vende sin caja, no cargador. Funciona.", 70, "PASAR"),
    ("apple_high_liquidity", "iPad 9 64GB wifi", "Sin bloqueo iCloud, desvinculado. Batería 90%", 160, "PASAR"),
    ("ps5_consoles_gta6", "PS5 Slim digital", "Con 2 mandos y 3 juegos ps5. No acepto bizum, solo envío por la app", 280, "PASAR (lote bueno)"),
    ("ps5_consoles_gta6", "PS5 slim lector", "Regalo mando de ps4. Perfecta", 290, "PASAR"),
    ("switch_oled", "Nintendo Switch OLED", "Joy-con sin problemas con drift, como nueva", 95, "PASAR"),
    ("steam_deck_handhelds", "Steam Deck 512GB", "Funciona perfecto, luz azul de carga ok", 240, "PASAR"),
]

print("=" * 100)
print("A) passes_filters + evaluate_fraud_risk con config real (radar, título, precio -> resultado)")
for rid, title, desc, price, expected in cases:
    t = T[rid]
    ok = ce.passes_filters(title, desc, price, t, gb)
    fr = evaluate_fraud_risk(title, desc, price, radar_id=rid, seller={"id": "x1", "seller_reviews_count": 12, "seller_rating": 4.8})
    hits = [h["code"] + ":" + h["label"][-40:] for h in fr["hits"]]
    # localizar qué token del blacklist bloquea
    text = f"{title} {desc}".lower()
    bl_hit = [w for w in gb + t.get("blacklist", []) if ce._token_in_text(w, text)]
    print(f"- [{rid}] '{title}' {price}€ -> passes={ok} | esperado={expected}")
    if bl_hit:
        print(f"     blacklist: {bl_hit}")
    if hits:
        print(f"     fraude(risk={fr['risk']}): {hits}")

print("=" * 100)
print("B) Similaridad del embedding local entre anuncios DISTINTOS del mismo modelo")
pairs = [
    ("PS5 Slim Digital\nComo nueva con caja", "PS5 Slim Digital\nComo nueva con caja y factura, 2 mandos"),
    ("Nintendo Switch OLED blanca\nPerfecto estado", "Nintendo Switch OLED neon\nPerfecto estado con funda"),
    ("Nintendo Switch\n", "Nintendo switch\nUsada, funciona"),
    ("Steam Deck 512GB\nCon caja", "Steam Deck 256GB\nCon caja"),
    ("PS5 Slim digital", "PS5 Slim lector"),
]
for a, b in pairs:
    va, vb = ce.generate_text_embedding(a), ce.generate_text_embedding(b)
    print(f"  sim={ce.cosine_similarity(va, vb):.3f}  |  {a!r}  vs  {b!r}")

print("=" * 100)
print("C) Micro-benchmarks (CPU, sin red)")
t = T["switch_std_lite"]
N = 2000
t0 = time.perf_counter()
for _ in range(N):
    ce.passes_filters("Nintendo Switch V2 completa", "Incluye dock y cargador original. Funciona perfecta. " * 3, 60, t, gb)
pf = (time.perf_counter() - t0) / N * 1000
t0 = time.perf_counter()
txt = "Nintendo Switch V2 completa\n" + "Incluye dock y cargador original, funciona perfecta y sin rayadas. " * 6
for _ in range(200):
    ce.generate_text_embedding(txt)
emb = (time.perf_counter() - t0) / 200 * 1000
conn = ce.get_db()
vec = ce.generate_text_embedding(txt)
t0 = time.perf_counter()
for _ in range(50):
    ce.find_semantic_duplicate(conn, txt, radar_id="switch_std_lite", threshold=0.92, hours=720, vector=vec, price=60)
fsd = (time.perf_counter() - t0) / 50 * 1000
print(f"  passes_filters: {pf:.3f} ms/anuncio | generate_text_embedding: {emb:.2f} ms | find_semantic_duplicate: {fsd:.2f} ms")
