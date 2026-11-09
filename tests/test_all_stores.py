import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import (
    fetch_cashconverters,
    fetch_milanuncios,
    fetch_ebay_es,
    fetch_todocoleccion
)
from antiban import BrowserProfileRotator

rotator = BrowserProfileRotator()

test_targets = [
    ("nintendo switch", 20, 250),
    ("ps4", 30, 200),
]

all_ok = True
print("=== VERIFICACIÓN MULTI-TIENDA Y MULTI-RADAR ===")

for q, min_p, max_p in test_targets:
    print(f"\n--- Radar: '{q}' ({min_p}€ - {max_p}€) ---")
    stores = [
        ("CASHCONVERTERS", fetch_cashconverters),
        ("MILANUNCIOS", fetch_milanuncios),
        ("EBAY", fetch_ebay_es),
        ("TODOCOLECCION", fetch_todocoleccion),
    ]
    for name, fetcher in stores:
        t0 = time.time()
        try:
            items = fetcher(rotator, q, min_p, max_p)
            elapsed = round(time.time() - t0, 2)
            count = len(items) if items else 0
            sample = items[0]["title"][:35] if count > 0 else "N/A"
            price = items[0]["price"] if count > 0 else "N/A"
            print(f"[{name}] ✅ OK: {count} anuncios ({elapsed}s) | '{sample}' - {price}€")
            if count == 0:
                print(f"[{name}] ⚠️ Alerta: 0 anuncios")
                all_ok = False
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            print(f"[{name}] ❌ ERROR ({elapsed}s): {type(e).__name__} -> {e}")
            all_ok = False
        time.sleep(1.0)

print("\n" + "=" * 50)
if all_ok:
    print("🎯 TODAS LAS TIENDAS FUNCIONAN PERFECTAMENTE AL 100%")
else:
    print("❌ HUBO ERRORES EN ALGUNA TIENDA")
