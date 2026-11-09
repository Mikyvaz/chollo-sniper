#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de verificación para la Fase 7.2 (P7-2):
- Matching CeX en Videojuegos y Formatos (Cartucho vs Caja vs Periféricos/Big Box)
- Lógica de selección de variantes en Algolia CeX (Pokémon HG/SS, Emerald)
- Freno de caja/estuche en el motor matemático (case_penalty = 12€)
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import (
    _box_matches_query_and_radar,
    evaluate_deal_math_and_score,
    _normalize_cex_text,
)


def run_tests():
    print("[1/5] Probando descarte de ediciones con Pokéwalker / Caja Exterior en anuncios normales...")
    radar_pokemon = {"id": "pokemon_ds_gba", "name": "Pokémon DS/GBA", "mode": "resale"}

    # Anuncio normal sin Pokéwalker
    box_with_walker = "Pokémon Edición Plata SoulSilver + Pokéwalker, con Caja Exterior"
    box_normal = "Pokémon Edición Plata SoulSilver (sin Pokéwalker)"

    # Caso A: El anuncio NO menciona Pokéwalker
    ctx_normal = "pokemon soulsilver nintendo ds original juego funcionando"
    matches_walker = _box_matches_query_and_radar(
        bname=box_with_walker, cat="Juegos DS", clean_q="pokemon soulsilver", target=radar_pokemon, context_text=ctx_normal
    )
    assert not matches_walker, f"ERROR: '{box_with_walker}' debió ser descartado para contexto normal '{ctx_normal}'!"
    print("  -> Caja con Pokéwalker correctamente descartada para anuncio estándar.")

    matches_normal = _box_matches_query_and_radar(
        bname=box_normal, cat="Juegos DS", clean_q="pokemon soulsilver", target=radar_pokemon, context_text=ctx_normal
    )
    assert matches_normal, f"ERROR: '{box_normal}' debió ser aceptado para '{ctx_normal}'!"
    print("  -> Caja estándar (sin Pokéwalker) aceptada correctamente.")

    # Caso B: El anuncio SÍ incluye Pokéwalker
    ctx_with_walker = "pokemon soulsilver con pokewalker y caja exterior completo"
    matches_walker_ok = _box_matches_query_and_radar(
        bname=box_with_walker, cat="Juegos DS", clean_q="pokemon soulsilver", target=radar_pokemon, context_text=ctx_with_walker
    )
    assert matches_walker_ok, f"ERROR: '{box_with_walker}' debió ser aceptado cuando el anuncio incluye Pokéwalker!"
    print("  -> Caja con Pokéwalker correctamente aceptada cuando el contexto lo especifica.")

    print("\n[2/5] Probando descarte de cajas completas en juegos GBA cuando es solo cartucho...")
    box_emerald_loose = "Pokemon Emerald, Sin Caja"
    box_emerald_caja = "Pokemon Emerald + Manual, Caja"
    ctx_loose_gba = "pokemon emerald gba solo cartucho original probado"

    matches_loose = _box_matches_query_and_radar(
        bname=box_emerald_loose, cat="Juegos GBA", clean_q="pokemon emerald", target=radar_pokemon, context_text=ctx_loose_gba
    )
    assert matches_loose, f"ERROR: '{box_emerald_loose}' debió ser aceptado para cartucho suelto!"
    print("  -> 'Pokemon Emerald, Sin Caja' aceptado para cartucho suelto.")

    matches_caja = _box_matches_query_and_radar(
        bname=box_emerald_caja, cat="Juegos GBA", clean_q="pokemon emerald", target=radar_pokemon, context_text=ctx_loose_gba
    )
    assert not matches_caja, f"ERROR: '{box_emerald_caja}' debió ser descartado cuando el anuncio es 'solo cartucho'!"
    print("  -> 'Pokemon Emerald + Manual, Caja' descartado para cartucho suelto.")

    print("\n[3/5] Probando case_penalty (-12€) en motor matemático para cartucho suelto DS...")
    target_poke = {
        "id": "pokemon_ds_gba",
        "name": "Pokémon DS/GBA",
        "mode": "resale",
        "min_profit_eur": 25.0,
        "direct_buy_max_price": 90.0,
    }
    # CeX solo tiene ficha con caja (sell: 107€, cash: 58€)
    cex_data_ds = {
        "box_id": "045496469276",
        "box_name": "Pokémon Edición Plata SoulSilver (sin Pokéwalker)",
        "cash": 58.0,
        "voucher": 75.0,
        "sell": 107.0,
        "category": "Juegos DS",
    }

    # Anuncio de cartucho suelto (includes_original_box=False) por 60€ (landed: 65€)
    ai_data_loose = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "missing_original_charger": False,
        "includes_original_box": False,
        "visual_confidence": "alta",
        "identified_model": "Pokémon SoulSilver (Solo Cartucho)",
        "estimated_resale_price": 0,
    }

    target_poke_no_offers = dict(target_poke, offer_sniping=False)

    approved_loose, score_loose, opt_price_loose, net_profit_loose, chg_loose, msg_loose = evaluate_deal_math_and_score(
        price=60.0,
        landed_cost=65.0,
        target=target_poke_no_offers,
        ai_data=ai_data_loose,
        cex_data=cex_data_ds,
        min_profit=25.0,
        min_score=8,
    )

    assert ai_data_loose.get("case_penalty") == 12.0, (
        f"ERROR: Se esperaba case_penalty=12.0 para DS suelto sin caja CeX, obtenido: {ai_data_loose.get('case_penalty')}"
    )
    # PVP CeX efectivo: 107 - 12 = 95€; cap reventa: round(95 * 0.84) = 80€
    # optimal_price = max(58 - 12, 80) = 80€
    # net_profit = 80 - 65 - 2 = 13€ (que es menor que el min_profit de 25€)
    assert not approved_loose, f"ERROR: No debió aprobarse como compra directa con solo 13€ de margen neto! Msg: {msg_loose}"
    assert ai_data_loose.get("deal_type") != "direct_chollo", "ERROR: deal_type no puede ser direct_chollo!"
    print(f"  -> Cartucho suelto DS correctamente penalizado (-12€). Margen neto ajustado: {net_profit_loose}€ (Descartado compra directa: {msg_loose})")

    print("\n[4/5] Probando juego DS completo con caja (case_penalty = 0€)...")
    ai_data_boxed = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "missing_original_charger": False,
        "includes_original_box": True,
        "visual_confidence": "alta",
        "identified_model": "Pokémon SoulSilver Completo",
        "estimated_resale_price": 0,
    }

    approved_boxed, score_boxed, opt_price_boxed, net_profit_boxed, chg_boxed, msg_boxed = evaluate_deal_math_and_score(
        price=50.0,
        landed_cost=55.0,
        target=target_poke,
        ai_data=ai_data_boxed,
        cex_data=cex_data_ds,
        min_profit=25.0,
        min_score=8,
    )

    assert ai_data_boxed.get("case_penalty") == 0.0, (
        f"ERROR: Se esperaba case_penalty=0.0 para juego completo con caja, obtenido: {ai_data_boxed.get('case_penalty')}"
    )
    # PVP CeX: 107€ -> cap reventa: round(107 * 0.84) = 90€
    # net_profit = 90 - 55 - 2 = 33€ (>= 25€) -> Aprobado!
    assert approved_boxed, f"ERROR: Juego completo con 33€ de beneficio debió ser aprobado! Msg: {msg_boxed}"
    print(f"  -> Juego completo con caja aprobado con {net_profit_boxed}€ de beneficio neto (Score: {score_boxed}/10).")

    print("\n[5/5] Probando que juego GBA con SKU 'Sin Caja' CeX no sufra doble penalización...")
    cex_data_gba_loose = {
        "box_id": "045496734794",
        "box_name": "Pokemon Emerald, Sin Caja",
        "cash": 33.0,
        "voucher": 45.0,
        "sell": 65.0,
        "category": "Juegos GBA",
    }
    ai_data_gba_loose = {
        "is_valid_deal": True,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "missing_original_charger": False,
        "includes_original_box": False,
        "visual_confidence": "alta",
        "identified_model": "Pokémon Esmeralda Cartucho Suelto",
        "estimated_resale_price": 0,
    }

    evaluate_deal_math_and_score(
        price=20.0,
        landed_cost=24.0,
        target=target_poke,
        ai_data=ai_data_gba_loose,
        cex_data=cex_data_gba_loose,
        min_profit=20.0,
        min_score=8,
    )

    assert ai_data_gba_loose.get("case_penalty") == 0.0, (
        f"ERROR: No debe aplicarse case_penalty si la SKU CeX ya es 'Sin Caja'!, obtenido: {ai_data_gba_loose.get('case_penalty')}"
    )
    print("  -> SKU CeX 'Sin Caja' no sufre penalización redundante (case_penalty = 0€).")

    print("\n" + "=" * 60)
    print("🎉 ¡TODOS LOS TESTS DE LA FASE 7.2 PASARON SATISFACTORIAMENTE!")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
