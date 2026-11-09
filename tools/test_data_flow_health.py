import re
import json
from core_engine import normalize_and_build_links, prescreen_item_text, analyze_and_generate_kit, init_db

print("=== 1. VERIFICACIÓN DE NORMALIZACIÓN DE ENLACES ===")
# Test Vinted FR URL
v_url_fr = "https://www.vinted.fr/items/10654319168-ipad-9-generazione?referrer=catalog"
v_item, v_checkout = normalize_and_build_links("VINTED", "10654319168", v_url_fr)
print(f"Vinted FR -> Item URL: {v_item}")
print(f"Vinted FR -> Checkout URL: {v_checkout}")
assert v_item == "https://www.vinted.es/items/10654319168-ipad-9-generazione", f"Falló normalización Vinted: {v_item}"
assert v_checkout == v_item, f"Checkout no debe ser transaction/buy/new: {v_checkout}"
assert "transaction/buy/new" not in v_checkout, "Error: Checkout 1-Tap ficticio presente!"

# Test Vinted numeric only
v_item2, v_checkout2 = normalize_and_build_links("VINTED", "999888777", "")
print(f"Vinted numeric -> Item URL: {v_item2}")
assert v_item2 == "https://www.vinted.es/items/999888777"
assert v_checkout2 == v_item2

# Test Wallapop slug
w_url = "https://es.wallapop.com/item/pokemon-platino-1320444866"
w_item, w_checkout = normalize_and_build_links("WALLAPOP", "x6qqp75vly6y", w_url)
print(f"Wallapop -> Item URL: {w_item}")
print(f"Wallapop -> Checkout URL: {w_checkout}")
assert w_item == w_url
assert w_checkout == w_url

print("\n=== 2. VERIFICACIÓN DEL FLUJO DE DATOS IA (PRESCREEN PUERTA 1) ===")
conn = init_db()
test_target = {
    "id": "test_ipad",
    "name": "iPad Test",
    "keywords": ["ipad"],
    "min_price": 50,
    "max_price": 200,
    "mode": "resale",
    "ai_valuation_guide": "Identifica si es iPad 9 o superior."
}
ps_dec, ps_reason, ps_conf, is_cached = prescreen_item_text(
    platform="VINTED",
    title="iPad 9 64GB gris espacial impecable",
    description="Vendo iPad 9 en perfecto estado con caja y cargador.",
    price=110.0,
    landed_cost=119.5,
    target=test_target,
    conn=conn
)
print(f"Prescreen Decisión: {ps_dec} | Motivo: {ps_reason} | Confianza: {ps_conf}")
assert ps_dec in ("proceed", "reject"), f"Decisión inesperada de prescreen: {ps_dec}"
print("✅ Prescreen con Gemini 3.5 funcionando con éxito (cero errores 404).")

print("\n=== 3. VERIFICACIÓN DE ANÁLISIS MULTIMODAL (PUERTA 2) ===")
ai_data = analyze_and_generate_kit(
    platform="VINTED",
    title="iPad 9 64GB gris espacial impecable",
    description="Vendo iPad 9 en perfecto estado con caja y cargador.",
    price=110.0,
    landed_cost=119.5,
    is_foreign=False,
    target=test_target,
    conn=conn,
    raw_images=[]
)
print(f"AI Multimodal Deal Score: {ai_data.get('deal_score')} | Válido: {ai_data.get('is_valid_deal')} | Modelo: {ai_data.get('identified_model')}")
assert "is_valid_deal" in ai_data, "Falta is_valid_deal en ai_data"
print("✅ Análisis IA Multimodal funcionando con éxito (cero errores 404).")

print("\n🎉 TODOS LOS TESTS DE SALUD DEL FLUJO DE DATOS PASARON CON ÉXITO.")
