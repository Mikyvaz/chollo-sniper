import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core_engine import fetch_milanuncios
from antiban import BrowserProfileRotator

rotator = BrowserProfileRotator()
try:
    items = fetch_milanuncios(rotator, "nintendo switch", 20, 250)
    print(f"fetch_milanuncios: {len(items)} items")
    if items:
        print(f"Sample: {items[0]['title']} -> {items[0]['price']}€")
except Exception as e:
    print(f"fetch_milanuncios ERROR: {type(e).__name__} -> {e}")

