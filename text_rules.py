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
