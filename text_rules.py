"""Matcher de tokens con límites de palabra y negación. Sin dependencias ni E/S.

Criterio de diseño: una negación mal detectada solo envía el anuncio a la IA (cuesta una
llamada); un bloqueo falso pierde el chollo. Por eso la ventana de negación es corta (5
palabras) y se corta en los signos de puntuación.
"""
import re
import html
import unicodedata
from functools import lru_cache

NEG_CUE = re.compile(
    r"\b(sin|no|ni|nunca|nada de|ningun[oa]?|cero|libre de|jamas|sans|pas de|aucune?|senza|nessun[oa]?|without|free of)\b"
)
CLAUSE_BREAK = ".,;:!?\n"


_ELISION = re.compile(r"\b(?:[jldnsmct]|qu|dell|all|nell|sull|un)['’`´](?=\w)")
_FINAL_ACUTE = re.compile(r"(?<=\w)[áéíóú](?!\w)")


def norm(text: str) -> str:
    """Minúsculas, sin acentos y sin apóstrofos.

    Excepción: la vocal acentuada (acento agudo) al FINAL de palabra se conserva, porque ahí el
    acento distingue «compro/achète» (comprar) de «compró/acheté» (ya comprado). Medido en el replay
    de `deals`: sin esta excepción 6 alertas legítimas ("se compró nueva", "acheté en 2025") caían
    por las blacklists "compro" y "achete".
    """
    t = unicodedata.normalize("NFC", html.unescape(text or "").lower())
    keep = {}

    def _protect(m):
        keep[len(keep)] = m.group(0)
        return f"\ue000{len(keep) - 1}\ue001"

    t = _FINAL_ACUTE.sub(_protect, t)
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    t = re.sub(r"\ue000(\d+)\ue001", lambda m: keep[int(m.group(1))], t)
    t = _ELISION.sub(lambda m: m.group(0)[:-1] + " ", t)  # j'achète -> "j achete": la elisión no pega la palabra siguiente
    t = re.sub(r"[’'`´]", "", t)
    return re.sub(r"[ \t\r]+", " ", t)


@lru_cache(maxsize=8192)
def _pattern(token: str, prefix: bool = False) -> re.Pattern:
    words = norm(token).strip().split()
    body = r"[\s\-]+".join(re.escape(w) for w in words)
    if prefix and not (len(words) == 1 and len(words[0]) <= 4):
        # Identificación de modelo ("ipad 9" en "ipad 9th", "ipad 9ème"): solo límite izquierdo.
        return re.compile(rf"(?<!\w){body}")
    plural = r"(?:s|es)?" if len(words) == 1 and len(words[0]) >= 4 and words[0][-1].isalpha() else ""
    return re.compile(rf"(?<!\w){body}{plural}(?!\w)")


@lru_cache(maxsize=8192)
def _token_is_negative(token: str) -> bool:
    return bool(NEG_CUE.match(norm(token).strip()))


def _negated(text_n: str, start: int, window: int = 5) -> bool:
    pre = text_n[max(0, start - 80):start]
    cut = max(pre.rfind(ch) for ch in CLAUSE_BREAK)
    if cut >= 0:
        pre = pre[cut + 1:]
    return bool(NEG_CUE.search(" ".join(pre.split()[-window:])))


def has_token(text_n: str, token: str, negatable: bool = True, prefix: bool = False) -> bool:
    """True si hay al menos una aparición NO negada. text_n debe venir de norm().

    prefix=True: para identificar modelos (must_contain_any, topes por submodelo); los tokens de
    más de 4 caracteres o con espacios solo exigen límite izquierdo, como la subcadena anterior.
    """
    if negatable and _token_is_negative(token):   # "sin joycon", "no enciende" ya son negativos
        negatable = False
    return any(not negatable or not _negated(text_n, m.start()) for m in _pattern(token, prefix).finditer(text_n))


@lru_cache(maxsize=256)
def _prefilter(tokens: tuple) -> re.Pattern:
    """Unión de subcadenas de todos los tokens: si no casa, ningún token puede casar (barato)."""
    bodies = []
    for tk in tokens:
        words = norm(tk).strip().split()
        if words:
            bodies.append(r"[\s\-]+".join(re.escape(w) for w in words))
    return re.compile("|".join(bodies)) if bodies else re.compile(r"(?!x)x")


def first_token(text_n: str, tokens, negatable: bool = True, prefix: bool = False):
    """Primer token de `tokens` con aparición válida (ver has_token) o None. Descarte rápido
    con una sola regex antes de comprobar token a token."""
    tokens = tuple(tokens)
    if not _prefilter(tokens).search(text_n):
        return None
    for tk in tokens:
        if has_token(text_n, tk, negatable=negatable, prefix=prefix):
            return tk
    return None


# ---------------------------------------------------------------------------
# T1.2: clasificador determinista consola / juego suelto / accesorio suelto.
# Mira SOLO el título. Reglas del prototipo v2 (docs/audits/2026-11-11-sondas/console_classifier_eval.py),
# validadas contra datos reales: atrapan el 57 % de los juegos/accesorios que hoy rechaza la IA y no
# pierden ninguna de las consolas reales alertadas. `unknown` sigue yendo a la IA.
# ---------------------------------------------------------------------------
_CC_STRONG = re.compile(r"\b(consola|console|konsole|hac-?001|heg-?001|hdh-?001|ctr-?001|spr-?001|red-?001|ktr-?001|jan-?001|pch-?[12]0\d\d|cfi-?[12]\d{3}|ags-?[01]01|oled|lite|v1|v2|slim|digital(e|a)?|lector|xl|ll|new ?3ds|new ?2ds|2ds ?xl|steam ?deck|rog ally|legion go|gba ?sp|advance ?sp|game ?boy micro|dsi ?xl|vita (1000|2000|slim|oled)|(1ere|primera|first|1a) (edicion|edition)|(edicion|edition|edizione) (digital(e)?|standard|estandar|disco|lector|slim|fat))\b")
_CC_WEAK = re.compile(r"\b(completa|completo|con caja|caja original|con dock|\d{2,4} ?(gb|go|tb|to)|portatil)\b")
_CC_GAME = re.compile(r"\b(juegos?|videojuegos?|jeux?|giochi|gioco|jogos?|games?|cartuchos?|cartouches?|cartucce|cartuccia|cartridge|deluxe|remaster(ed)?|goty|definitive|collection|trilogy|coleccionista|collector|zelda|mario|luigis?|pokemon|kirby|metroid|animal crossing|splatoon|smash|xenoblade|fire emblem|octopath|minecraft|sonic|hogwarts|fifa|fc ?2[4-9]|just dance|ring fit|tomodachi|yo-?kai|monster hunter|disgaea|god eater|jump force|tales of|persona \d|bayonetta|donkey kong|pikmin|layton|inazuma|dragon (ball|quest)|naruto|one piece|call of duty|gta|grand theft auto|red dead|spider-?man|god of war|uncharted|horizon|gran turismo|ghost of|assassins? creed|advance wars)\b")
_CC_ACC_FIRST = re.compile(r"^\W*(\d+\s*)?(accesorios?|accessoires?|accessori|joy-?cons?|mandos?|controllers?|manettes?|dualsense|dualshock|pro controller|fundas?|housse|custodia|dock|base de carga|cargador|chargeur|caricatore|volante|protector|carcasa|skin|bolsa|maletin|estuche|soporte|grip|auriculares|casque|cuffie|cable|adaptador|tarjeta|micro ?sd|lote (de )?(juegos|jogos|jeux|giochi)|pack (de )?juegos|lot de jeux)\b")
_CC_ACC_ANY = re.compile(r"\b(joy-?cons?|mandos?|controllers?|manettes?|dualsense|dualshock)\b")
_CC_BUNDLE = re.compile(r"(\+|&|\b(con|avec|with|pack|lote|bundle|et|y|e|and|plus|mas)\b)")
_CC_CONSOLE_WORD = re.compile(r"\b(consola|console)\b")
_CC_SWITCH2 = re.compile(r"\bswitch ?2\b")


def classify_console_listing(title: str, radar_id: str = "") -> str:
    """'console' | 'game_only' | 'accessory_only' | 'out_of_scope' | 'unknown' según el TÍTULO."""
    t = norm(title)
    if radar_id in ("switch_std_lite", "switch_oled") and _CC_SWITCH2.search(t):
        return "out_of_scope"                      # Switch 2 (consola o juego) no entra en radares de Switch 1
    strong, weak, bundle = bool(_CC_STRONG.search(t)), bool(_CC_WEAK.search(t)), bool(_CC_BUNDLE.search(t))
    if _CC_ACC_FIRST.search(t) and not _CC_CONSOLE_WORD.search(t):
        return "accessory_only"
    if _CC_GAME.search(t) and not strong and not bundle:
        return "game_only"
    if _CC_ACC_ANY.search(t) and not (strong or weak or bundle):
        return "accessory_only"
    return "console" if (strong or weak) else "unknown"
