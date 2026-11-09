import sys, os
sys.path.insert(0, '/app')
sys.path.insert(0, '.')

import core_engine

# Edit in memory or test function
def test_matches(bname, cat, clean_q=None, target=None, context_text="", includes_lens=True, **kwargs):
    if bname is None and "box_name" in kwargs:
        bname = kwargs["box_name"]
    if "query" in kwargs:
        clean_q = kwargs["query"]
    if "radar_id" in kwargs:
        target = kwargs["radar_id"]

    if isinstance(target, str) and not context_text:
        context_text = target
        radar_id = clean_q if isinstance(clean_q, str) else ""
        clean_q = cat
        cat = ""
        target = {"id": radar_id}
    elif clean_q is None and isinstance(cat, str):
        clean_q = cat
        cat = ""

    bname_l = bname.lower()
    bname_norm = core_engine._normalize_cex_text(bname)
    cat_l = (cat or "").lower()
    q_norm = core_engine._normalize_cex_text(clean_q)
    ctx_norm = core_engine._normalize_cex_text(f"{clean_q} {context_text}")
    radar_id = (target or {}).get("id", "") if isinstance(target, dict) else (target if isinstance(target, str) else "")
    rid = radar_id
    is_console_radar = (rid in core_engine.CONSOLE_RADAR_IDS) or any(c_id in rid for c_id in core_engine.CONSOLE_RADAR_IDS)

    if radar_id in core_engine.RADAR_ALLOWED_CEX_CATEGORIES:
        allowed_cats = core_engine.RADAR_ALLOWED_CEX_CATEGORIES[radar_id]
        if not any(ac in cat_l for ac in allowed_cats):
            return False

    if is_console_radar:
        if "juegos" in cat_l or "accesorios" in cat_l:
            return False
        bname_no_sin = core_engine.re.sub(r"sin\s+(cargador|caja|lapiz|manual|dock|lector)", "", bname_l)
        bname_no_sin = core_engine.re.sub(r"\+\s*joy-?con", "", bname_no_sin)
        if any(bad in bname_no_sin for bad in core_engine.CEX_CONSOLE_ACCESSORY_REJECT):
            return False
        # Si la ficha de CeX contiene nombre de juego pero NO es una consola/edición, es un juego suelto -> descartar ficha CeX
        is_console_box = any(
            cw in bname_norm for cw in (
                "consola", "console", "nintendo switch", "switch oled", "switch lite",
                "3ds", "2ds", "ps vita", "steam deck", "rog ally", "legion go", "ps5", "playstation 5"
            )
        )
        if not is_console_box:
            return False
        for gt in core_engine.VIDEO_GAME_TITLES_AND_TOKENS:
            gt_s = gt.strip()
            if len(gt_s) >= 4 and gt_s in bname_norm and gt_s not in q_norm:
                if any(se in q_norm and se in bname_norm for se in core_engine.SPECIAL_EDITION_CONSOLE_TOKENS):
                    continue
                return False
        # No cruzar consola estándar con Edición Especial (evaluando solo q_norm de la IA)
        for se_tok in core_engine.SPECIAL_EDITION_CONSOLE_TOKENS:
            if (se_tok in bname_norm) != (se_tok in q_norm):
                return False

    if "switch" in q_norm or "switch" in radar_id or radar_id in ("switch_oled", "switch_std_lite"):
        if ("oled" in bname_norm) != ("oled" in ctx_norm):
            return False
        if ("lite" in bname_norm) != ("lite" in ctx_norm):
            return False

    return True

print("=== PROBANDO LOS 4 CASOS DE TASK 1.2 ===")
c1 = test_matches(
    box_name='Nintendo Switch Consola OLED 64GB Blanca Sin Caja',
    query='Nintendo Switch OLED 64GB',
    radar_id='switch_oled_v1_lite',
    context_text='Vendo Switch OLED impecable, regalo Zelda Breath of the Wild y funda'
)
print("Caso 1:", c1)
assert c1 is True

c2 = test_matches(
    bname="The Legend of Zelda: Breath of the Wild",
    cat="Juegos Switch",
    clean_q="Nintendo Switch OLED 64GB",
    target={"id": "switch_oled_v1_lite"},
    context_text="Nintendo Switch OLED con juego Zelda"
)
print("Caso 2:", c2)
assert c2 is False

c3 = test_matches(
    bname="Nintendo Switch Consola OLED 64GB Edicion Zelda Tears of the Kingdom",
    cat="Consolas",
    clean_q="Nintendo Switch OLED 64GB",
    target={"id": "switch_oled_v1_lite"},
    context_text="Vendo Switch OLED impecable, regalo Zelda Breath of the Wild"
)
print("Caso 3:", c3)
assert c3 is False

c4 = test_matches(
    bname="Nintendo Switch Consola OLED 64GB Edicion Zelda Tears of the Kingdom",
    cat="Consolas",
    clean_q="Nintendo Switch OLED Edicion Zelda",
    target={"id": "switch_oled_v1_lite"},
    context_text="Vendo Switch OLED Edicion Zelda Tears of the Kingdom completa en caja"
)
print("Caso 4:", c4)
assert c4 is True

print("LOS 4 CASOS PASARON AL 100%!")

