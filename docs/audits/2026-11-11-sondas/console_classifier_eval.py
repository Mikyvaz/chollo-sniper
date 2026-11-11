# Sonda T1.2: construye el dataset de títulos y mide el prototipo de clasificador consola / juego / accesorio.
#  - Negativos: títulos que la IA rechazó (Puerta 1 o visión) por ser juego suelto / mando / accesorio (logs de data/logs).
#  - Positivos: títulos de alertas (deals) de radares de consola (BD copiada en DB_PATH).
import glob, json, os, re, sqlite3, sys, unicodedata, html

LOG_GLOB = "/src/data/logs/*sniper.log*"
DB = os.environ.get("DB_PATH", "/work/test.db")
CONSOLE_RADARS = ("new_3ds_family", "switch_oled", "switch_std_lite", "ps_vita", "retro_handhelds",
                  "steam_deck_handhelds", "ps5_consoles_gta6")
KNOWN_JUNK = {  # alertas de radares de consola que en realidad eran juegos/accesorios (revisadas a mano)
    "9 juegos de Mario para Nintendo 3DS", "Dragon Ball Sparking Zero Collector PS5", "FC 25 Nintendo Switch EA Sports",
    "Lotto 6 giochi Nintendo 3DS", "The Legend of Zelda: Tri Force Heroes – Nintendo 3DS",
    "Volante Logitech G923 con Pedales y Shifter", "Zelda Nintendo 3DS",
}


def norm(t):
    t = html.unescape(t or "").lower()
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    t = re.sub(r"[’'`´]", "", t)
    return re.sub(r"\s+", " ", t).strip()


STRONG = re.compile(r"\b(consola|console|konsole|hac-?001|heg-?001|hdh-?001|ctr-?001|spr-?001|red-?001|ktr-?001|jan-?001|pch-?[12]0\d\d|cfi-?[12]\d{3}|ags-?[01]01|oled|lite|v1|v2|slim|digital(e|a)?|lector|xl|ll|new ?3ds|new ?2ds|2ds ?xl|steam ?deck|rog ally|legion go|gba ?sp|advance ?sp|game ?boy micro|dsi ?xl|vita (1000|2000|slim|oled)|(1ere|primera|first|1a) (edicion|edition)|(edicion|edition|edizione) (digital(e)?|standard|estandar|disco|lector|slim|fat))\b")
WEAK = re.compile(r"\b(completa|completo|con caja|caja original|con dock|\d{2,4} ?(gb|go|tb|to)|portatil)\b")
GAME = re.compile(r"\b(juegos?|videojuegos?|jeux?|giochi|gioco|jogos?|games?|cartuchos?|cartouches?|cartucce|cartuccia|cartridge|deluxe|remaster(ed)?|goty|definitive|collection|trilogy|coleccionista|collector|zelda|mario|luigis?|pokemon|kirby|metroid|animal crossing|splatoon|smash|xenoblade|fire emblem|octopath|minecraft|sonic|hogwarts|fifa|fc ?2[4-9]|just dance|ring fit|tomodachi|yo-?kai|monster hunter|disgaea|god eater|jump force|tales of|persona \d|bayonetta|donkey kong|pikmin|layton|inazuma|dragon (ball|quest)|naruto|one piece|call of duty|gta|grand theft auto|red dead|spider-?man|god of war|uncharted|horizon|gran turismo|ghost of|assassins? creed|advance wars)\b")
ACC_FIRST = re.compile(r"^\W*(\d+\s*)?(accesorios?|accessoires?|accessori|joy-?cons?|mandos?|controllers?|manettes?|dualsense|dualshock|pro controller|fundas?|housse|custodia|dock|base de carga|cargador|chargeur|caricatore|volante|protector|carcasa|skin|bolsa|maletin|estuche|soporte|grip|auriculares|casque|cuffie|cable|adaptador|tarjeta|micro ?sd|lote (de )?(juegos|jogos|jeux|giochi)|pack (de )?juegos|lot de jeux)\b")
ACC_ANY = re.compile(r"\b(joy-?cons?|mandos?|controllers?|manettes?|dualsense|dualshock)\b")
BUNDLE = re.compile(r"(\+|&|\b(con|avec|with|pack|lote|bundle|et|y|e|and|plus|mas)\b)")
SWITCH2 = re.compile(r"\bswitch ?2\b")
REJECT = ("game_only", "accessory_only", "out_of_scope")


def classify_console_listing(title, radar_id=""):
    t = norm(title)
    if radar_id in ("switch_std_lite", "switch_oled") and SWITCH2.search(t):
        return "out_of_scope"
    strong, weak, bundle = bool(STRONG.search(t)), bool(WEAK.search(t)), bool(BUNDLE.search(t))
    if ACC_FIRST.search(t) and not re.search(r"\b(consola|console)\b", t):
        return "accessory_only"
    if GAME.search(t) and not strong and not bundle:
        return "game_only"
    if ACC_ANY.search(t) and not (strong or weak or bundle):
        return "accessory_only"
    return "console" if (strong or weak) else "unknown"


def build_negatives():
    line_re = re.compile(r"\[[A-Z]+ (?:DESCARTADO TEXTO PUERTA 1|DESCARTADO IA)\] (.*) \((\d+(?:\.\d+)?)€\) -> (.*)$")
    neg = set()
    for path in glob.glob(LOG_GLOB):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = line_re.search(line.strip())
                if not m:
                    continue
                reason = m.group(3).lower()
                if re.search(r"videojuego suelto|juego suelto|mandos? suelt|joy-?cons? suelt", reason) or \
                   (re.search(r"juego|videojuego|cartucho|mando|joy-?con|accesori|funda|dock\b", reason) and "consola" in reason):
                    neg.add(m.group(1))
    return sorted(neg)


def build_positives():
    c = sqlite3.connect(DB)
    rows = c.execute(f"SELECT DISTINCT title FROM deals WHERE radar_id IN ({','.join('?' * len(CONSOLE_RADARS))})",
                     CONSOLE_RADARS).fetchall()
    return sorted({r[0] for r in rows if r[0]})


def report(name, titles, radar=""):
    counts = {}
    for t in titles:
        k = classify_console_listing(t, radar)
        counts[k] = counts.get(k, 0) + 1
    rejected = sum(v for k, v in counts.items() if k in REJECT)
    print(f"{name}: n={len(titles)} rechazados={rejected} ({rejected / max(1, len(titles)):.1%}) {counts}")


if __name__ == "__main__":
    neg, pos = build_negatives(), build_positives()
    if len(sys.argv) > 1:  # opcional: volcar datasets (solo títulos) para congelarlos en tests/unit/data/
        out = sys.argv[1]
        json.dump(neg, open(os.path.join(out, "console_neg.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        json.dump(pos, open(os.path.join(out, "console_pos.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    report("NEGATIVOS (juegos/accesorios rechazados por la IA)", neg)
    report("NEGATIVOS en radar switch_std_lite (regla Switch 2)", neg, "switch_std_lite")
    report("POSITIVOS (alertas de radares de consola)", pos)
    lost = [t for t in pos if classify_console_listing(t) in REJECT and t not in KNOWN_JUNK]
    print(f"Consolas reales perdidas (rechazadas y fuera de KNOWN_JUNK): {len(lost)}")
    for t in lost:
        print(f"   [{classify_console_listing(t)}] {t}")
