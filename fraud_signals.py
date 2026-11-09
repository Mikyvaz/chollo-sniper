#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fraud_signals.py — Motor determinista de detección de fraude en capas (Boti Suite v4.4).
Unifica:
  - Detección exhaustiva de teléfonos normales y camuflados (P2-1)
  - Detección de canales de contacto externos y pagos no protegidos (P2-2, P2-3)
  - Huella perceptual dHash (8x8) para fotos reutilizadas entre vendedores/plataformas (P2-5)
  - Análisis de anzuelos de precio imposible respecto al suelo CeX (P2-6)
  - Tabla fraud_log para auditoría completa de rechazos
"""

import re
import io
import html
import json
import sqlite3
import time
import threading
from datetime import datetime
from PIL import Image

try:
    from curl_cffi import requests
except ImportError:
    import requests

# ==================== TOKENS CATEGORIZADOS ====================

GENERIC_SELLER_NAMES = {"particular", "particular milanuncios", "usuario wallapop", "vendedor", "vendedor ebay", "cash converters", "todocoleccion"}

# 1. Pagos externos no protegidos (Riesgo 4 = Rechazo inmediato)
EXTERNAL_PAYMENT_TOKENS = (
    # Español
    "bizum", "solo bizum", "solo por bizum", "pago por bizum", "transferencia",
    "transferencia bancaria", "ingreso en cuenta", "pago amigo", "amigos y familiares",
    "amigo o familiar", "cripto", "bitcoin", "usdt", "tarjeta regalo", "paysafecard",
    "western union", "moneygram", "solo transferencia", "solo pago por bizum",
    "envio por seur urgente", "envío por seur urgente", "pago fuera de wallapop",
    # Francés
    "virement bancaire", "virement", "uniquement via virement", "paypal entre proches",
    "sans frais paypal", "virement instantane", "virement instantané", "que virement",
    # Italiano
    "bonifico bancario", "bonifico", "ricarica postepay", "postepay",
    "amici e parenti", "solo bonifico",
    # Inglés
    "bank transfer", "wire transfer", "friends and family", "gift card", "crypto",
    "direct transfer"
)

# 2. Contacto externo y bypass de la plataforma (Riesgo 4 = Rechazo inmediato)
CONTACT_EXTERNAL_PATTERNS = (
    r"(?:whatsapp|watsap|whasap|wasap|whapp|wapp|wsp)\b",
    r"(?:telegram|t\.me/)\b",
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    r"(?:no\s+me\s+(?:va|funciona)\s+(?:el\s+)?chat)",
    r"regalo\s+de\s+comuni[oó]n",
    r"(?:escr[ií]beme|h[aá]blame|contacta(?:r|me)?|ll[aá]mame)\s+(?:al|por|a)?\s*[:\d\+]+",
    r"(?:prima di acquistare|avant d['’]acheter|ne pas acheter directement|écrivez-moi|scrivimi)"
)

# 3. Urgencia sospechosa y anzuelos clásicos de estafa
URGENCY_SCAM_TOKENS = (
    "vendo por traslado", "traslado al extranjero", "mudanza al extranjero",
    "me mudo de pais", "me mudo de país", "viaje urgente", "herencia de",
    "regalo de mi ex", "urge venta por viaje", "demenagement a l'etranger",
    "déménagement à l'étranger", "mutation professionnelle",
    "trasferimento all'estero", "no me va el chat", "no me funciona el chat",
    "regalo de comunion", "regalo de comunión", "solo pago por bizum",
    "envio por seur urgente", "envío por seur urgente"
)

# 4. Defectos, averías y productos rotos (Riesgo 4 = Rechazo inmediato)
DEFECT_TOKENS = (
    # Español
    "sin probar", "no funciona", "no enciende", "no carga", "no da imagen", "no da video", "no lee",
    "lector roto", "lector no funciona", "bisagra rota", "bisagra partida", "pantalla partida",
    "pantalla quebrada", "pantalla rota", "pantalla dañada", "pantalla en negro", "pantalla blanca",
    "mancha en pantalla", "lineas en pantalla", "líneas en pantalla", "cristal rajado", "cristal roto",
    "error e18", "error e32", "luz roja", "luz azul", "joycon drift", "con drift", "sin consola",
    "solo tablet", "solo la pantalla", "sin joycon", "sin joy-con", "sin joycons", "sin mandos",
    "cartucho vacio", "cartucho vacío", "falsificacion", "falsificación", "bloqueo icloud",
    "bloqueado icloud", "cuenta icloud", "para piezas", "para reparar", "a reparar", "averiado",
    "averiada", "defectuoso", "defectuosa", "tactil no funciona", "táctil no funciona",
    "solo la consola no", "pantalla superior a reemplazar", "placa base", "placa madre", "motherboard",
    "carte mere", "carte mère", "scheda madre", "despiece", "repuesto de pantalla", "pantalla superior",
    "pantalla inferior", "flex de pantalla", "pantalla de repuesto",
    # Francés
    "hors service", "pour pieces", "pour pièces", "en panne", "ne fonctionne pas", "ne s'allume pas",
    "ne charge pas", "ecran hs", "écran hs", "ecran casse", "écran cassé", "ecran fissure",
    "écran fissuré", "fissure", "charniere cassee", "charnière cassée", "fonctionne partiellement",
    "ne repondent pas", "ne répondent pas", "plusieurs defauts", "plusieurs défauts",
    "changer la coque", "changer la batterie", "a reparer", "à réparer", "sans console",
    "sans joycon", "sans joy-con", "sans manette", "vendu seul sans",
    # Italiano
    "non funzionante", "non si accende", "non carica", "per pezzi", "per parti", "pezzi di ricambio",
    "da riparare", "schermo rotto", "schermo crepato", "display rotto", "vetro rotto", "crepa",
    "cerniera rotta", "manca joystick", "senza joycon", "senza joy-con", "solo corpo tablet",
    "difettoso", "difettosa", "guasto", "guasta", "non procedere",
    # Portugués / Inglés
    "avariado", "avariada", "para pecas", "para peças", "ecra partido", "ecrã partido",
    "for parts", "not working", "broken screen", "cracked screen", "faulty", "spares or repair",
    "no joycons", "tablet only"
)

# 5. Prefijos y títulos que delatan accesorios o cajas vacías
ACCESSORY_TITLE_PREFIXES = (
    "funda ", "carcasa ", "protector ", "mando ", "caja ", "bolsa ", "mochila ",
    "cargador ", "cable ", "soporte ", "adaptador ", "correa ", "bateria ", "batería ",
    "manual ", "instrucciones ", "estuche ", "maletin ", "maletín ",
    "cristal ", "juego ", "juegos ", "videojuego ", "lote de juegos", "pack de juegos",
    "coque ", "housse ", "boite ", "boîte ", "chargeur ", "etui ", "étui ", "verre ",
    "sacoche ", "pochette ", "casque ", "manette ", "jeu ", "jeux ", "lot de jeux",
    "custodia ", "scatola ", "caricatore ", "pellicola ", "borsa ", "zaino ", "cuffie ",
    "gioco ", "giochi ", "lotto giochi", "capa ", "caixa ", "auriculares ",
    "case for ", "cover for ", "box for ", "charger for ", "bag for ", "game for ",
    "placa base ", "placa madre ", "motherboard ", "pantalla ", "display ", "repuesto ", "recambio ",
    "despiece ", "piezas "
)


# ==================== DETECCIÓN DE TELÉFONOS (P2-1) ====================

class PhoneDetectionResult(tuple):
    def __new__(cls, found: bool, match_str: str = ""):
        return super().__new__(cls, (bool(found), str(match_str)))

    @property
    def found(self):
        return self[0]

    @property
    def match_str(self):
        return self[1]

    def __bool__(self):
        return bool(self[0])


def detect_phone_number(text):
    """
    Detecta números de teléfono normales y camuflados (P2-1).
    Ignora códigos y números de serie de producto precedidos por ref, sn, id, modelo, etc.
    Retorna tupla (found: bool, match_str: str) compatible con desempaquetado y evaluación booleana directa.
    """
    if not text:
        return PhoneDetectionResult(False, "")

    # Ignorar códigos precedidos por ref/serie/sn/s/n/id/ean/isbn/modelo/cod
    cleaned = re.sub(
        r"(?:ref|referencia|serie|s/n|sn|id|ean|isbn|cod|codigo|código|modelo)[:\s#-]*\d{8,14}",
        "",
        text,
        flags=re.IGNORECASE
    )

    # 1. Regex estructurado con prefijo internacional (+34, 0034, +33, etc.)
    m_intl = re.search(r"(?:\+|00)(?:34|33|39|351|44)[\s\-\.]*[67]\d{2}[\s\-\.]*\d{2,3}[\s\-\.]*\d{2,3}(?:[\s\-\.]*\d{2,3})?\b", cleaned)
    if m_intl:
        return PhoneDetectionResult(True, m_intl.group(0))

    # 2. Formato estándar con separadores de 2 dígitos (ej: 612 34 56 78 o 612-34-56-78 o 612.34.56.78)
    m_sep = re.search(r"\b[67]\d{2}[\s\-\.]+\d{2}[\s\-\.]+\d{2}[\s\-\.]+\d{2}\b", cleaned)
    if m_sep:
        return PhoneDetectionResult(True, m_sep.group(0))

    # 3. Formato con separadores de 3 dígitos o paréntesis: 612 345 678 o 612-345-678 o (612) 345 678
    m_sep3 = re.search(r"(?:\([67]\d{2}\)|\b[67]\d{2})[\s\-\.]+\d{3}[\s\-\.]+\d{3}\b", cleaned)
    if m_sep3:
        return PhoneDetectionResult(True, m_sep3.group(0))

    # 4. Formato contextual de contacto directo seguido de 9 dígitos
    m_ctx = re.search(
        r"(?:tlf|tel|telefono|teléfono|whatsapp|wasap|wsp|whasap|llamame|llámame|escribeme|escríbeme|contactar|contacto|hablar\s+al|movil|móvil)[:\s\-\.]*([67]\d{8})\b",
        cleaned,
        re.IGNORECASE
    )
    if m_ctx:
        return PhoneDetectionResult(True, m_ctx.group(0))

    # 5. Detección camuflada con espacios entre cada dígito (ej: "6 1 2 3 4 5 6 7 8")
    m_spaced = re.search(r"\b([67](?:\s+\d){8})\b", cleaned)
    if m_spaced:
        return PhoneDetectionResult(True, m_spaced.group(1))

    return PhoneDetectionResult(False, "")


# ==================== HUELLA PERCEPTUAL DHASH (P2-5) ====================

def calculate_dhash(img_bytes, hash_size=8):
    """
    Calcula perceptual dHash (difference hash) de 8x8 (64 bits, 16 caracteres hexadecimales).
    Invariante a escalado, compresión JPEG y modificaciones menores de brillo.
    """
    if not img_bytes:
        return None
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            img = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            pixels = list(img.getdata())
            diff = []
            for row in range(hash_size):
                row_start = row * (hash_size + 1)
                for col in range(hash_size):
                    diff.append(pixels[row_start + col] > pixels[row_start + col + 1])
            decimal_value = 0
            for index, value in enumerate(diff):
                if value:
                    decimal_value += 2 ** index
            return hex(decimal_value)[2:].zfill(hash_size * hash_size // 4)
    except Exception:
        return None


def hamming_distance(h1, h2):
    """Calcula la distancia de Hamming entre dos hashes hexadecimales."""
    if not h1 or not h2 or len(h1) != len(h2):
        return 999
    try:
        x = int(h1, 16) ^ int(h2, 16)
        return bin(x).count("1")
    except Exception:
        return 999


def check_and_record_image_hash(conn, img_hash, platform, item_id, radar_id="", max_hamming_dist=4):
    """
    Comprueba si el hash perceptual ya fue visto en un anuncio DISTINTO (P2-5).
    Verifica coincidencia exacta (O(1)) o similitud por distancia Hamming <= max_hamming_dist
    en candidatos recientes del mismo radar.
    Retorna: (is_reused: bool, match_info: dict or None)
    """
    if not conn or not img_hash:
        return False, None
    try:
        c = conn.cursor()
        sid = str(item_id)
        plat = platform.upper()

        # 1. Búsqueda exacta rápida indexada
        c.execute(
            """SELECT platform, item_id, radar_id, seen_at, hash
               FROM seen_image_hashes
               WHERE hash = ? AND (item_id != ? OR platform != ?)
               LIMIT 1""",
            (img_hash, sid, plat)
        )
        row = c.fetchone()

        # 2. Si no hay coincidencia exacta y hay radar_id, buscar por distancia Hamming en los últimos registros
        if not row and max_hamming_dist > 0 and radar_id:
            c.execute(
                """SELECT platform, item_id, radar_id, seen_at, hash
                   FROM seen_image_hashes
                   WHERE radar_id = ? AND (item_id != ? OR platform != ?)
                   ORDER BY seen_at DESC LIMIT 300""",
                (radar_id, sid, plat)
            )
            for cand in c.fetchall():
                cand_plat, cand_id, cand_radar, cand_seen, cand_hash = cand
                if hamming_distance(img_hash, cand_hash) <= max_hamming_dist:
                    row = cand
                    break

        # Registrar el hash para futuras comparaciones de forma idempotente
        c.execute(
            """INSERT OR IGNORE INTO seen_image_hashes (hash, platform, item_id, radar_id, seen_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (img_hash, plat, sid, radar_id)
        )
        conn.commit()

        if row:
            return True, {
                "platform": row[0],
                "item_id": row[1],
                "radar_id": row[2],
                "seen_at": row[3],
                "matched_hash": row[4]
            }
        return False, None
    except Exception:
        return False, None


# ==================== ANÁLISIS DE PRECIO IMPOSIBLE (P2-6) ====================

def check_impossible_price(price, cex_cash, threshold_ratio=0.35):
    """
    Si price < 0.35 * cex_cash para producto de alta demanda (CeX cash > 90€),
    es el patrón clásico de estafa de anzuelo (P2-6).
    """
    p = float(price or 0)
    c_cash = float(cex_cash or 0)
    if c_cash >= 90.0 and p > 0:
        if p < (c_cash * threshold_ratio):
            return True, f"Precio sospechosamente bajo ({p:.0f}€ vs Suelo CeX {c_cash:.0f}€, ratio {p/c_cash:.1%})"
    return False, ""


# A-004: solo estas señales cuentan como "flag" de reincidencia. Cuenta nueva, 0 valoraciones
# o foto reutilizada ya bloquean el anuncio concreto, pero no deben vetar al vendedor.
SEVERE_FLAG_CODES = {"CONTACT_PHONE", "CONTACT_EXTERNAL", "PAYMENT_EXTERNAL"}
REPEAT_FLAG_TTL_DAYS = 30


def migrate_seller_reputation(conn):
    """A-004: añade last_flag_at. Al migrar se anulan los flags antiguos, que se sumaban
    por señales leves (cuenta nueva / 0 valoraciones) y vetaban de por vida."""
    if not conn:
        return
    try:
        c = conn.cursor()
        cols = [r[1] for r in c.execute("PRAGMA table_info(seller_reputation)").fetchall()]
        if cols and "last_flag_at" not in cols:
            c.execute("ALTER TABLE seller_reputation ADD COLUMN last_flag_at DATETIME")
            c.execute("UPDATE seller_reputation SET fraud_flags = 0 WHERE fraud_flags > 0")
            conn.commit()
            print("[MIGRACIÓN] seller_reputation: añadida last_flag_at y anulados flags leves antiguos (A-004)")
    except Exception as e:
        # Otro proceso pudo migrar a la vez (columna duplicada): no es un error
        if "duplicate column" not in str(e).lower():
            print(f"[WARN] Migración seller_reputation: {e}")


def init_fraud_tables(conn):
    """Inicializa las tablas de fraude y reputación de vendedores si no existen."""
    if not conn:
        return
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS seller_reputation (
            seller_id TEXT PRIMARY KEY,
            platform TEXT,
            total_seen INTEGER DEFAULT 0,
            fraud_flags INTEGER DEFAULT 0,
            avg_risk_score REAL DEFAULT 0.0,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS fraud_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            item_id TEXT,
            title TEXT,
            price REAL,
            risk INTEGER,
            reasons_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS image_hashes (
            hash TEXT PRIMARY KEY,
            platform TEXT,
            item_id TEXT,
            radar_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    migrate_seller_reputation(conn)


# ==================== ENRIQUECIMIENTO Y EVALUACIÓN DE VENDEDORES (BLOQUE 2) ====================

_SELLER_PROFILE_CACHE = {}
_SELLER_PROFILE_LOCK = threading.Lock()
_SELLER_CACHE_MAX_SIZE = 2000
_THREAD_LOCAL_HTTP = threading.local()

def _get_thread_local_session():
    sess = getattr(_THREAD_LOCAL_HTTP, "session", None)
    if sess is None:
        try:
            sess = requests.Session(impersonate="chrome124")
        except Exception:
            sess = requests.Session()
        _THREAD_LOCAL_HTTP.session = sess
    return sess

def _set_cached_seller_profile(key, profile):
    now = time.time()
    with _SELLER_PROFILE_LOCK:
        if len(_SELLER_PROFILE_CACHE) >= _SELLER_CACHE_MAX_SIZE:
            expired = [k for k, (t, _) in _SELLER_PROFILE_CACHE.items() if (now - t) >= 1800]
            if expired:
                for k in expired:
                    _SELLER_PROFILE_CACHE.pop(k, None)
            else:
                sorted_keys = sorted(_SELLER_PROFILE_CACHE.keys(), key=lambda k: _SELLER_PROFILE_CACHE[k][0])
                for k in sorted_keys[: max(1, len(sorted_keys) // 5)]:
                    _SELLER_PROFILE_CACHE.pop(k, None)
        _SELLER_PROFILE_CACHE[key] = (now, profile)

def enrich_seller_profile(item_data: dict, http_session=None, platform: str = None) -> dict:
    """
    Enriquece el perfil del vendedor consultando la API pública de la plataforma o extrayendo
    métricas reales con caché en memoria TTL de 30 minutos por (platform, seller_id).
    Devuelve un diccionario normalizado y actualiza item_data in-place.
    """
    if not isinstance(item_data, dict):
        return {}

    platform = (platform or str(item_data.get("platform") or "")).strip().upper()
    seller = item_data.get("seller")
    if not isinstance(seller, dict):
        if any(k in item_data for k in ("reviews_count", "rating", "seller_reviews_count", "seller_rating", "id", "seller_id")):
            seller = item_data
        else:
            seller = {}
            item_data["seller"] = seller

    seller_id = str(seller.get("id") or seller.get("seller_id") or item_data.get("user_id") or "").strip()
    cache_key = (platform, seller_id)

    now = time.time()
    if seller_id:
        with _SELLER_PROFILE_LOCK:
            if cache_key in _SELLER_PROFILE_CACHE:
                cached_time, cached_profile = _SELLER_PROFILE_CACHE[cache_key]
                if (now - cached_time) < 1800:
                    seller.update(cached_profile)
                    item_data["seller_reviews_count"] = cached_profile.get("seller_reviews_count", 0)
                    item_data["seller_rating"] = cached_profile.get("seller_rating")
                    item_data["seller_trust_badge"] = cached_profile.get("seller_trust_badge", "")
                    return cached_profile

    # Valores existentes en item_data o seller
    reviews_count = seller.get("seller_reviews_count", seller.get("reviews_count", seller.get("sales", seller.get("feedback_count"))))
    rating = seller.get("seller_rating", seller.get("rating", seller.get("scoring", seller.get("feedback_reputation"))))
    joined_days = seller.get("seller_joined_days", seller.get("joined_days", seller.get("age_days")))
    verified = seller.get("seller_verified", seller.get("verified", False))

    should_cache = True
    if platform == "CASHCONVERTERS" or "CASH" in platform or seller_id == "cash_converters_official":
        profile = {
            "seller_id": seller_id or "cash_converters_official",
            "seller_name": "Cash Converters",
            "seller_reviews_count": 999,
            "seller_rating": 5.0,
            "seller_joined_days": 3650.0,
            "seller_verified": True,
            "seller_trust_badge": "🟢 Tienda Oficial Cash Converters (Garantía 3 años)",
            "platform": "CASHCONVERTERS"
        }
        _set_cached_seller_profile(cache_key, profile)
        seller.update(profile)
        item_data["seller_reviews_count"] = 999
        item_data["seller_rating"] = 5.0
        item_data["seller_trust_badge"] = profile["seller_trust_badge"]
        return profile

    if platform == "WALLAPOP":
        if seller_id and (reviews_count is None or rating is None):
            sess = http_session or _get_thread_local_session()
            wallapop_http_ok = False
            try:
                url = f"https://api.wallapop.com/api/v3/users/{seller_id}"
                r = sess.get(url, timeout=2.5)
                if r.status_code == 200:
                    wallapop_http_ok = True
                    u_data = r.json()
                    stats = u_data.get("stats") or {}
                    u_rev = stats.get("reviews") or {}
                    if isinstance(u_rev, dict) and "total" in u_rev:
                        reviews_count = u_rev["total"]
                    elif "sales" in stats:
                        reviews_count = stats["sales"]
                    elif "reviews_count" in u_data:
                        reviews_count = u_data["reviews_count"]

                    raw_scoring = u_data.get("scoring") or stats.get("rating")
                    if raw_scoring is not None:
                        rating = raw_scoring

                    reg_date = u_data.get("register_date") or u_data.get("created_at")
                    if reg_date:
                        try:
                            ts = float(reg_date)
                            if ts > 1e11:
                                ts /= 1000.0
                            joined_days = round((now - ts) / 86400.0, 1)
                        except Exception:
                            pass

                    verif = u_data.get("verification") or {}
                    if isinstance(verif, dict):
                        verified = any(bool(v) for v in verif.values())
            except Exception:
                pass

            if reviews_count is None or rating is None:
                try:
                    stats_url = f"https://api.wallapop.com/api/v3/users/{seller_id}/stats"
                    st_headers = {"User-Agent": "Mozilla/5.0", "X-DeviceOS": "0"}
                    st_r = sess.get(stats_url, headers=st_headers, timeout=2.5)
                    if st_r.status_code == 200:
                        wallapop_http_ok = True
                        st_data = st_r.json()
                        rev_cnt = 0
                        sells_cnt = 0
                        for c in st_data.get("counters", []):
                            if isinstance(c, dict):
                                c_type = c.get("type")
                                c_val = c.get("value") or 0
                                if c_type == "reviews":
                                    rev_cnt = max(rev_cnt, int(c_val))
                                elif c_type in ("sells", "sold"):
                                    sells_cnt = max(sells_cnt, int(c_val))
                        reviews_count = max(rev_cnt, sells_cnt)
                        seller["sales"] = sells_cnt

                        if st_data.get("rating_average") is not None and float(st_data.get("rating_average") or 0) > 0:
                            rating = float(st_data["rating_average"])
                        else:
                            for r_item in st_data.get("ratings", []):
                                if isinstance(r_item, dict) and r_item.get("type") == "reviews":
                                    val = r_item.get("value")
                                    if val is not None:
                                        rating = float(val)
                                        break
                except Exception:
                    pass

            if not wallapop_http_ok:
                should_cache = False

    elif platform == "VINTED":
        if seller_id and (reviews_count is None or rating is None):
            sess = http_session or _get_thread_local_session()
            try:
                url = f"https://www.vinted.es/api/v2/users/{seller_id}"
                r = sess.get(url, timeout=2.5)
                if r.status_code == 200:
                    u_data = r.json().get("user", {})
                    if "feedback_count" in u_data:
                        reviews_count = u_data["feedback_count"]
                    if "feedback_reputation" in u_data and u_data["feedback_reputation"] is not None:
                        try:
                            rating = round(float(u_data["feedback_reputation"]) * 5.0, 2)
                        except Exception:
                            rating = u_data["feedback_reputation"]
                    created_at = u_data.get("created_at")
                    if created_at:
                        try:
                            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                            joined_days = round((now - dt.timestamp()) / 86400.0, 1)
                        except Exception:
                            pass
            except Exception:
                pass

    # Normalizar valores
    try:
        reviews_count = int(reviews_count) if reviews_count is not None else 0
    except Exception:
        reviews_count = 0

    try:
        rating = float(rating) if rating is not None else None
    except Exception:
        rating = None

    try:
        joined_days = float(joined_days) if joined_days is not None else None
    except Exception:
        joined_days = None

    # Normalización de escala preventiva (Regla v5.2)
    if rating is not None:
        if 0.0 < rating <= 1.0 or (platform and platform.upper() == "VINTED" and rating <= 1.0):
            rating = round(rating * 5.0, 2)
        elif rating > 5.0:
            rating = round(rating / 20.0, 2)

    if reviews_count == 0:
        rating = None

    badge = ""
    if reviews_count >= 5 and rating is not None and rating >= 4.5:
        badge = f"🟢 Vendedor Fiable ({reviews_count} val., {rating:.1f}⭐)"
    elif reviews_count >= 2 and rating is not None and rating >= 4.0:
        badge = f"🟢 Vendedor Verificado ({reviews_count} val., {rating:.1f}⭐)"

    profile = {
        "seller_id": seller_id,
        "seller_name": seller.get("name") or seller.get("seller_name", ""),
        "seller_reviews_count": reviews_count,
        "reviews_count": reviews_count,
        "seller_rating": rating,
        "rating": rating,
        "seller_joined_days": joined_days,
        "seller_verified": bool(verified),
        "seller_trust_badge": badge,
        "platform": platform
    }

    if seller_id and should_cache:
        _set_cached_seller_profile(cache_key, profile)

    seller.update(profile)
    item_data["seller_reviews_count"] = reviews_count
    item_data["seller_rating"] = rating
    item_data["seller_trust_badge"] = badge

    return profile


class SellerRiskResult(dict):
    """Permite tratar el resultado de evaluate_seller_risk como dict o desempaquetar como (risk_delta, reasons, hits)."""
    def __iter__(self):
        return iter((
            self.get("risk_delta", 0),
            [h.get("label") or h.get("code") for h in self.get("hits", [])],
            [h.get("code") for h in self.get("hits", [])]
        ))


def evaluate_seller_risk(seller_or_item, price=None, platform=""):
    """
    Evalúa señales del perfil del vendedor con heurística normalizada (v5.2):
      - Tienda oficial contrastada (Cash Converters -> 0 riesgo, 100% confianza)
      - Normalización de escala preventiva: rating > 5.0 -> / 20.0
      - Regla A: Cuenta recién creada (<=14 días) con 0 valoraciones -> +3 (si precio >= 50€: +2 adicionales, total +5)
      - Regla B: Cuenta con 0 valoraciones sin antigüedad confirmada -> +2 (aviso visible, bloquea Flash)
      - Regla C: Mala reputación real -> solo si reviews_count >= 2 y rating < 3.5 -> +3
      - Regla D: Bonus vendedor fiable -> si reviews_count >= 5 y rating >= 4.5 -> -1 y sello verde
    """
    if not seller_or_item:
        return SellerRiskResult({"risk_delta": 0, "hits": [], "is_trusted": False, "seller_trust_badge": "", "block_flash": False})

    if isinstance(seller_or_item, dict) and "seller" in seller_or_item and isinstance(seller_or_item["seller"], dict):
        seller = dict(seller_or_item["seller"])
        if price is None:
            price = seller_or_item.get("price")
        if not platform:
            platform = seller_or_item.get("platform", "")
    elif isinstance(seller_or_item, dict):
        seller = dict(seller_or_item)
    else:
        seller = {}

    raw_p = price or seller.get("price") or 0.0
    if isinstance(raw_p, dict):
        raw_p = raw_p.get("amount") or 0.0
    try:
        price_val = float(raw_p or 0.0)
    except (ValueError, TypeError):
        price_val = 0.0
    plat_str = str(platform or seller.get("platform") or "").upper()
    s_id = str(seller.get("id") or seller.get("seller_id") or "").lower()
    s_name = str(seller.get("name") or seller.get("seller_name") or "").lower()

    # 1. Cash Converters / Tienda oficial
    if "CASH" in plat_str or s_id == "cash_converters_official" or "cash converters" in s_name:
        return SellerRiskResult({
            "risk_delta": 0,
            "hits": [],
            "is_trusted": True,
            "seller_trust_badge": "🟢 Tienda Oficial Cash Converters (Garantía 3 años)",
            "block_flash": False,
            "seller_reviews_count": 999,
            "seller_rating": 5.0,
            "seller_verified": True
        })

    # Extraer métricas normalizadas
    reviews_count = None
    for k in ("seller_reviews_count", "reviews_count", "sales", "feedback_count"):
        if k in seller and seller[k] is not None:
            try:
                reviews_count = int(seller[k])
                break
            except Exception:
                pass
    if reviews_count is None:
        reviews_count = 0

    rating = None
    for k in ("seller_rating", "rating", "scoring", "feedback_reputation"):
        if k in seller and seller[k] is not None:
            try:
                rating = float(seller[k])
                break
            except Exception:
                pass

    joined_days = None
    for k in ("seller_joined_days", "joined_days", "age_days"):
        if k in seller and seller[k] is not None:
            try:
                joined_days = float(seller[k])
                break
            except Exception:
                pass

    verified = bool(seller.get("seller_verified") or seller.get("verified", False))

    # Normalización de escala preventiva
    if rating is not None:
        if rating > 5.0:
            rating = round(rating / 20.0, 2)

    if reviews_count == 0:
        rating = None

    hits = []
    block_flash = False
    seller_trust_badge = ""

    # Antigüedad de la cuenta
    if joined_days is not None:
        if joined_days <= 2.0:
            hits.append({
                "code": "SELLER_CREATED_TODAY",
                "category": "seller",
                "label": "Cuenta de vendedor creada hace menos de 48 horas",
                "weight": 2
            })
            block_flash = True
        elif joined_days <= 7.0:
            hits.append({
                "code": "SELLER_BRAND_NEW",
                "category": "seller",
                "label": f"Vendedor novato (cuenta de {joined_days:.0f} días)",
                "weight": 1
            })

    # Regla A — Cuenta Fantasma / Recién Creada (Alto Riesgo de Estafa)
    if reviews_count == 0:
        hits.append({
            "code": "SELLER_ZERO_REVIEWS",
            "category": "seller",
            "label": "⚠️ Vendedor con 0 valoraciones (verificar perfil/trato en mano o pago 100% dentro de la app)",
            "weight": 2
        })
        block_flash = True
        if joined_days is not None and joined_days <= 14:
            hits.append({
                "code": "SELLER_NEW_ACCOUNT_ZERO_REVIEWS",
                "category": "seller",
                "label": f"Cuenta recién creada ({joined_days:.0f} días) con 0 valoraciones",
                "weight": 3
            })
            if price_val >= 50.0:
                hits.append({
                    "code": "HIGH_TICKET_ZERO_REP_SCAM_RISK",
                    "category": "seller",
                    "label": f"Alto riesgo de estafa: artículo de {price_val:.0f}€ en cuenta sin reputación recién creada",
                    "weight": 2
                })

    # Regla C — Vendedor con Mala Reputación Real (Solo si reviews >= 2)
    if reviews_count >= 2 and rating is not None and rating < 4.0:
        weight = 3 if rating < 3.5 else 2
        hits.append({
            "code": "SELLER_LOW_RATING",
            "category": "seller",
            "label": f"Puntuación baja del vendedor ({rating:.1f}/5 con {reviews_count} valoraciones)",
            "weight": weight
        })
        block_flash = True

    # Regla D — Bonus de Vendedor Fiable ("Sello Verde")
    is_trusted = False
    if reviews_count >= 5 and rating is not None and rating >= 4.5:
        seller_trust_badge = f"🟢 Vendedor Fiable ({reviews_count} val., {rating:.1f}⭐)"
        is_trusted = True
    elif reviews_count >= 2 and rating is not None and rating >= 4.0:
        seller_trust_badge = f"🟢 Vendedor Verificado ({reviews_count} val., {rating:.1f}⭐)"
        is_trusted = True

    # Reventa masiva / Profesional encubierto
    listings = seller.get("listings_count")
    if listings is not None:
        try:
            list_val = int(listings)
            if list_val >= 50:
                hits.append({
                    "code": "SELLER_MASSIVE_LISTINGS",
                    "category": "seller",
                    "label": f"Vendedor con {list_val} anuncios activos (posible reventa masiva)",
                    "weight": 1
                })
        except Exception:
            pass

    # Bloquear flash si tiene menos de 2 reviews en Wallapop o Vinted
    if reviews_count < 2 and (not plat_str or plat_str in ("WALLAPOP", "VINTED")):
        block_flash = True

    return SellerRiskResult({
        "risk_delta": sum(h["weight"] for h in hits),
        "hits": hits,
        "is_trusted": is_trusted,
        "seller_trust_badge": seller_trust_badge,
        "block_flash": block_flash,
        "seller_reviews_count": reviews_count,
        "seller_rating": rating,
        "seller_joined_days": joined_days,
        "seller_verified": verified
    })


# ==================== MOTOR DE FRAUDE UNIFICADO ====================

def evaluate_fraud_risk(
    title="", description="", price=0.0, cex_cash=0.0, raw_images=None,
    radar_id="", conn=None, platform="", item_id="", seller=None, **kwargs
):
    """
    Motor Unificado de Detección de Fraude en Capas v1.1 (TASK-05).
    Soporta firmas flexibles:
      1) evaluate_fraud_risk(conn, item_dict, target_dict, cex_cash=0.0)
      2) evaluate_fraud_risk(title, description, price, cex_cash=0.0, ...)
      3) evaluate_fraud_risk(item_dict, target_dict)
    """
    should_hard_block = False

    if hasattr(title, "cursor") and isinstance(description, dict):
        active_conn = title
        item_obj = description
        target_obj = price if isinstance(price, dict) else {}
        clean_title = html.unescape(item_obj.get("title") or "").strip()
        clean_desc = html.unescape(item_obj.get("description") or item_obj.get("desc") or "").strip()
        raw_p = item_obj.get("price")
        if isinstance(raw_p, dict):
            raw_p = raw_p.get("amount") or 0.0
        try:
            price_val = float(raw_p or 0.0)
        except (ValueError, TypeError):
            price_val = 0.0
        platform_str = str(item_obj.get("platform") or platform or "")
        item_id_str = str(item_obj.get("id") or item_obj.get("item_id") or item_id or "")
        radar_id_str = str(target_obj.get("id") or target_obj.get("name") or radar_id or "")
        seller_obj = item_obj.get("seller") or {
            "seller_id": item_obj.get("seller_id"),
            "seller_reviews_count": item_obj.get("seller_reviews_count"),
            "seller_rating": item_obj.get("seller_rating"),
            "seller_joined_days": item_obj.get("seller_joined_days"),
            "seller_verified": item_obj.get("seller_verified")
        }
    elif isinstance(title, dict):
        active_conn = conn
        item_obj = title
        target_obj = description if isinstance(description, dict) else {}
        clean_title = html.unescape(item_obj.get("title") or "").strip()
        clean_desc = html.unescape(item_obj.get("description") or item_obj.get("desc") or "").strip()
        raw_p = item_obj.get("price")
        if isinstance(raw_p, dict):
            raw_p = raw_p.get("amount") or 0.0
        try:
            price_val = float(raw_p or 0.0)
        except (ValueError, TypeError):
            price_val = 0.0
        platform_str = str(item_obj.get("platform") or platform or "")
        item_id_str = str(item_obj.get("id") or item_obj.get("item_id") or item_id or "")
        radar_id_str = str(target_obj.get("id") or target_obj.get("name") or radar_id or "")
        seller_obj = item_obj.get("seller") or {
            "seller_id": item_obj.get("seller_id"),
            "seller_reviews_count": item_obj.get("seller_reviews_count"),
            "seller_rating": item_obj.get("seller_rating"),
            "seller_joined_days": item_obj.get("seller_joined_days"),
            "seller_verified": item_obj.get("seller_verified")
        }
    else:
        active_conn = conn
        clean_title = html.unescape(title or "").strip()
        clean_desc = html.unescape(description or "").strip()
        price_val = float(price or 0.0)
        platform_str = str(platform or "")
        item_id_str = str(item_id or "")
        radar_id_str = str(radar_id or "")
        seller_obj = seller

    text = f"{clean_title} {clean_desc}".lower()
    hits = []

    # 1. Teléfono normal o camuflado
    has_phone, phone_val = detect_phone_number(text)
    if has_phone:
        hits.append({
            "code": "CONTACT_PHONE",
            "category": "contact",
            "label": f"Teléfono detectado en el texto ({phone_val})",
            "weight": 4
        })

    # 2. Canales externos (WhatsApp, Telegram, email, etc.)
    for pat in CONTACT_EXTERNAL_PATTERNS:
        if re.search(pat, text):
            hits.append({
                "code": "CONTACT_EXTERNAL",
                "category": "contact",
                "label": "Mención a contacto externo (WhatsApp/Telegram/Email)",
                "weight": 4
            })
            break

    # 3. Pagos externos no protegidos con verificación de negación (P2-3)
    for p_token in EXTERNAL_PAYMENT_TOKENS:
        if p_token in text:
            negation_re = re.compile(
                rf"(?:no\s+(?:acepto|hago|uso|quiero|admito|recibo)|nada\s+de|ni\s+(?:hago|acepto|uso|quiero)?)\s*(?:[\w,]+\s+){{0,4}}{re.escape(p_token)}",
                re.IGNORECASE,
            )
            if negation_re.search(text):
                continue

            hits.append({
                "code": "PAYMENT_EXTERNAL",
                "category": "payment",
                "label": f"Pago externo no protegido detectado ('{p_token}')",
                "weight": 4
            })
            break

    # 4. Urgencia sospechosa
    for u_token in URGENCY_SCAM_TOKENS:
        if u_token in text:
            hits.append({
                "code": "SCAM_URGENCY",
                "category": "urgency",
                "label": f"Patrón de urgencia/traslado ('{u_token}')",
                "weight": 2
            })
            break

    # 5. Defectos o averías de hardware
    for d_token in DEFECT_TOKENS:
        if d_token in text:
            hits.append({
                "code": "PRODUCT_DEFECT",
                "category": "defect",
                "label": f"Defecto o avería detectada ('{d_token}')",
                "weight": 4
            })
            break

    # 6. Precio imposible respecto a CeX
    cex_val = float(cex_cash or 0.0)
    is_imp, imp_msg = check_impossible_price(price_val, cex_val)
    if is_imp:
        hits.append({
            "code": "IMPOSSIBLE_PRICE",
            "category": "price",
            "label": imp_msg,
            "weight": 2
        })

    # 6.5. Heurística Anti-Estafa Combinada (TASK-05)
    if cex_val >= 80.0 and price_val > 0.0 and price_val < (cex_val * 0.82):
        s_revs = 0
        s_joined = None
        if isinstance(seller_obj, dict):
            s_revs = int(seller_obj.get("seller_reviews_count") or seller_obj.get("reviews_count") or seller_obj.get("sales") or seller_obj.get("feedback_count") or 0)
            s_joined = seller_obj.get("seller_joined_days") or seller_obj.get("joined_days") or seller_obj.get("age_days")
            if s_joined is not None:
                try:
                    s_joined = float(s_joined)
                except Exception:
                    s_joined = None
        full_text = f"{clean_title} {clean_desc}".lower()
        is_new_seller = (s_revs == 0) or (s_joined is not None and s_joined < 30)
        has_suspicious_hook = ("precintad" in full_text) or ("no me va el chat" in full_text) or ("comunion" in full_text) or ("comunión" in full_text)
        if is_new_seller or has_suspicious_hook:
            hits.append({
                "code": "ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER",
                "category": "price_seller",
                "label": f"Anomalía severa: {price_val:.0f}€ vs Suelo CeX {cex_val:.0f}€ (<82%) con vendedor sin historial o precintada",
                "weight": 3
            })
            if price_val < (cex_val * 0.65) and s_revs == 0:
                should_hard_block = True

    # 7. Huella de imagen reutilizada (dHash)
    if raw_images and len(raw_images) > 0 and raw_images[0] and active_conn:
        img_h = calculate_dhash(raw_images[0])
        if img_h:
            is_reused, r_info = check_and_record_image_hash(active_conn, img_h, platform_str, item_id_str, radar_id_str)
            if is_reused and r_info:
                hits.append({
                    "code": "REUSED_IMAGE",
                    "category": "image",
                    "label": f"Foto idéntica ya vista en {r_info['platform']} (item {r_info['item_id']})",
                    "weight": 3
                })

    # 8. Señales de vendedor (P2-4 / Capa 2)
    seller_eval = evaluate_seller_risk(seller_obj, price=price_val, platform=platform_str)
    if seller_eval["hits"]:
        hits.extend(seller_eval["hits"])

    # 8.1. Consulta activa de seller_reputation para bloquear estafadores reincidentes
    seller_id = ""
    if seller_obj:
        if isinstance(seller_obj, dict):
            seller_id = str(seller_obj.get("id") or seller_obj.get("seller_id") or seller_obj.get("name") or "").strip()
        elif isinstance(seller_obj, (str, int)):
            seller_id = str(seller_obj).strip()

    if seller_id.lower() in GENERIC_SELLER_NAMES or len(seller_id) < 3:
        seller_id = ""

    historical_trust_bonus = 0
    if active_conn and seller_id:
        try:
            plat = str(platform_str).upper() if platform_str else ""
            c = active_conn.cursor()
            # Los flags caducan a los REPEAT_FLAG_TTL_DAYS (A-004). Tablas sin last_flag_at usan last_seen.
            fresh_expr = f"(COALESCE(last_flag_at, last_seen) >= datetime('now', '-{REPEAT_FLAG_TTL_DAYS} days'))"
            try:
                c.execute(f"SELECT 1, {fresh_expr} FROM seller_reputation LIMIT 0")
            except Exception:
                fresh_expr = f"(last_seen >= datetime('now', '-{REPEAT_FLAG_TTL_DAYS} days'))"
            base_q = f"SELECT total_seen, fraud_flags, avg_risk_score, {fresh_expr} FROM seller_reputation WHERE seller_id = ?"
            if plat:
                c.execute(base_q + " AND (platform = ? OR platform IS NULL OR platform = '')", (seller_id, plat))
            else:
                c.execute(base_q, (seller_id,))
            rep_row = c.fetchone()
            if not rep_row and plat:
                c.execute(base_q, (seller_id,))
                rep_row = c.fetchone()

            if rep_row:
                s_seen, s_flags, s_avg, s_fresh = rep_row
                s_seen = int(s_seen or 0)
                s_flags = int(s_flags or 0) if s_fresh else 0
                s_avg = float(s_avg or 0.0)
                # Un perfil que hoy tiene >= 2 valoraciones y rating >= 4 no se veta por su historial
                currently_trusted = bool(seller_eval.get("is_trusted"))

                if not currently_trusted and (s_flags >= 2 or (s_seen >= 3 and s_avg >= 4.5 and s_flags >= 1)):
                    hits.append({
                        "code": "SELLER_KNOWN_REPEAT_OFFENDER",
                        "category": "seller",
                        "label": f"Vendedor reincidente en fraudes (flags={s_flags}, avg={s_avg:.1f})",
                        "weight": 4
                    })
                elif s_seen >= 3 and s_flags == 0 and s_avg <= 1.0:
                    historical_trust_bonus = 1
        except Exception:
            pass

    # Consolidación del nivel de riesgo (0 a 5)
    total_weight = sum(h["weight"] for h in hits)

    # Bonificación para vendedores contrastados de alta reputación si no hay sospechas graves de estafa directa
    has_hard_scam = any(h["code"] in ("CONTACT_PHONE", "CONTACT_EXTERNAL", "PAYMENT_EXTERNAL", "SELLER_KNOWN_REPEAT_OFFENDER", "ANOMALOUS_BELOW_CEX_CASH_NEW_SELLER") for h in hits)
    if (seller_eval.get("is_trusted") or historical_trust_bonus > 0) and not has_hard_scam and total_weight > 0:
        total_weight = max(0, total_weight - 1)

    risk = min(5, total_weight)

    warning_banner = ""
    if risk == 1:
        reasons = "; ".join(h["label"] for h in hits)
        warning_banner = f"🟡 **AVISO DE PRECAUCIÓN:** {reasons}"
    elif risk in (2, 3):
        reasons = "; ".join(h["label"] for h in hits)
        warning_banner = f"⚠️ **ALERTA DE RIESGO MODERADO:** {reasons}"

    if active_conn and seller_id:
        if seller_id.lower() in GENERIC_SELLER_NAMES or len(seller_id) < 3:
            seller_id = ""
        else:
            try:
                sid = seller_id
                plat = str(platform_str).upper() if platform_str else ""
                c = active_conn.cursor()
                # A-004: solo un bloqueo con señal grave suma flag
                severe_hit = risk >= 4 and any(h.get("code") in SEVERE_FLAG_CODES for h in hits)
                add_flag = 1 if severe_hit else 0
                has_flag_col = True
                try:
                    c.execute(
                        f"""SELECT total_seen, fraud_flags, avg_risk_score,
                                   (COALESCE(last_flag_at, last_seen) >= datetime('now', '-{REPEAT_FLAG_TTL_DAYS} days'))
                            FROM seller_reputation WHERE seller_id = ?""",
                        (sid,)
                    )
                except Exception:
                    has_flag_col = False
                    c.execute("SELECT total_seen, fraud_flags, avg_risk_score, 1 FROM seller_reputation WHERE seller_id = ?", (sid,))
                row = c.fetchone()
                if row:
                    total_seen, fraud_flags, old_avg, flags_fresh = row
                    total_seen = int(total_seen or 0)
                    new_seen = total_seen + 1
                    kept_flags = int(fraud_flags or 0) if flags_fresh else 0  # flags caducados se reinician
                    new_flags = kept_flags + add_flag
                    new_avg = round(((float(old_avg or 0.0) * total_seen) + float(risk)) / new_seen, 2)
                    if has_flag_col:
                        c.execute(
                            """UPDATE seller_reputation
                               SET total_seen = ?, fraud_flags = ?, avg_risk_score = ?, last_seen = CURRENT_TIMESTAMP,
                                   last_flag_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_flag_at END
                               WHERE seller_id = ?""",
                            (new_seen, new_flags, new_avg, add_flag, sid)
                        )
                    else:
                        c.execute(
                            """UPDATE seller_reputation
                               SET total_seen = ?, fraud_flags = ?, avg_risk_score = ?, last_seen = CURRENT_TIMESTAMP
                               WHERE seller_id = ?""",
                            (new_seen, new_flags, new_avg, sid)
                        )
                else:
                    if has_flag_col:
                        c.execute(
                            """INSERT INTO seller_reputation (seller_id, platform, total_seen, fraud_flags, avg_risk_score, last_seen, last_flag_at)
                               VALUES (?, ?, 1, ?, ?, CURRENT_TIMESTAMP, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)""",
                            (sid, plat, add_flag, round(float(risk), 2), add_flag)
                        )
                    else:
                        c.execute(
                            """INSERT INTO seller_reputation (seller_id, platform, total_seen, fraud_flags, avg_risk_score, last_seen)
                               VALUES (?, ?, 1, ?, ?, CURRENT_TIMESTAMP)""",
                            (sid, plat, add_flag, round(float(risk), 2))
                        )
                active_conn.commit()
            except Exception:
                pass

    reasons_list = [h.get("code") or h.get("label") for h in hits]
    is_blocked = (risk >= 4) or should_hard_block

    return {
        "risk": max(4, risk) if should_hard_block else risk,
        "risk_score": max(4, risk) if should_hard_block else risk,
        "is_fraud": is_blocked,
        "should_block": is_blocked,
        "hits": hits,
        "reasons": reasons_list,
        "warning_banner": warning_banner
    }


def log_fraud_event(conn, platform, item_id, title, price, risk, hits):
    """Registra en SQLite un descarte por fraude para auditoría (P2-2)."""
    if not conn:
        return
    try:
        c = conn.cursor()
        c.execute(
            """INSERT INTO fraud_log (platform, item_id, title, price, risk, reasons_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (platform, str(item_id), title or "", float(price or 0), int(risk), json.dumps(hits, ensure_ascii=False))
        )
        conn.commit()
    except Exception as e:
        print(f"[WARN] Error registrando en fraud_log: {e}")
