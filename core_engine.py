# -*- coding: utf-8 -*-
"""
Motor Central Unificado (Core Engine v4.3 - Multimodal Vision, Offer Sniping, 2nd Push Verdict & Direct Checkout)
Compartido por todos los snipers (Wallapop, Vinted, Multi-Market) y el bot de Discord.
"""

import os
import io
import re
import html
import json
import time
import threading
import random
import math
import struct
import base64
import shutil
import hashlib
import zlib
import sqlite3
import urllib.parse
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import requests as std_requests
from requests.adapters import HTTPAdapter
from curl_cffi import requests, CurlMime
from PIL import Image, ImageEnhance, ImageOps

HTTP_SESSION = std_requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
HTTP_SESSION.mount("https://", _adapter)
HTTP_SESSION.mount("http://", _adapter)

from fraud_signals import (
    evaluate_fraud_risk, log_fraud_event, detect_phone_number,
    calculate_dhash, check_and_record_image_hash,
    enrich_seller_profile, evaluate_seller_risk, migrate_seller_reputation,
    EXTERNAL_PAYMENT_TOKENS, CONTACT_EXTERNAL_PATTERNS,
    URGENCY_SCAM_TOKENS, DEFECT_TOKENS, ACCESSORY_TITLE_PREFIXES
)
from antiban import default_parser_tracker, default_rate_limiter


class WafBlockedError(Exception):
    """Lanzada cuando un fetcher recibe un bloqueo WAF/DataDome/PerimeterX (405, 403, CAPTCHA)."""
    pass

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
ALERTS_CHANNEL_ID = os.getenv("ALERTS_CHANNEL_ID", "1564559740434911366").strip()
ALERT_USER_ID = os.getenv("ALERT_USER_ID", "").strip()
NTFY_TOPIC_URL = os.getenv("NTFY_TOPIC_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "gemini-3.5-flash-lite").strip()
AI_ANALYST_MODEL = os.getenv("AI_ANALYST_MODEL", AI_MODEL).strip()
AI_PRESCREEN_MODEL = os.getenv("AI_PRESCREEN_MODEL", "gemini-3.5-flash-lite").strip()

if GEMINI_API_KEY and len(GEMINI_API_KEY) < 8:
    print("[WARN] GEMINI_API_KEY sospechosamente corta. Verifique el archivo .env.")
PROXY_URL = os.getenv("PROXY_URL", "").strip()

DB_PATH = os.getenv("DB_PATH", "/app/data/sniper.db" if os.path.exists("/app") else "./data/sniper.db")
CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config.json" if os.path.exists("/app/config.json") else "./config.json")
KITS_DIR = os.getenv("KITS_DIR", "/app/kits" if os.path.exists("/app") else "./kits")

AI_DAILY_BUDGET_USD = float(os.getenv("AI_DAILY_BUDGET_USD", "0.10"))
AI_PRESCREEN_SHADOW_MODE = os.getenv("AI_PRESCREEN_SHADOW_MODE", "false").strip().lower() in ("true", "1", "yes")

_budget_notified_80 = False
_budget_notified_100 = False
_budget_notified_120 = False
_last_budget_check_day = ""

LOCAL_KEYWORDS = [
    "sevilla", "mairena", "alcala", "alcalá", "viso", "carmona",
    "dos hermanas", "utrera", "camas", "tomares", "bormujos", "rinconada",
    "san juan de aznalfarache", "coria del rio", "gines", "castilleja"
]


class LandedCost(float):
    fees: float = 0.0

    def __new__(cls, landed_cost, fees=0.0):
        obj = super().__new__(cls, round(float(landed_cost), 2))
        obj.fees = round(float(fees), 2)
        return obj

    def __iter__(self):
        yield float(self)
        yield self.fees


def estimate_landed_cost(price, *args, is_local=False, is_foreign=False, is_italy=False, shipping_tier="standard", **kwargs):
    """
    Calcula el coste puesto en casa (landed cost) con Wallapop Envíos y comisión de protección.
    Soporta firmas flexibles:
      - estimate_landed_cost(price, is_local, is_foreign, shipping_tier="standard")
      - estimate_landed_cost(price, is_italy, shipping_tier="standard")
    Devuelve LandedCost(float) que es numéricamente igual al coste total y a la vez iterable (landed_cost, fees).
    """
    if len(args) == 1:
        if isinstance(args[0], str):
            shipping_tier = args[0]
        else:
            is_foreign = bool(args[0])
            is_italy = is_foreign
    elif len(args) == 2:
        if isinstance(args[1], str):
            is_foreign = bool(args[0])
            is_italy = is_foreign
            shipping_tier = args[1]
        else:
            is_local = bool(args[0])
            is_foreign = bool(args[1])
            is_italy = is_foreign
    elif len(args) >= 3:
        is_local = bool(args[0])
        is_foreign = bool(args[1])
        is_italy = is_foreign
        shipping_tier = str(args[2])

    if is_local:
        return LandedCost(price, 0.0)

    is_foreign_dest = is_foreign or is_italy
    tier = str(shipping_tier or "standard").lower()

    if tier == "heavy":
        shipping = 9.95 if is_foreign_dest else 6.95
    else:
        shipping = 6.95 if is_foreign_dest else 3.49

    insurance = max(1.50, float(price) * 0.05)
    total_fees = round(shipping + insurance, 2)
    total_cost = round(float(price) + total_fees, 2)

    return LandedCost(total_cost, total_fees)

PLATFORM_COLORS = {
    "WALLAPOP": 0x13C1AC,
    "VINTED": 0x09B1BA,
    "MILANUNCIOS": 0x2ECC71,
    "CASHCONVERTERS": 0xE67E22,
    "EBAY": 0x3498DB,
    "TODOCOLECCION": 0x9B59B6,
    "PERSONAL": 0xF1C40F,
    "OFFER_OPPORTUNITY": 0x9B59B6
}


# ==================== HEALTHCHECK & HEARTBEAT ====================

def touch_heartbeat(path="/tmp/heartbeat"):
    """Actualiza el fichero de heartbeat para que el healthcheck de Docker sepa que el proceso está vivo."""
    try:
        with open(path, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def sleep_with_heartbeat(seconds, heartbeat_path="/tmp/heartbeat", interval=15):
    """Pausa la ejecución manteniendo actualizado el heartbeat periódicamente."""
    start = time.time()
    while time.time() - start < seconds:
        touch_heartbeat(heartbeat_path)
        remaining = seconds - (time.time() - start)
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    touch_heartbeat(heartbeat_path)


# ==================== BASE DE DATOS Y CACHÉ ====================

def get_db_connection(db_path=None, timeout=15.0):
    """Conexión SQLite ultraligera en modo WAL con timeout y busy_timeout configurados."""
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(target_path, timeout=float(timeout))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)};")
    return conn


def get_db(db_path=None, timeout=30.0):
    return get_db_connection(db_path=db_path, timeout=timeout)


def run_db_maintenance(conn=None):
    """Ejecuta purga de registros antiguos sin bloquear el flujo principal de consultas."""
    close_after = False
    if conn is None:
        conn = get_db()
        close_after = True
    c = conn.cursor()
    try:
        c.execute("DELETE FROM market_seen_items WHERE seen_at < datetime('now', '-45 days')")
        c.execute("DELETE FROM seen_items WHERE seen_at < datetime('now', '-45 days')")
        c.execute("DELETE FROM vinted_seen_items WHERE seen_at < datetime('now', '-45 days')")
        c.execute("DELETE FROM cex_cache WHERE updated_at < datetime('now', '-3 days')")
        c.execute("DELETE FROM ai_eval_cache WHERE evaluated_at < datetime('now', '-7 days')")
        c.execute("DELETE FROM ai_prescreen_cache WHERE evaluated_at < datetime('now', '-7 days')")
        c.execute("DELETE FROM ai_prompt_cache WHERE created_at < datetime('now', '-7 days')")
        c.execute("DELETE FROM ai_cost_log WHERE created_at < datetime('now', '-60 days')")
        c.execute("DELETE FROM seen_image_hashes WHERE seen_at < datetime('now', '-30 days')")
        c.execute("DELETE FROM item_embeddings WHERE created_at < datetime('now', '-30 days')")
        c.execute("DELETE FROM fraud_log WHERE created_at < datetime('now', '-60 days')")
        try:
            c.execute("DELETE FROM price_history WHERE recorded_at < datetime('now', '-90 days')")
        except Exception:
            pass
        conn.commit()
    except Exception as e:
        print(f"[WARN] Mantenimiento de BD: {e}")
    finally:
        if close_after:
            conn.close()


def init_db(conn=None):
    if conn is None:
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        os.makedirs(KITS_DIR, exist_ok=True)
        conn = get_db()
    else:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_items (
            item_id TEXT PRIMARY KEY,
            title TEXT,
            price REAL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS vinted_seen_items (
            item_id TEXT PRIMARY KEY,
            title TEXT,
            price REAL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS market_seen_items (
            platform TEXT,
            item_id TEXT,
            title TEXT,
            price REAL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (platform, item_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cex_cache (
            search_query TEXT PRIMARY KEY,
            box_name TEXT,
            cash_price REAL,
            voucher_price REAL,
            sell_price REAL,
            box_id TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cex_catalog_mirror (
            box_id TEXT PRIMARY KEY,
            box_name TEXT,
            category_name TEXT,
            cash_price REAL,
            voucher_price REAL,
            sell_price REAL,
            last_synced DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_eval_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT,
            evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_prescreen_cache (
            cache_key TEXT PRIMARY KEY,
            decision TEXT,
            reject_reason TEXT,
            confidence TEXT,
            evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_prompt_cache (
            prompt_hash TEXT PRIMARY KEY,
            result_json TEXT,
            model TEXT,
            call_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            radar_id TEXT,
            model TEXT,
            call_type TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER,
            usd REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_image_hashes (
            hash TEXT,
            platform TEXT,
            item_id TEXT,
            radar_id TEXT,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE IF NOT EXISTS item_embeddings (
            item_key TEXT PRIMARY KEY,
            radar_id TEXT,
            embedding BLOB,
            model TEXT,
            verdict_json TEXT DEFAULT '',
            title TEXT DEFAULT '',
            price REAL DEFAULT 0.0,
            seller_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            deal_id TEXT PRIMARY KEY,
            platform TEXT,
            item_id TEXT,
            radar_id TEXT,
            radar_name TEXT,
            mode TEXT DEFAULT 'resale',
            title TEXT,
            description TEXT,
            price REAL,
            landed_cost REAL,
            fees REAL,
            optimal_price REAL,
            net_profit REAL,
            deal_score INTEGER,
            identified_model TEXT,
            cex_search_query TEXT,
            cex_box_name TEXT,
            cex_cash REAL,
            cex_voucher REAL,
            cex_sell REAL,
            item_url TEXT,
            checkout_url TEXT,
            image_url TEXT,
            kit_folder TEXT,
            seller_city TEXT,
            is_local INTEGER DEFAULT 0,
            fast_msg TEXT,
            haggle_msg TEXT,
            seo_title TEXT,
            seo_description TEXT,
            value_add_tip TEXT,
            image_urls_json TEXT DEFAULT '[]',
            visual_notes TEXT DEFAULT '',
            deal_type TEXT DEFAULT 'direct_chollo',
            recommended_offer REAL DEFAULT 0,
            status TEXT DEFAULT 'alerted',
            bought_price REAL DEFAULT 0,
            sold_price REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        cols = [col[1] for col in c.execute("PRAGMA table_info(cex_cache)").fetchall()]
        if "box_id" not in cols:
            c.execute("ALTER TABLE cex_cache ADD COLUMN box_id TEXT DEFAULT '';")

        deal_cols = [col[1] for col in c.execute("PRAGMA table_info(deals)").fetchall()]
        if "image_urls_json" not in deal_cols:
            c.execute("ALTER TABLE deals ADD COLUMN image_urls_json TEXT DEFAULT '[]';")
        if "visual_notes" not in deal_cols:
            c.execute("ALTER TABLE deals ADD COLUMN visual_notes TEXT DEFAULT '';")
        if "deal_type" not in deal_cols:
            c.execute("ALTER TABLE deals ADD COLUMN deal_type TEXT DEFAULT 'direct_chollo';")
        if "recommended_offer" not in deal_cols:
            c.execute("ALTER TABLE deals ADD COLUMN recommended_offer REAL DEFAULT 0;")
        # Cartera: fechas, notas, comisiones de venta y último cambio de estado
        for col_name, col_def in (
            ("bought_at", "DATETIME"), ("sold_at", "DATETIME"), ("notes", "TEXT DEFAULT ''"),
            ("sale_fees", "REAL DEFAULT 0"), ("status_changed_at", "DATETIME"),
        ):
            if col_name not in deal_cols:
                try:
                    c.execute(f"ALTER TABLE deals ADD COLUMN {col_name} {col_def};")
                except sqlite3.OperationalError:
                    pass  # Otro contenedor la añadió a la vez
        ensure_deal_events_table(conn)

        embed_cols = [col[1] for col in c.execute("PRAGMA table_info(item_embeddings)").fetchall()]
        if "seller_id" not in embed_cols:
            c.execute("ALTER TABLE item_embeddings ADD COLUMN seller_id TEXT DEFAULT '';")

        run_db_maintenance(conn)

        c.execute("CREATE INDEX IF NOT EXISTS idx_market_seen_at ON market_seen_items(seen_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_cost_created ON ai_cost_log(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_cost_radar ON ai_cost_log(radar_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_prescreen_eval_at ON ai_prescreen_cache(evaluated_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_prompt_cache_created ON ai_prompt_cache(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_seen_image_hashes ON seen_image_hashes(hash)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_seen_image_hashes_time ON seen_image_hashes(seen_at)")
        try:
            c.execute("""
                DELETE FROM seen_image_hashes
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM seen_image_hashes
                    GROUP BY item_id, hash
                )
            """)
        except Exception:
            pass
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_img_item_hash ON seen_image_hashes(item_id, hash)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_seen_image_hashes_radar_seen ON seen_image_hashes(radar_id, seen_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fraud_log_created ON fraud_log(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_item_embeddings_radar ON item_embeddings(radar_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_item_embeddings_time ON item_embeddings(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_radar_price_date ON item_embeddings(radar_id, price, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cex_mirror_synced ON cex_catalog_mirror(last_synced)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cex_mirror_box_name ON cex_catalog_mirror(box_name)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                radar_name TEXT,
                platform TEXT,
                price REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_price_history_radar ON price_history(radar_name, recorded_at)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS seller_reputation (
                seller_id TEXT PRIMARY KEY,
                platform TEXT,
                total_seen INTEGER DEFAULT 1,
                fraud_flags INTEGER DEFAULT 0,
                avg_risk_score REAL DEFAULT 0.0,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        migrate_seller_reputation(conn)
    except Exception as e:
        print(f"[WARN] Mantenimiento de BD: {e}")

    conn.commit()

    # Pre-calentar espejo CeX en segundo plano con control de lockfile para evitar tormenta x4
    try:
        lock_dir = os.path.dirname(DB_PATH) or "."
        lock_path = os.path.join(lock_dir, "cex_sync.lock")
        should_sync = True
        if os.path.exists(lock_path):
            try:
                mtime = os.path.getmtime(lock_path)
                if time.time() - mtime < 600:
                    should_sync = False
            except OSError:
                pass

        if should_sync:
            try:
                with open(lock_path, "w") as f:
                    f.write(str(time.time()))
            except Exception:
                pass

            def _bg_sync_cex_mirror():
                bg_conn = None
                try:
                    bg_conn = get_db()
                    sync_cex_catalog_mirror(bg_conn)
                except Exception:
                    pass
                finally:
                    if bg_conn:
                        try:
                            bg_conn.close()
                        except Exception:
                            pass
                    try:
                        if os.path.exists(lock_path):
                            os.remove(lock_path)
                    except Exception:
                        pass

            threading.Thread(target=_bg_sync_cex_mirror, daemon=True).start()
    except Exception:
        pass

    return conn


# ==================== CONTABILIDAD DE COSTE IA & PRESUPUESTO ====================

def calculate_ai_cost_usd(model, tokens_in, tokens_out, thoughts_tokens=0):
    model_str = (model or "").lower()
    t_in = max(0, int(tokens_in or 0))
    t_out = max(0, int(tokens_out or 0))
    t_thoughts = max(0, int(thoughts_tokens or 0))
    billable_out = t_out + t_thoughts

    if "embedding" in model_str:
        in_cost = t_in * (0.02 / 1_000_000)
        out_cost = 0.0
    elif "lite" in model_str:
        in_cost = t_in * (0.10 / 1_000_000)
        out_cost = billable_out * (0.40 / 1_000_000)
    elif "gpt-4o-mini" in model_str:
        in_cost = t_in * (0.15 / 1_000_000)
        out_cost = billable_out * (0.60 / 1_000_000)
    elif "flash" in model_str:
        in_cost = t_in * (0.30 / 1_000_000)
        out_cost = billable_out * (2.50 / 1_000_000)
    else:
        in_cost = t_in * (0.15 / 1_000_000)
        out_cost = billable_out * (0.60 / 1_000_000)
    return round(in_cost + out_cost, 6)


def log_ai_cost(conn, platform, radar_id, model, call_type, tokens_in, tokens_out, thoughts_tokens=0):
    if not conn:
        return
    try:
        usd = calculate_ai_cost_usd(model, tokens_in, tokens_out, thoughts_tokens)
        c = conn.cursor()
        total_out = int(tokens_out or 0) + int(thoughts_tokens or 0)
        c.execute(
            """INSERT INTO ai_cost_log (platform, radar_id, model, call_type, tokens_in, tokens_out, usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (platform, radar_id or "", model or "", call_type or "unknown", int(tokens_in or 0), total_out, usd)
        )
        conn.commit()
    except Exception as e:
        print(f"[WARN] Error registrando coste IA: {e}")


def get_today_ai_cost(conn):
    if not conn:
        return 0.0
    try:
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(usd), 0.0) FROM ai_cost_log WHERE created_at >= date('now', 'start of day')")
        row = c.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        return 0.0


AI_MAX_CONCURRENCY = int(os.getenv("AI_MAX_CONCURRENCY", "3"))
_AI_GATEWAY_SEMAPHORE = threading.Semaphore(AI_MAX_CONCURRENCY)


class BudgetExceededError(RuntimeError):
    """Lanzada cuando el gasto acumulado diario supera el hard limit de presupuesto."""
    pass


def repair_and_parse_json(raw_text: str, schema_defaults: dict = None):
    """
    Limpiador y reparador determinista de JSON local (CORRECION_FALLOS.md 3.2).
    1. Recorta bloques Markdown (```json ... ```) y texto conversacional exterior.
    2. Convierte literales Python (True, False, None) a JSON (true, false, null).
    3. Repara comas finales en objetos y listas (trailing commas).
    4. Repara saltos de línea y caracteres de control no escapados dentro de cadenas.
    5. Soporta objetos envueltos en listas (e.g. [ {...} ]).
    6. Valida y rellena valores por defecto según schema_defaults (cero caídas por campos faltantes).
    """
    if not raw_text or not isinstance(raw_text, str):
        return dict(schema_defaults) if schema_defaults else None

    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    start_brace = text.find("{")
    end_brace = text.rfind("}")
    start_bracket = text.find("[")
    end_bracket = text.rfind("]")

    target_text = text
    if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
        if start_bracket == -1 or start_brace < start_bracket:
            target_text = text[start_brace:end_brace + 1]
        elif start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
            target_text = text[start_bracket:end_bracket + 1]
    elif start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
        target_text = text[start_bracket:end_bracket + 1]

    parsed = None
    try:
        parsed = json.loads(target_text)
    except Exception:
        pass

    if parsed is None:
        cand = target_text
        cand = re.sub(r'\bTrue\b', 'true', cand)
        cand = re.sub(r'\bFalse\b', 'false', cand)
        cand = re.sub(r'\bNone\b', 'null', cand)
        cand = re.sub(r',\s*([}\]])', r'\1', cand)
        try:
            parsed = json.loads(cand)
        except Exception:
            try:
                cand_lines = re.sub(r'(?<!\\)\n', r'\\n', cand)
                parsed = json.loads(cand_lines)
            except Exception:
                try:
                    cand_quotes = re.sub(r"(?<=[{\[,:\s])'([^']+?)'(?=[}\],:\s])", r'"\1"', cand)
                    parsed = json.loads(cand_quotes)
                except Exception:
                    pass

    if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
        parsed = parsed[0]

    if not isinstance(parsed, dict):
        if schema_defaults:
            return dict(schema_defaults)
        return None

    if schema_defaults:
        result = dict(schema_defaults)
        for k, default_val in schema_defaults.items():
            if k in parsed and parsed[k] is not None:
                if isinstance(default_val, (int, float)) and not isinstance(default_val, bool):
                    try:
                        result[k] = type(default_val)(parsed[k])
                    except (ValueError, TypeError):
                        result[k] = default_val
                elif isinstance(default_val, bool):
                    if isinstance(parsed[k], bool):
                        result[k] = parsed[k]
                    elif isinstance(parsed[k], str):
                        result[k] = parsed[k].lower() in ("true", "1", "yes", "si", "sí")
                    else:
                        result[k] = bool(parsed[k])
                elif isinstance(default_val, str):
                    result[k] = str(parsed[k])
                elif isinstance(default_val, list):
                    if isinstance(parsed[k], list):
                        result[k] = parsed[k]
                    else:
                        result[k] = [parsed[k]]
                else:
                    result[k] = parsed[k]
            else:
                result[k] = default_val
        for k, v in parsed.items():
            if k not in result:
                result[k] = v
        return result

    return parsed


def ai_gateway_call(
    prompt: str,
    images_b64: list = None,
    candidate_models: list = None,
    openrouter_models: list = None,
    max_tokens: int = 300,
    temperature: float = 0.1,
    conn=None,
    platform: str = "SYSTEM",
    radar_id: str = "",
    call_type: str = "general",
    timeout: int = 15,
    enforce_budget: bool = True,
    schema_defaults: dict = None
):
    """
    AI Gateway Centralizado con Cortafuegos de Presupuesto (CORRECION_FALLOS.md 3.1 & 2.4 & 3.2):
    1. Hard Limit Diario: Valida el gasto diario acumulado contra AI_DAILY_BUDGET_USD (0.10$). Si se supera,
       lanza BudgetExceededError bloqueando cualquier llamada adicional de inmediato.
    2. Control de Concurrencia y Abort Timeout: Limita llamadas concurrentes a AI_MAX_CONCURRENCY con timeout estricto.
    3. Parámetro max_tokens estricto: Todo llamado fuerza un maxOutputTokens acotado (150-450) para evitar
       respuestas kilométricas de alto coste.
    4. Telemetría centralizada: Registra automáticamente cada llamada en ai_cost_log con desglose de tokens y coste.
    5. Reparación local determinista: Parsea las respuestas mediante repair_and_parse_json para 0 reintentos por API.
    6. Caché Local SHA-256 (Tier 2): Si un prompt idéntico ya fue evaluado, devuelve el resultado cacheado con 0 coste y 0 tokens.
    7. Schema Validation con Fallback: Asegura esquema completo con valores por defecto ante respuestas parciales.
    """
    if not prompt:
        return dict(schema_defaults) if schema_defaults else None

    close_conn_after = False
    active_conn = conn
    if active_conn is None:
        try:
            active_conn = get_db()
            close_conn_after = True
        except Exception:
            active_conn = None

    try:
        # 1. Caché Local SHA-256 (Tier 2 - Nivel Prompt Exacto)
        hasher = hashlib.sha256()
        hasher.update((call_type or "general").encode("utf-8"))
        hasher.update(b"::")
        hasher.update(prompt.encode("utf-8"))
        if images_b64:
            for img in images_b64:
                if isinstance(img, str):
                    hasher.update(img[:128].encode("ascii", errors="ignore"))
                    hasher.update(img[-128:].encode("ascii", errors="ignore"))
                    hasher.update(str(len(img)).encode("ascii"))
        prompt_hash = hasher.hexdigest()

        if active_conn:
            try:
                c = active_conn.cursor()
                c.execute("SELECT result_json FROM ai_prompt_cache WHERE prompt_hash = ?", (prompt_hash,))
                cached_row = c.fetchone()
                if cached_row and cached_row[0]:
                    cached_data = repair_and_parse_json(cached_row[0], schema_defaults=schema_defaults)
                    if cached_data is not None:
                        return cached_data
            except Exception:
                pass

        # 2. Cortafuegos de Presupuesto Diario (Hard Limit)
        if enforce_budget and active_conn:
            today_cost = get_today_ai_cost(active_conn)
            if today_cost >= AI_DAILY_BUDGET_USD:
                err_msg = (
                    f"🛑 CORTAFUEGOS IA ACTIVO: Gasto acumulado hoy ({today_cost:.4f}$) "
                    f"ha alcanzado o superado el Hard Limit diario ({AI_DAILY_BUDGET_USD:.4f}$)."
                )
                print(f"[AI_GATEWAY] {err_msg}")
                raise BudgetExceededError(err_msg)

        # 3. Control de Concurrencia y Abort Timeout (Punto 6)
        slot_acquired = _AI_GATEWAY_SEMAPHORE.acquire(timeout=float(timeout or 15))
        if not slot_acquired:
            print(f"[WARN AI_GATEWAY] Concurrencia máxima ({AI_MAX_CONCURRENCY}) ocupada. Timeout esperando slot en {call_type}.")
            return dict(schema_defaults) if schema_defaults else None

        try:
            # 4. Control de max_tokens duro
            max_tokens = int(max_tokens or 300)
            if max_tokens <= 0:
                max_tokens = 300
            if call_type == "prescreen":
                max_tokens = min(max_tokens, 200)
            else:
                max_tokens = min(max_tokens, 500)

            # 5. Modelos por defecto
            if not candidate_models:
                candidate_models = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
                if openrouter_models is None:
                    openrouter_models = ["google/gemini-2.5-flash-lite", "openai/gpt-4o-mini"]
            elif openrouter_models is None:
                openrouter_models = []

            parsed_result = None
            used_model = None

            # 6. Intento con Google Gemini Direct API
            if GEMINI_API_KEY:
                parts = [{"text": prompt}]
                if images_b64:
                    for b64_img in images_b64:
                        parts.append({
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": b64_img
                            }
                        })

                for model in candidate_models:
                    if "3.8" in model:
                        continue
                    payload = {
                        "contents": [{"parts": parts}],
                        "generationConfig": {
                            "responseMimeType": "application/json",
                            "temperature": temperature,
                            "maxOutputTokens": max_tokens
                        }
                    }
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
                    try:
                        resp = HTTP_SESSION.post(url, json=payload, timeout=timeout)
                        if resp.status_code == 200:
                            resp_json = resp.json()
                            usage = resp_json.get("usageMetadata", {})
                            p_tokens = usage.get("promptTokenCount", 0)
                            c_tokens = usage.get("candidatesTokenCount", 0)
                            thoughts_tokens = usage.get("thoughtsTokenCount", 0)
                            log_ai_cost(active_conn, platform, radar_id, model, call_type, p_tokens, c_tokens, thoughts_tokens)

                            candidates = resp_json.get("candidates", [])
                            if candidates:
                                for p in candidates[0].get("content", {}).get("parts", []):
                                    if "text" in p and not p.get("thought", False):
                                        raw_parsed = repair_and_parse_json(p["text"], schema_defaults=None)
                                        if raw_parsed is not None:
                                            parsed_result = repair_and_parse_json(p["text"], schema_defaults=schema_defaults)
                                            used_model = model
                                            break
                            if parsed_result is not None:
                                break
                        elif resp.status_code == 404:
                            continue
                        else:
                            print(f"[WARN AI_GATEWAY] HTTP {resp.status_code} en {model}: {resp.text[:120]}")
                    except Exception as e:
                        print(f"[WARN AI_GATEWAY] Excepción Gemini ({model}): {e}")
                        continue

            # 7. Intento con OpenRouter Fallback
            if parsed_result is None and OPENROUTER_API_KEY:
                for fallback_model in openrouter_models:
                    user_content = [{"type": "text", "text": prompt}]
                    if images_b64:
                        for b64_img in images_b64:
                            user_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                            })
                    payload = {
                        "model": fallback_model,
                        "messages": [{"role": "user", "content": user_content}],
                        "temperature": temperature,
                        "response_format": {"type": "json_object"},
                        "max_tokens": max_tokens
                    }
                    headers = {
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://homelab.local",
                        "X-Title": "Boti Sniper Gateway"
                    }
                    try:
                        resp = HTTP_SESSION.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=timeout)
                        if resp.status_code == 200:
                            resp_json = resp.json()
                            usage = resp_json.get("usage", {})
                            p_tokens = usage.get("prompt_tokens", 0)
                            c_tokens = usage.get("completion_tokens", 0)
                            log_ai_cost(active_conn, platform, radar_id, fallback_model, call_type, p_tokens, c_tokens)

                            raw_text = resp_json["choices"][0]["message"].get("content", "")
                            raw_parsed = repair_and_parse_json(raw_text, schema_defaults=None)
                            if raw_parsed is not None:
                                parsed_result = repair_and_parse_json(raw_text, schema_defaults=schema_defaults)
                                used_model = fallback_model
                                break
                    except Exception as e:
                        print(f"[WARN AI_GATEWAY] Excepción OpenRouter ({fallback_model}): {e}")
                        continue

            # 8. Si obtuvimos resultado válido de una API real, guardar en ai_prompt_cache
            if parsed_result is not None and used_model is not None and active_conn:
                try:
                    c = active_conn.cursor()
                    c.execute(
                        """INSERT OR REPLACE INTO ai_prompt_cache (prompt_hash, result_json, model, call_type, created_at)
                           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (prompt_hash, json.dumps(parsed_result, ensure_ascii=False), used_model, call_type)
                    )
                    active_conn.commit()
                except Exception:
                    pass

            if parsed_result is None and schema_defaults is not None:
                return dict(schema_defaults)

            return parsed_result
        finally:
            _AI_GATEWAY_SEMAPHORE.release()
    finally:
        if close_conn_after and active_conn:
            try:
                active_conn.close()
            except Exception:
                pass


def check_ai_budget_status(conn, health_monitor=None):
    global _budget_notified_80, _budget_notified_100, _budget_notified_120, _last_budget_check_day
    today_str = datetime.now().strftime("%Y-%m-%d")
    if _last_budget_check_day != today_str:
        _last_budget_check_day = today_str
        _budget_notified_80 = False
        _budget_notified_100 = False
        _budget_notified_120 = False

    cost_today = get_today_ai_cost(conn)
    ratio = cost_today / max(0.01, AI_DAILY_BUDGET_USD)

    if ratio >= 1.0:
        if not _budget_notified_100:
            _budget_notified_100 = True
            msg = f"🚨 **CORTAFUEGOS IA ACTIVO (100% Presupuesto)**: Gastado hoy {cost_today:.4f}$ / {AI_DAILY_BUDGET_USD:.4f}$. Visión pausada preventivamente."
            print(f"[BUDGET ALERTA] {msg}")
            if health_monitor and hasattr(health_monitor, "send_health_embed"):
                health_monitor.send_health_embed("🚨 Cortafuegos Presupuesto IA", msg, 0xE74C3C)
        return "PAUSED", ratio
    elif ratio >= 0.80:
        if not _budget_notified_80:
            _budget_notified_80 = True
            msg = f"🟡 **PRESUPUESTO IA AL 80%**: Gastado hoy {cost_today:.4f}$ / {AI_DAILY_BUDGET_USD:.4f}$."
            print(f"[BUDGET AVISO] {msg}")
            if health_monitor and hasattr(health_monitor, "send_health_embed"):
                health_monitor.send_health_embed("🟡 Aviso de Presupuesto IA", msg, 0xF1C40F)
        return "NORMAL", ratio
    return "NORMAL", ratio


def is_ai_budget_paused(conn):
    """True mientras el cortafuegos diario de IA está activo. Los escáneres lo usan
    para aplazar anuncios en vez de rechazarlos (A-001)."""
    try:
        status, _ = check_ai_budget_status(conn)
        return status == "PAUSED"
    except Exception:
        return False


def _budget_pause_verdict(title):
    """Veredicto temporal de pausa por presupuesto. Lleva _api_error para que nadie lo
    cachee, lo guarde como embedding ni marque el anuncio como visto (A-001)."""
    return {
        "_api_error": True,
        "_budget_paused": True,
        "is_valid_deal": False,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "visual_confidence": "baja",
        "identified_model": title,
        "cex_search_query": title,
        "bundled_games_queries": [],
        "visual_notes": "⚠️ Presupuesto IA diario alcanzado. Pausado preventivamente.",
        "reject_reason": "Presupuesto IA diario alcanzado (pausa preventiva)",
        "estimated_resale_price": 0,
        "value_add_tip": "Presupuesto IA en pausa",
        "fast_buy_message": ""
    }


# ==================== GENERACIÓN DE CLAVE DE CACHÉ ROBUSTA ====================

STOP_WORDS_CACHE = {
    "de", "la", "el", "en", "y", "a", "los", "se", "del", "las", "un", "por", "con", "no", "una",
    "su", "para", "es", "al", "lo", "como", "mas", "más", "pero", "sus", "le", "ya", "o", "fue",
    "este", "ha", "si", "porque", "esta", "son", "entre", "cuando", "muy", "sin", "sobre",
    "ser", "tiene", "tambien", "también", "me", "hasta", "hay", "donde", "quien", "desde", "todo",
    "nos", "durante", "todos", "uno", "les", "ni", "contra", "otros", "ese", "eso", "ante", "ellos",
    "e", "esto", "mi", "antes", "algunos", "que", "qué"
}

def normalize_text_for_cache(text):
    if not text:
        return ""
    t = html.unescape(text).lower()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    tokens = [w for w in t.split() if len(w) > 1 and w not in STOP_WORDS_CACHE]
    return " ".join(tokens)


def build_robust_cache_key(radar_id, title, desc, price, img_sig=None, prefix="v44"):
    price_bucket = int(round(float(price or 0) / 5.0) * 5)
    norm_title = normalize_text_for_cache(title)[:80]
    norm_desc = normalize_text_for_cache(desc)[:120]
    sig = img_sig or "nosig"
    seed = f"{prefix}_{radar_id}_{norm_title}_{price_bucket}_{norm_desc}_{sig}"
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()



def check_item_seen_or_dropped(conn, platform, item_id, current_price, min_drop_pct=15, min_drop_eur=10):
    plat = platform.upper()
    sid = str(item_id)
    c = conn.cursor()
    c.execute("SELECT price FROM market_seen_items WHERE platform = ? AND item_id = ?", (plat, sid))
    row = c.fetchone()

    if row is None:
        return False, None

    old_price = float(row[0] or 0)
    cur_price = float(current_price or 0)
    if old_price > 0 and cur_price > 0 and cur_price < old_price:
        diff = old_price - cur_price
        pct = (diff / old_price) * 100.0
        if diff >= min_drop_eur and pct >= min_drop_pct:
            return False, {
                "old_price": old_price,
                "new_price": cur_price,
                "drop_eur": diff,
                "drop_pct": round(pct)
            }

    return True, None


# ==================== DEDUPLICACIÓN SEMÁNTICA (EMBEDDINGS) ====================

def pack_embedding(vec):
    """Empaqueta una lista/tupla de floats en BLOB binario (float32)."""
    if not vec:
        return b""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob):
    """Desempaqueta un BLOB binario en tupla de floats."""
    if not blob or len(blob) % 4 != 0:
        return ()
    return struct.unpack(f"{len(blob) // 4}f", blob)


def cosine_similarity(v1, v2):
    """
    Calcula similitud coseno entre dos vectores en Python puro (sin dependencias externas ni numpy).
    Devuelve float entre -1.0 y 1.0 (0.0 si dimensiones difieren o norma es 0).
    """
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        norm1 += a * a
        norm2 += b * b
    if norm1 <= 0.0 or norm2 <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm1) * math.sqrt(norm2))


_EMBED_STOPWORDS = {
    "de", "la", "el", "en", "y", "a", "los", "las", "con", "por", "para", "un", "una",
    "unos", "unas", "del", "al", "su", "sus", "se", "es", "di", "da", "in", "il", "lo",
    "per", "the", "and", "or", "in", "with", "of", "to", "for", "on", "at", "by", "is"
}

_EMBED_KEY_TOKENS = {
    "switch", "oled", "lite", "playstation", "ps5", "ps4", "ps3", "xbox", "series",
    "nintendo", "ds", "gba", "gameboy", "vita", "deck", "steam", "iphone", "ipad",
    "macbook", "air", "pro", "mini", "pokemon", "pokémon", "zelda", "mario", "dualsense",
    "dualshock", "joycon", "disco", "digital", "slim", "rtx", "gtx", "gpu", "ssd", "nvme"
}


def generate_text_embedding(text, *args, **kwargs):
    """
    Generador determinista local de embeddings semánticos (TASK-02).
    0 llamadas HTTP, 0€ coste, <0.2ms de latencia.
    Devuelve: list[float] de exactamente 64 dimensiones L2-normalizadas.
    """
    if not text or len(str(text).strip()) < 2:
        return None

    clean_text = html.unescape(str(text)).lower()
    words = re.findall(r"[a-z0-9áéíóúüñ]+", clean_text)
    if not words:
        return None

    vec = [0.0] * 64

    for w in words:
        if w in _EMBED_STOPWORDS:
            continue
        is_key = (w in _EMBED_KEY_TOKENS) or any(c.isdigit() for c in w)
        weight = 2.5 if is_key else 1.0

        for dim in range(64):
            h = zlib.crc32(f"{dim}_{w}".encode("utf-8"))
            sign = 1.0 if (h & 1) else -1.0
            vec[dim] += weight * sign

        if len(w) >= 4:
            for i in range(len(w) - 2):
                tri = w[i:i+3]
                for dim in range(64):
                    h = zlib.crc32(f"{dim}_t_{tri}".encode("utf-8"))
                    sign = 1.0 if (h & 1) else -1.0
                    vec[dim] += (0.5 * weight) * sign

    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0.0:
        return [0.0] * 64
    return [round(x / norm, 6) for x in vec]


def save_item_embedding(conn, item_key, radar_id, vector_or_text=None, model="models/gemini-embedding-2", verdict_json="", title="", price=0.0, seller_id="", precomputed_vec=None, **kwargs):
    """
    Guarda o actualiza un embedding en la tabla item_embeddings.
    Acepta 'vector_or_text' como bytes (BLOB), lista/tupla de floats, o string de texto.
    Si se proporciona 'precomputed_vec', se utiliza directamente ahorrando llamadas extra a la API de embeddings.
    """
    if not conn or not item_key:
        return False
    try:
        blob = None
        vec = precomputed_vec

        if vec is not None:
            if isinstance(vec, (bytes, bytearray)):
                blob = vec
            elif isinstance(vec, (list, tuple)):
                blob = pack_embedding(vec)
        elif isinstance(vector_or_text, (bytes, bytearray)):
            blob = vector_or_text
        elif isinstance(vector_or_text, (list, tuple)):
            blob = pack_embedding(vector_or_text)
        elif isinstance(vector_or_text, (int, float)):
            # Soporte de llamada alternativa: save_item_embedding(conn, item_id, radar_id, price, text, verdict_dict, seller_id="", precomputed_vec=None)
            price = float(vector_or_text)
            text_val = model if isinstance(model, str) else ""
            if isinstance(verdict_json, dict):
                verdict_json = json.dumps(verdict_json, ensure_ascii=False)
            if precomputed_vec:
                blob = pack_embedding(precomputed_vec) if isinstance(precomputed_vec, (list, tuple)) else precomputed_vec
            elif text_val:
                gen_vec = generate_text_embedding(text_val, model="models/gemini-embedding-2", radar_id=radar_id, conn=conn)
                if gen_vec:
                    blob = pack_embedding(gen_vec)
        elif isinstance(vector_or_text, str) and vector_or_text.strip():
            gen_vec = generate_text_embedding(vector_or_text, model=model, radar_id=radar_id, conn=conn)
            if gen_vec:
                blob = pack_embedding(gen_vec)

        if not blob:
            return False

        if isinstance(verdict_json, dict):
            verdict_json = json.dumps(verdict_json, ensure_ascii=False)

        c = conn.cursor()
        c.execute(
            """INSERT OR REPLACE INTO item_embeddings
               (item_key, radar_id, embedding, model, verdict_json, title, price, seller_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                str(item_key),
                str(radar_id or ""),
                blob,
                str(model),
                str(verdict_json or ""),
                str(title or "")[:200],
                float(price or 0.0),
                str(seller_id or "")
            )
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[WARN] Error guardando embedding en BD ({item_key}): {e}")
        return False


def find_semantic_duplicate(conn, text, radar_id="", threshold=0.92, hours=720, model="models/gemini-embedding-2", vector=None, price=None, exclude_item_key=None):
    """
    Busca si existe un anuncio previo semánticamente duplicado (similitud coseno >= threshold).
    Compara contra los embeddings del mismo radar (o todos si radar_id está vacío) en las últimas 'hours' horas (por defecto 30 días).
    exclude_item_key ("PLATAFORMA:id") excluye el propio anuncio: tras una rebaja no debe
    heredar su veredicto anterior (A-002).
    Devuelve tupla (dup_match_dict_or_None, vector_utilizado).
    dup_match_dict contiene: {item_key, radar_id, score, similarity, verdict_json, title, price, seller_id, created_at}
    """
    if not conn:
        return None, vector

    target_vec = vector
    if target_vec is None and text:
        target_vec = generate_text_embedding(text, model=model, radar_id=radar_id, conn=conn)

    if not target_vec:
        return None, None

    try:
        c = conn.cursor()
        query = "SELECT item_key, radar_id, embedding, verdict_json, title, price, seller_id, created_at FROM item_embeddings WHERE created_at >= datetime('now', ?)"
        params = [f"-{int(hours)} hours"]
        if radar_id:
            query += " AND radar_id = ?"
            params.append(str(radar_id))

        if exclude_item_key:
            query += " AND UPPER(item_key) != UPPER(?)"
            params.append(str(exclude_item_key))

        # Ignora veredictos temporales de pausa por presupuesto guardados antes de A-001
        query += " AND COALESCE(verdict_json, '') NOT LIKE '%Presupuesto IA diario alcanzado%'"
        # Ignora rechazos antiguos anclados a la mediana de compra de price_history (A-003)
        query += " AND NOT (COALESCE(verdict_json, '') LIKE '%\"is_valid_deal\": false%' AND LOWER(COALESCE(verdict_json, '')) LIKE '%mediana%')"

        if price is not None and float(price) > 0:
            p_val = float(price)
            min_p = round(p_val * 0.65, 2)
            max_p = round(p_val * 1.35, 2)
            query += " AND price BETWEEN ? AND ?"
            params.extend([min_p, max_p])

        query += " ORDER BY created_at DESC LIMIT 300"

        c.execute(query, tuple(params))
        rows = c.fetchall()

        best_score = 0.0
        best_match = None

        for r_key, r_radar, r_blob, r_verdict, r_title, r_price, r_seller, r_time in rows:
            if not r_blob:
                continue
            r_vec = unpack_embedding(r_blob)
            if not r_vec or len(r_vec) != len(target_vec):
                continue
            sim = cosine_similarity(target_vec, r_vec)
            if sim > best_score:
                best_score = sim
                best_match = {
                    "item_key": r_key,
                    "radar_id": r_radar,
                    "score": round(sim, 4),
                    "similarity": round(sim, 4),
                    "verdict_json": r_verdict or "",
                    "title": r_title or "",
                    "price": float(r_price or 0.0),
                    "seller_id": str(r_seller or ""),
                    "created_at": str(r_time)
                }

        if best_match and best_score >= threshold:
            return best_match, target_vec

    except Exception as e:
        print(f"[WARN] Error buscando duplicado semántico: {e}")

    return None, target_vec


def is_duplicate_recent_deal(conn, platform, title, price, hours=48, radar_id="", semantic_check=False, vector=None):
    if not conn or not title:
        return False
    norm_t = re.sub(r"[^a-z0-9]", "", html.unescape(title).lower())
    if len(norm_t) < 4:
        return False
    try:
        c = conn.cursor()
        c.execute(
            "SELECT title, price FROM deals WHERE platform = ? AND created_at >= datetime('now', ?)",
            (platform.upper(), f"-{int(hours)} hours")
        )
        recent_deals = c.fetchall()
        for r_title, r_price in recent_deals:
            r_norm = re.sub(r"[^a-z0-9]", "", html.unescape(r_title or "").lower())
            if r_norm == norm_t and abs(float(r_price or 0) - float(price or 0)) <= 10.0:
                return True

        if semantic_check and recent_deals:
            price_matches = [r for r in recent_deals if abs(float(r[1] or 0) - float(price or 0)) <= 10.0]
            if price_matches:
                dup_match, _ = find_semantic_duplicate(
                    conn, title, radar_id=radar_id, threshold=0.92, hours=hours, vector=vector
                )
                if dup_match:
                    return True
    except Exception:
        pass
    return False


def mark_item_seen(conn, platform, item_id, title, price):
    plat = platform.upper()
    sid = str(item_id)
    p = float(price or 0)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO market_seen_items (platform, item_id, title, price, seen_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (plat, sid, title, p)
    )
    conn.commit()


def count_platform_seen(conn, platform):
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM market_seen_items WHERE platform = ?", (platform.upper(),))
    return c.fetchone()[0]


def purge_old_kits(max_days=30):
    now = time.time()
    cutoff = now - (max_days * 86400)
    if not os.path.exists(KITS_DIR):
        return
    for folder in os.listdir(KITS_DIR):
        fpath = os.path.join(KITS_DIR, folder)
        if os.path.isdir(fpath):
            try:
                if os.path.getmtime(fpath) < cutoff:
                    shutil.rmtree(fpath)
            except Exception:
                pass


_config_lock = threading.Lock()

def load_raw_config(config_path=CONFIG_PATH):
    """Carga config.json crudo sin expandir ofertas, con reintentos breves (20ms-50ms) en caso de concurrencia."""
    cfg = None
    last_err = None
    for attempt in range(8):
        try:
            with _config_lock:
                with open(config_path, "r", encoding="utf-8") as f:
                    content = f.read()
            if content.strip():
                cfg = json.loads(content)
                break
            else:
                last_err = json.JSONDecodeError("Config file temporarily empty", "", 0)
        except (json.JSONDecodeError, OSError) as e:
            last_err = e
        time.sleep(0.025)
    if cfg is None:
        raise last_err if last_err else json.JSONDecodeError("Config file empty or invalid", "", 0)
    return cfg


def save_config(cfg, config_path=CONFIG_PATH):
    """Guarda config.json de manera atómica usando un archivo temporal y os.replace (con fallback para bind mounts en Docker)."""
    clean_cfg = dict(cfg)
    clean_targets = []
    for t in cfg.get("targets", []):
        tc = dict(t)
        if "direct_buy_max_price" in tc:
            tc["max_price"] = int(tc.pop("direct_buy_max_price"))
        clean_targets.append(tc)
    clean_cfg["targets"] = clean_targets

    data_str = json.dumps(clean_cfg, ensure_ascii=False, indent=2)

    with _config_lock:
        tmp_path = f"{config_path}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(data_str)
        try:
            os.replace(tmp_path, config_path)
        except OSError:
            # Fallback para cuando config_path es un bind mount directo en Docker (Errno 16 EBUSY)
            with open(config_path, "r+", encoding="utf-8") as f:
                f.seek(0)
                f.write(data_str)
                f.truncate()
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass


def load_config(expand_for_offers=True):
    """
    Carga config.json y amplía automáticamente el techo de escaneo un +30% (offer_scan_multiplier)
    para que los snipers capten también anuncios candidatos a 'Oportunidad de Chollo (Hacer Oferta)'
    conservando `direct_buy_max_price` como el tope estricto de compra directa y Flash Alerts.
    Fusiona y unifica todas las listas de palabras prohibidas globales (global_blacklist, global_blacklisted_words, discard_keywords).
    Incluye reintentos breves en caso de colisión con save_config.
    """
    cfg = load_raw_config(CONFIG_PATH)

    words = []
    words.extend(cfg.get("global_blacklist", []))
    words.extend(cfg.get("global_settings", {}).get("global_blacklisted_words", []))
    words.extend(cfg.get("discard_keywords", []))
    seen_w = set()
    unified_blacklist = []
    for w in words:
        w_clean = str(w).strip().lower()
        if w_clean and w_clean not in seen_w:
            seen_w.add(w_clean)
            unified_blacklist.append(w_clean)
    cfg["global_blacklist"] = unified_blacklist

    mult = float(cfg.get("offer_scan_multiplier", 1.30))
    for t in cfg.get("targets", []):
        base_max = int(t.get("direct_buy_max_price") or t.get("max_price", 100))
        t["direct_buy_max_price"] = base_max
        if expand_for_offers and t.get("offer_sniping", True):
            t["max_price"] = int(round(base_max * mult))
        else:
            t["max_price"] = base_max
    return cfg


# ==================== NORMALIZACIÓN DE ENLACES Y CHECKOUT 1-TAP ====================

def sanitize_item_url(url: str, platform: str = "") -> str:
    """
    Limpia parámetros basura (?utm_..., &promulgated...) y garantiza una URL canónica limpia.
    """
    if not url:
        return ""
    url_str = str(url).strip()
    plat = str(platform or "").upper()

    parsed = urllib.parse.urlparse(url_str)
    path = parsed.path

    if "WALLAPOP" in plat or "wallapop.com" in parsed.netloc:
        m = re.search(r"(/item/[a-zA-Z0-9_\-]+)", path)
        if m:
            return f"https://es.wallapop.com{m.group(1)}"
        return f"https://es.wallapop.com{path}".split("?")[0]

    elif "VINTED" in plat or "vinted." in parsed.netloc:
        m = re.search(r"(/items/\d+[a-zA-Z0-9_\-]*)", path)
        if m:
            return f"https://www.vinted.es{m.group(1)}"
        return f"https://www.vinted.es{path}".split("?")[0]

    elif "EBAY" in plat or "ebay." in parsed.netloc:
        m = re.search(r"(/itm/\d+)", path)
        if m:
            domain = parsed.netloc if "ebay." in parsed.netloc else "www.ebay.es"
            return f"https://{domain}{m.group(1)}"
        return f"https://www.ebay.es{path}".split("?")[0]

    elif "CASHCONVERTERS" in plat or "cashconverters.es" in parsed.netloc:
        return f"https://cashconverters.es{path}" if path else url_str.split("?")[0]

    # Para otras plataformas, limpiar parámetros conocidos de tracking
    query_params = urllib.parse.parse_qs(parsed.query)
    blacklist_params = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "promulgated", "tracking", "_trkparms", "_trksid", "hash", "amdata",
        "epid", "referrer", "ref", "source", "session_id"
    }
    clean_params = {k: v for k, v in query_params.items() if k.lower() not in blacklist_params and not k.lower().startswith("utm_")}
    new_query = urllib.parse.urlencode(clean_params, doseq=True)

    return urllib.parse.urlunparse((
        parsed.scheme or "https",
        parsed.netloc,
        parsed.path,
        "",
        new_query,
        ""
    ))


def send_one_tap_ntfy_alert(item_data, analysis_result=None, is_flash=False):
    """
    Envía notificación push 1-Tap mediante ntfy.sh con cabeceras Click y Actions
    para que al pulsar la notificación en el móvil se abra directamente la app nativa / anuncio.
    """
    if not NTFY_TOPIC_URL:
        return False

    try:
        title = item_data.get("title", "")
        raw_price = item_data.get("price", 0.0)
        if isinstance(raw_price, dict):
            raw_price = raw_price.get("amount") or 0.0
        try:
            price = float(raw_price or 0.0)
        except (ValueError, TypeError):
            price = 0.0
        platform = item_data.get("platform", "")
        raw_url = item_data.get("url") or item_data.get("item_url", "")
        clean_url = sanitize_item_url(raw_url, platform)

        analysis = analysis_result or {}
        p2p_profit = float(analysis.get("p2p_net_profit", 0.0) or 0.0)
        cex_cash = float(analysis.get("cex_cash", 0.0) or 0.0)
        deal_tier = analysis.get("deal_tier")

        if is_flash:
            tier_str = "FLASH"
            priority = "5"
            profit_str = ""
        else:
            tier_str = f"NIVEL {deal_tier}" if deal_tier else "CHOLLO"
            priority = "5" if deal_tier == 1 else "4"
            profit_str = f" (Beneficio +{p2p_profit:.0f}€)" if p2p_profit > 0 else ""

        title_header = f"⚡ [{tier_str}] {title[:45]} - {price:.0f}€{profit_str}"

        seller = item_data.get("seller") or {}
        seller_badge = item_data.get("seller_trust_badge") or seller.get("seller_trust_badge", "👤 Vendedor particular")

        body_lines = [
            f"{seller_badge}",
            f"💵 CeX: {cex_cash:.0f}€ | 📈 P2P: +{p2p_profit:.0f}€"
        ]
        body = "\n".join(body_lines)

        headers = {
            "Title": title_header,
            "Click": clean_url,
            "Actions": f"view, Comprar Ahora, {clean_url}, clear=true",
            "Priority": priority,
            "Tags": "zap,moneybag"
        }

        resp = HTTP_SESSION.post(NTFY_TOPIC_URL, data=body.encode("utf-8"), headers=headers, timeout=2.0)
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"[WARN] Error enviando ntfy alert: {e}")
        return False


def build_telegram_inline_keyboard(clean_url, price, item_id, cex_cash=0.0, cex_link=None):
    """
    Genera teclado Inline con botones gigantes de 1 toque para Telegram:
    - Botón 1 (Fila 1 completa): ⚡ ABRIR ANUNCIO DIRECTO ({price}€)
    - Botón 2 (Fila 2): 📊 Ver en CeX ({cex_cash}€) | 🚫 Descartar
    """
    buttons = [
        [{"text": f"⚡ ABRIR ANUNCIO DIRECTO ({price:.0f}€)", "url": clean_url}]
    ]
    row2 = []
    if cex_cash and cex_cash > 0 and cex_link:
        row2.append({"text": f"📊 Ver en CeX ({cex_cash:.0f}€)", "url": cex_link})
    row2.append({"text": "🚫 Descartar", "callback_data": f"discard_{item_id}"})
    buttons.append(row2)
    return {"inline_keyboard": buttons}


def normalize_and_build_links(platform, item_id, item_url, checkout_url=None):
    """
    Corrige y unifica los enlaces de todas las plataformas asegurando que
    apunten SIEMPRE a la ficha real y específica del anuncio:
    - En Vinted convierte dominios extranjeros (.fr, .it, .pt, .be) a vinted.es para abrir la App
      española directamente en la ficha del producto (/items/{id} o /items/{id}-{slug}).
      Se elimina la ruta /transaction/buy/new porque devuelve HTTP 404 y desorienta la app móvil
      dejándola atascada en el anuncio anterior.
    - En Wallapop, eBay, Milanuncios y CashConverters asegura URLs limpias y directas.
    """
    plat = (platform or "").upper()
    sid = str(item_id or "").strip()
    clean_item_url = (item_url or "").strip()

    if plat == "VINTED":
        if not sid and clean_item_url:
            m_id = re.search(r"/items/(\d+)", clean_item_url)
            if m_id:
                sid = m_id.group(1)
        if clean_item_url:
            clean_item_url = re.sub(r"https?://(?:www\.)?vinted\.[a-z.]+/", "https://www.vinted.es/", clean_item_url)
            clean_item_url = clean_item_url.split("?")[0]
        elif sid:
            clean_item_url = f"https://www.vinted.es/items/{sid}"

        # En Vinted la URL canónica del anuncio es el destino único y fiable tanto en web como en app nativa.
        clean_checkout_url = clean_item_url

    elif plat == "WALLAPOP":
        if not clean_item_url and sid:
            clean_item_url = f"https://es.wallapop.com/item/{sid}"
        clean_checkout_url = clean_item_url

    else:
        clean_checkout_url = (checkout_url or clean_item_url).strip()

    return clean_item_url, clean_checkout_url


def build_native_deeplink(platform_name: str, url: str = "", item_id: str = "") -> str:
    """
    Genera el esquema URI nativo (deeplink) para abrir la app oficial instalada
    en iOS o Android directamente en la ficha del anuncio sin pasar por el navegador web:
    - Wallapop: wallapop://item/{item_id}
    - Vinted: vinted://items/{item_id_numerico}
    """
    plat = (platform_name or "").upper()
    sid = str(item_id or "").strip()
    u = str(url or "").strip()

    if "WALLAPOP" in plat:
        clean_id = re.sub(r"^(?:wallapop|wal)_", "", sid, flags=re.IGNORECASE)
        if not clean_id and u:
            m = re.search(r"/(?:item/|items/)?(?:[a-zA-Z0-9_\u00C0-\u017F-]+-)?([a-zA-Z0-9]+)(?:$|[?/#])", u)
            if m:
                clean_id = m.group(1)
        if clean_id:
            return f"wallapop://item/{clean_id}"
        return ""

    if "VINTED" in plat:
        num_id = re.sub(r"^vinted_", "", sid, flags=re.IGNORECASE)
        if not num_id or not num_id.isdigit():
            if u:
                m = re.search(r"/items/(\d+)", u)
                if m:
                    num_id = m.group(1)
            if (not num_id or not num_id.isdigit()) and sid:
                m2 = re.search(r"(\d+)", sid)
                if m2:
                    num_id = m2.group(1)
        if num_id and num_id.isdigit():
            return f"vinted://items/{num_id}"
        return ""

    return ""


def generate_multilingual_haggle_msg(title, description_or_price="", country_tag="", offer_price=None, model_name=""):
    """
    Detecta el idioma del vendedor (FR / BE / IT / PT / NL / ES) y genera el mensaje de oferta
    persuasivo por `offer_price`€ listo para copiar y pegar en 1 clic.
    Soporta dos firmas:
      1. Clásica: (title, description, country_tag, offer_price, model_name="")
      2. Directa: (title, offer_price, country_tag)
    """
    if isinstance(description_or_price, (int, float)) or (isinstance(description_or_price, str) and description_or_price.replace(".", "", 1).isdigit() and not offer_price):
        offer_val = float(description_or_price)
        ctag = str(country_tag or "ES").upper().strip()
        desc_text = ""
    else:
        desc_text = str(description_or_price or "")
        ctag = str(country_tag or "ES").upper().strip()
        offer_val = float(offer_price if offer_price is not None else 0.0)

    offer_int = int(round(offer_val))
    text_l = f"{title or ''} {desc_text}".lower()

    # Prioridad 1: Código de país explícito (country_tag)
    if ctag in ("FR", "BE"):
        return (
            f"Bonjour ! Je suis très intéressé. Si vous acceptez une offre à {offer_int}€, "
            f"je vous l'achète immédiatement via Vinted (paiement instantané). Merci beaucoup !"
        )
    if ctag == "IT":
        return (
            f"Ciao! Sono molto interessato. Se accetti un'offerta di {offer_int}€, "
            f"procedo subito con l'acquisto immediato tramite l'app. Grazie mille!"
        )
    if ctag == "PT":
        return (
            f"Olá! Tenho muito interesse. Se aceitar uma proposta de {offer_int}€, "
            f"compro imediatamente através da aplicação com pagamento direto. Muito obrigado!"
        )
    if ctag == "NL":
        return (
            f"Hallo! Ik heb veel interesse. Als u akkoord gaat met een bod van {offer_int}€, "
            f"koop ik het meteen via de app met directe betaling. Alvast hartelijk dank!"
        )

    # Prioridad 2: Heurística por texto en el anuncio si country_tag es genérico ("EU", "ES", "")
    words = set(re.findall(r"\b\w+\b", text_l))
    is_fr = bool(words & {"bonjour", "vends", "vend", "état", "etat", "avec", "boite", "boîte", "manette", "envoi", "envoie", "très", "tres"})
    if is_fr:
        return (
            f"Bonjour ! Je suis très intéressé. Si vous acceptez une offre à {offer_int}€, "
            f"je vous l'achète immédiatement via Vinted (paiement instantané). Merci beaucoup !"
        )

    is_it = bool(words & {"ottimo", "scatola", "perfettamente", "spedizione", "funzionante", "grazie", "trattabile", "ritiro"})
    if is_it:
        return (
            f"Ciao! Sono molto interessato. Se accetti un'offerta di {offer_int}€, "
            f"procedo subito con l'acquisto immediato tramite l'app. Grazie mille!"
        )

    is_pt = bool(words & {"olá", "ola", "ótimo", "otimo", "caixa", "comandos", "obrigado"})
    if is_pt:
        return (
            f"Olá! Tenho muito interesse. Se aceitar uma proposta de {offer_int}€, "
            f"compro imediatamente através da aplicação com pagamento direto. Muito obrigado!"
        )

    is_nl = bool(words & {"hallo", "goede", "staat", "doos", "verzending", "bedankt", "alsjeblieft"})
    if is_nl:
        return (
            f"Hallo! Ik heb veel interesse. Als u akkoord gaat met een bod van {offer_int}€, "
            f"koop ik het meteen via de app met directe betaling. Alvast hartelijk dank!"
        )

    # Predeterminado: Español
    return (
        f"¡Hola! Me interesa mucho. Si te cuadran {offer_int}€ te hago la oferta y te lo compro "
        f"ahora mismo con pago inmediato por la aplicación, sin mareos. ¡Gracias!"
    )


# ==================== FILTRADO ALGORÍTMICO PREVIO A LA IA (0 TOKENS) ====================

ACCESSORY_TITLE_PREFIXES = (
    "funda ", "funda para ", "carcasa ", "protector ", "caja ", "caja vacia", "caja vacía",
    "cargador ", "cable ", "soporte ", "adaptador ", "correa ", "bateria ", "batería ",
    "manual ", "instrucciones ", "estuche ", "bolsa ", "maletin ", "maletín ", "mochila ",
    "cristal ", "juego ", "juegos ", "videojuego ", "lote de juegos", "pack de juegos",
    "coque ", "housse ", "boite ", "boîte ", "chargeur ", "etui ", "étui ", "verre ",
    "sacoche ", "pochette ", "casque ", "manette ", "jeu ", "jeux ", "lot de jeux",
    "custodia ", "scatola ", "caricatore ", "pellicola ", "borsa ", "zaino ", "cuffie ",
    "gioco ", "giochi ", "lotto giochi", "capa ", "caixa ", "auriculares ",
    "case for ", "cover for ", "box for ", "charger for ", "bag for ", "game for ",
    "placa base ", "placa madre ", "motherboard ", "pantalla ", "display ", "repuesto ", "recambio ",
    "despiece ", "piezas ", "stickers ", "pegatinas ", "skin ", "vinilo "
)

SPARE_PARTS_AND_COMPONENTS = (
    # Placas base y circuitería
    "placa base", "placa madre", "motherboard", "carte mere", "carte mère", "scheda madre",
    "mainboard", "placa logica", "placa lógica", "logic board",
    # Pantallas y recambios sueltos
    "pantalla superior", "pantalla inferior", "pantalla repuesto", "pantalla de repuesto",
    "pantalla recambio", "pantalla de recambio", "display repuesto", "repuesto de pantalla",
    "ecran de remplacement", "schermo di ricambio", "replacement screen", "flex de pantalla",
    # Módulos, despiece y lectores
    "despiece", "para piezas", "para repuestos", "para repuesto", "pour pieces", "pour pièces",
    "per parti", "per pezzi", "for parts", "piezas o reparar", "lector blu-ray", "lecteur blu-ray",
    "modulo wifi", "módulo wifi", "flex de encendido", "ventilador interno", "disipador",
    "fuente de alimentacion", "fuente de alimentación", "chasis interno"
)

CONSOLE_STANDALONE_ACCESSORIES = (
    r"(?:^|[^\w\+\&])(?:mando|controller|manette|gamepad)\s+(?:rematch|pro|wireless|inalambrico|inalámbrico|bluetooth|usb|cable|para|pour|for|sans fil|sans-fil)\b",
    r"\b(?:solo|seul|only)\s+(?:el\s+)?(?:mando|controller|manette|joycon|joy-con|dock|cargador|pantalla|tablet)\b",
    r"\b(?:dualsense|joycon|joy-con)\s+(?:suelto|suelta|seul|alone)\b",
    r"\b(?:dock\s+station|base\s+dock|dock\s+original)\b"
)

PRE_AI_DEFECT_TOKENS = DEFECT_TOKENS

CONSOLE_RADAR_IDS = {
    "new_3ds_family", "switch_oled", "switch_std_lite",
    "ps_vita", "retro_handhelds", "steam_deck_handhelds", "ps5_consoles_gta6"
}

CONSOLE_HARDWARE_WORDS = (
    "consola", "console", "nintendo switch", "switch oled", "switch lite", "switch v1", "switch v2", "switch 2019", "consola switch",
    "3ds xl", "3ds ll", "2ds xl", "2ds ll", "new 3ds", "new 2ds", "new nintendo 3ds", "nintendo 3ds", "nintendo 2ds",
    "ps vita", "playstation vita", "ps vita 1000", "ps vita 2000", "psvita", "psvita 1000", "psvita 2000", "pch-100", "pch-200",
    "gba sp", "advance sp", "ags-001", "ags-101", "game boy micro", "gameboy micro",
    "steam deck", "steamdeck", "rog ally", "legion go", "ps5", "playstation 5", "consola ps5",
    "con cargador", "avec chargeur", "con caricatore", "con dock", "avec station"
)

VIDEO_GAME_TITLES_AND_TOKENS = (
    "zelda", "ocarina", "majora", "tri force", "triforce", "between worlds", "breath of the wild",
    "tears of the kingdom", "skyward sword", "hyrule warriors", "professeur layton", "professor layton",
    "profesor layton", "pokemon", "pokémon", "mario kart", "super mario", "paper mario", "mario party",
    "mario & luigi", "mario et luigi", "luigi's mansion", "luigis mansion", "luigi mansion",
    "animal crossing", "tomodachi", "nintendogs", "kirby", "yo-kai", "yokai", "fire emblem",
    "dragon quest", "bravely", "metroid", "smash bros", "splatoon", "xenoblade", "bayonetta",
    "octopath", "persona ", "uncharted", "god of war", "killzone", "littlebigplanet", "tearaway",
    "gravity rush", "mortal kombat", "resident evil", "silent hill", "castlevania", "final fantasy",
    "kingdom hearts", "inazuma", "monster hunter", "donkey kong", "pikmin", "wario", "rayman",
    "minecraft", "assassin's creed", "assassins creed", "call of duty", "need for speed",
    "lego ", "sonic ", "sims ", "cooking mama", "rhythm paradise", "kid icarus", "starfox",
    "star fox", "detective pikachu", "miitopia", "ever oasis", "fantasy life", "story of seasons",
    "harvest moon", "rune factory", "shin megami", "etrian odyssey", "ace attorney", "phoenix wright",
    "danganronpa", "persona 4", "metal gear", "sly cooper", "jak and daxter", "ratchet", "borderlands",
    "fifa ", "fifa2", "fc 24", "fc 25", "pes ", "just dance", "ring fit", "switch sports",
    "arceus", "escarlata", "purpura", "violet", "scarlet", "espada", "escudo", "sword", "shield",
    "let's go", "lets go", "ultrasol", "ultraluna", "ultra sun", "ultra moon", "omega ruby",
    "alpha sapphire", "rubi omega", "zafiro alfa", "overcooked", "witcher", "wonderful 101",
    "puzzle bobble", "hogwarts legacy", "dark souls", "skyrim", "diablo", "cuphead", "hollow knight",
    "stardew valley", "it takes two", "subnautica", "terraria", "dragon ball", "naruto", "one piece",
    "grand theft auto", "red dead", "nba 2k", "blister", "remastered", "trilogy",
    "para nintendo switch", "pour nintendo switch", "per nintendo switch", "for nintendo switch",
    "para nintendo 3ds", "pour nintendo 3ds", "per nintendo 3ds",
    "jeu ", "jeux ", "gioco ", "giochi ", "videojuego", "casque ", "mochila ", "konix"
)

GAME_OR_ACC_STRONG_MARKERS = (
    "para nintendo switch", "pour nintendo switch", "per nintendo switch", "for nintendo switch",
    "para nintendo 3ds", "pour nintendo 3ds", "per nintendo 3ds",
    "videojuego", "jeu switch", "jeu 3ds", "jeu nintendo", "gioco switch", "gioco 3ds", "gioco nintendo",
    "sous blister", "sotto blister", "en blister", "remastered", "casque ", "mochila ", "konix",
    "funda nintendo", "housse nintendo", "custodia nintendo", "sacoche nintendo"
)


def _token_in_text(token, text):
    token_l = token.lower().strip()
    if not token_l:
        return False
    if len(token_l) <= 4 and " " not in token_l:
        return re.search(rf"\b{re.escape(token_l)}\b", text) is not None
    return token_l in text


def passes_filters(title, description, price, target, global_blacklist, extra_text="", seller=None):
    if not target.get("enabled", True):
        return False

    offer_mult = 1.30 if target.get("offer_sniping", True) else 1.0
    base_max = float(target.get("direct_buy_max_price") or target.get("max_price", 9999))
    effective_max = max(float(target.get("max_price", 9999)), round(base_max * offer_mult))

    if price < target["min_price"] or price > effective_max:
        return False

    clean_title = html.unescape(title or "").strip()
    clean_desc = html.unescape(description or "").strip()
    title_l = clean_title.lower()
    if title_l.startswith(ACCESSORY_TITLE_PREFIXES):
        return False

    text = f"{clean_title} {clean_desc} {extra_text}".lower()
    radar_id = target.get("id", "")

    # Descarte de componentes internos, placas base y despiece (P7-1)
    if any(_token_in_text(comp, text) for comp in SPARE_PARTS_AND_COMPONENTS):
        return False

    # Descarte de mandos o docks sueltos en radares de consolas (P7-1)
    if radar_id in CONSOLE_RADAR_IDS:
        if any(re.search(pat, title_l) for pat in CONSOLE_STANDALONE_ACCESSORIES):
            if not any(cw in title_l for cw in ("consola", "console", "pack consola", "consola con", "consola +")):
                return False

    # Motor determinista de detección de fraude en capas (Capa 1: reglas de contacto/pago, Capa 2: vendedor)
    f_res = evaluate_fraud_risk(clean_title, f"{clean_desc} {extra_text}", price, radar_id=radar_id, seller=seller)
    if f_res["is_fraud"]:
        return False

    if radar_id in CONSOLE_RADAR_IDS:
        if any(gm in text for gm in GAME_OR_ACC_STRONG_MARKERS):
            if not any(cw in title_l for cw in ("consola", "console")):
                return False

        if radar_id == "new_3ds_family" and any(k in title_l for k in ("dsi", "ds lite", "dslite", "nintendo ds ")):
            if not any(k in title_l for k in ("new 3ds", "3ds xl", "2ds xl", "new 2ds")):
                return False

        if any(gt in text for gt in VIDEO_GAME_TITLES_AND_TOKENS):
            has_console_hw = any(cw in title_l for cw in CONSOLE_HARDWARE_WORDS)
            if not has_console_hw:
                return False

        if re.search(r"\b(r4|r4i|jeu|jeux|gioco|giochi|juego|juegos|videojuego|cartucho|cartouche|cartuccia|blister)\b", text):
            if not any(cw in title_l for cw in CONSOLE_HARDWARE_WORDS):
                return False

    if radar_id == "apple_high_liquidity":
        if "mac mini" in text:
            intel_mac_tokens = (
                "250gb", "250 gb", "500gb", "500 gb", "320gb", "120gb", "ddr3",
                "4gb", "4 gb", "catalina", "mojave", "high sierra", "sierra", "el capitan",
                "yosemite", "firewire", "hd 4000", "hd graphics", "core 2", "core i",
                "i3", "i5", "i7", "2009", "2010", "2011", "2012", "2014", "2018", "a1347", "a1993"
            )
            if any(imt in text for imt in intel_mac_tokens):
                return False
            apple_silicon_tokens = ("m1", "m2", "m3", "m4", "a2348", "a2686", "apple silicon")
            if not any(_token_in_text(ast, text) for ast in apple_silicon_tokens):
                return False

    for bad in global_blacklist + target.get("blacklist", []):
        if _token_in_text(bad, text):
            return False

    must_any = target.get("must_contain_any", [])
    if must_any and not any(_token_in_text(word, text) for word in must_any):
        return False

    # Topes por submodelo (permitiendo hasta +30% para candidatos a Oferta/Regateo, salvo topes de bloqueo <= 5€)
    for cap_rule in target.get("submodel_price_caps", []):
        max_sub_p = float(cap_rule.get("max_price", 9999))
        allowed_sub_max = max_sub_p if max_sub_p <= 5 else round(max_sub_p * offer_mult)
        if price > allowed_sub_max:
            match_tokens = cap_rule.get("match_any", [])
            unless_tokens = cap_rule.get("unless_any", [])
            if any(_token_in_text(mt, text) for mt in match_tokens):
                if not (unless_tokens and any(_token_in_text(ut, text) for ut in unless_tokens)):
                    return False

    return True


# ==================== CEX ESPAÑA ALGOLIA EN VIVO ====================

ACCESSORY_KEYWORDS_CEX = [
    "funda", "carcasa", "protector", "adaptador", "cable", "cargador",
    "soporte", "estuche", "correa", "bateria", "dock", "caja vacia",
    "carrying case", "pouch", "stand", "grip", "skin", "stylus", "volante", "wheel",
    "mando", "controller", "joy-con", "joycon", "auriculares", "headset",
    "shifter", "palanca", "pedales", "lector de discos"
]

RADAR_ALLOWED_CEX_CATEGORIES = {
    "new_3ds_family": ("consolas",),
    "switch_oled": ("consolas",),
    "switch_std_lite": ("consolas",),
    "ps_vita": ("consolas",),
    "retro_handhelds": ("consolas",),
    "ps5_consoles_gta6": ("consolas",),
    "steam_deck_handhelds": ("consolas", "pc gaming portatil", "portatiles"),
    "digicams_premium": ("camaras", "fotografia"),
    "mirrorless_cameras": ("camaras", "fotografia"),
    "pokemon_ds_gba": ("juegos", "nintendo ds", "gameboy", "game boy"),
    "pokemon_ds_3ds_gba": ("juegos", "nintendo ds", "gameboy", "game boy", "3ds", "nintendo 3ds", "switch", "playstation"),
    "apple_high_liquidity": ("apple ipad", "tablets", "pc sobremesa - apple", "apple mac"),
    "rtx_gpus": ("tarjetas graficas", "componentes"),
    "simracing_wheels": ("accesorios", "volantes", "playstation", "pc", "xbox"),
    "kindle_ereaders": ("ereaders", "tablets"),
}

SPECIAL_EDITION_CONSOLE_TOKENS = (
    "pokemon", "pokémon", "zelda", "hyrule", "majora", "mario", "luigi", "peach",
    "animal crossing", "monster hunter", "smash bros", "splatoon", "fire emblem",
    "metroid", "samus", "pikachu", "solgaleo", "lunala", "pokeball", "snes",
    "super nintendo", "dragon quest", "yo-kai", "yokai", "persona", "minecraft",
    "fortnite", "diablo", "edicion", "edición", "limited", "special edition",
    "collector", "spider-man", "spiderman", "aniversario", "anniversary", "god of war", "final fantasy"
)

CEX_CONSOLE_ACCESSORY_REJECT = (
    "bolsa", "funda", "protector", "mando", "dock", "volante", "stand", "grip", "cover plate",
    "estuche", "maletin", "maletín", "mochila"
)

STRICT_MODEL_TOKENS = (
    "a6000", "a6100", "a6400", "zv-e10", "zve10", "x-t10", "xt10", "x-t20", "xt20",
    "x-t30", "xt30", "x100", "g7x", "g9x", "g5x", "sx720", "sx730", "sx740", "rx100",
    "3060", "3070", "3080", "4060", "4070", "g29", "g920", "g923", "t300",
    "heartgold", "soulsilver", "platino", "blanco 2", "negro 2", "esmeralda",
    "cristal", "rojo fuego", "verde hoja", "ags-001", "ags-101", "micro",
    "dsi xl", "steam deck", "rog ally", "legion go", "paperwhite", "oasis"
)

_RE_NORM_PUNCT = re.compile(r"[\-_/+]")
_RE_NORM_SPACES = re.compile(r"\s+")
CLEAN_GAME_TOKENS = tuple(gt.strip() for gt in VIDEO_GAME_TITLES_AND_TOKENS if len(gt.strip()) >= 4)


def _normalize_cex_text(s):
    if not s:
        return ""
    if "&" in s:
        s = html.unescape(s)
    s = s.lower()
    s = _RE_NORM_PUNCT.sub(" ", s)
    s = _RE_NORM_SPACES.sub(" ", s).strip()
    return s


def _box_matches_query_and_radar(bname=None, cat="", clean_q=None, target=None, context_text="", includes_lens=True, **kwargs):
    # Compatibilidad de firmas (bidireccional y kwargs):
    # Firma 1 estándar: (bname, cat, clean_q, target=target_dict, context_text="", includes_lens=True)
    # Firma 2 simplificada: (box_name, query, radar_id, context_text="")
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
    bname_norm = _normalize_cex_text(bname)
    cat_l = (cat or "").lower()
    q_norm = _normalize_cex_text(clean_q)
    ctx_norm = _normalize_cex_text(f"{clean_q} {context_text}")
    radar_id = (target or {}).get("id", "") if isinstance(target, dict) else (target if isinstance(target, str) else "")
    rid = radar_id
    is_console_radar = (rid in CONSOLE_RADAR_IDS) or any(c_id in rid for c_id in CONSOLE_RADAR_IDS)

    if radar_id in RADAR_ALLOWED_CEX_CATEGORIES:
        allowed_cats = RADAR_ALLOWED_CEX_CATEGORIES[radar_id]
        if not any(ac in cat_l for ac in allowed_cats):
            return False

    if is_console_radar:
        if "juegos" in cat_l or "accesorios" in cat_l:
            return False
        bname_no_sin = re.sub(r"sin\s+(cargador|caja|lapiz|manual|dock|lector)", "", bname_l)
        bname_no_sin = re.sub(r"\+\s*joy-?con", "", bname_no_sin)
        if any(bad in bname_no_sin for bad in CEX_CONSOLE_ACCESSORY_REJECT):
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
        for gt in CLEAN_GAME_TOKENS:
            if gt in bname_norm and gt not in q_norm:
                if any(se in q_norm and se in bname_norm for se in SPECIAL_EDITION_CONSOLE_TOKENS):
                    continue
                return False
        # No cruzar consola estándar con Edición Especial (evaluando solo q_norm de la IA)
        for se_tok in SPECIAL_EDITION_CONSOLE_TOKENS:
            if (se_tok in bname_norm) != (se_tok in q_norm):
                return False

    # Reglas específicas para Videojuegos y Coleccionismo Retro (P7-2)
    if radar_id == "pokemon_ds_gba" or "juegos" in cat_l:
        has_peripheral_in_box = bool(re.search(r"(\+|\bcon\b)\s*pok[eé]walker|\bcaja\s+exterior\b|\bbig\s+box\b|\bcaja\s+cart[oó]n\b", bname_l))
        has_peripheral_in_ctx = bool(re.search(r"pok[eé]walker|pod[oó]metro|caja\s+exterior|big\s+box|caja\s+cart[oó]n", ctx_norm))
        if has_peripheral_in_box and not has_peripheral_in_ctx:
            return False

        is_loose_ctx = any(lc in ctx_norm for lc in ("solo cartucho", "cartucho suelto", "solo juego", "loose", "sin caja", "sin estuche"))
        if is_loose_ctx:
            if any(mb in bname_l for mb in ("+ manual, caja", "sin manual, caja", "con caja exterior", "perfecto")):
                return False
            if ("gba" in cat_l or "advance" in cat_l) and "sin caja" not in bname_l and any(cb in bname_l for cb in ("caja", "manual", "perfecto")):
                return False

    # Reglas específicas para PlayStation 5 (evitar confusión Digital vs Lector vs Pro vs Ediciones Especiales de 600€)
    if radar_id == "ps5_consoles_gta6" or "playstation 5" in q_norm or "ps5" in q_norm:
        if "pro" in bname_norm.split() and "pro" not in ctx_norm.split():
            return False
        if ("digital" in bname_norm) != ("digital" in q_norm or "sin lector" in ctx_norm):
            return False
        if "slim" in bname_norm and "slim" not in ctx_norm and "1tb" not in ctx_norm:
            return False

    if radar_id == "rtx_gpus":
        if any(pc_w in bname_l for pc_w in ("intel", "ryzen", "core i", "ssd", "ram", "ordenador", "portatil", "laptop", "pc ")):
            return False
        if "ti" in bname_norm.split() and "ti" not in ctx_norm.split():
            return False
        if "ti" in q_norm.split() and "ti" not in bname_norm.split():
            return False
        if "super" in bname_norm.split() and "super" not in ctx_norm.split():
            return False
        if "12gb" in bname_l and ("8gb" in ctx_norm or "8 gb" in ctx_norm):
            return False

    if radar_id == "simracing_wheels":
        if any(bad_w in bname_l for bad_w in ("cambiador", "servo base", "auriculares", "raton", "teclado", "mando", "soporte", "flight")):
            return False
        if "shifter" in bname_l and not any(sw in ctx_norm for sw in ("shifter", "palanca", "cambio", "marchas")):
            return False
        if not any(w in bname_l for w in ("wheel", "volante")):
            return False

    for tok in STRICT_MODEL_TOKENS:
        tok_norm = _normalize_cex_text(tok)
        if re.search(rf"\b{re.escape(tok_norm)}\b", q_norm):
            alt_forms = [tok_norm, tok_norm.replace(" ", "")]
            if tok_norm == "zv e10":
                alt_forms.extend(["zv-e10", "zve10"])
            elif tok_norm in ("x t10", "x t20", "x t30"):
                alt_forms.append(tok_norm.replace(" ", "-"))
            if not any(re.search(rf"\b{re.escape(af)}\b", bname_norm) or af in bname_l.replace("-", "") for af in alt_forms):
                return False

    if "3ds" in q_norm or "2ds" in q_norm or radar_id == "new_3ds_family":
        has_new_box = bool(re.search(r"\bnew\b", bname_norm))
        has_new_ctx = bool(re.search(r"\bnew\b", ctx_norm))
        if has_new_box != has_new_ctx:
            return False
        has_xl_box = bool(re.search(r"\b(xl|ll)\b", bname_norm))
        has_xl_ctx = bool(re.search(r"\b(xl|ll)\b", ctx_norm))
        if has_xl_box != has_xl_ctx:
            return False
        if ("2ds" in q_norm and "2ds" not in bname_norm) or ("3ds" in q_norm and "3ds" not in bname_norm):
            return False

    if "switch" in q_norm or "switch" in radar_id or radar_id in ("switch_oled", "switch_std_lite"):
        if ("oled" in bname_norm) != ("oled" in ctx_norm):
            return False
        if ("lite" in bname_norm) != ("lite" in ctx_norm):
            return False

    if "steam deck" in q_norm or radar_id == "steam_deck_handhelds":
        if ("oled" in bname_norm) != ("oled" in ctx_norm):
            return False
        if "steam deck" in q_norm:
            if "512gb" in bname_l and "512" not in ctx_norm:
                return False
            if "1tb" in bname_l and "1tb" not in ctx_norm and "1 tb" not in ctx_norm:
                return False
            if "256gb" in bname_l and "256" not in ctx_norm and "512" not in ctx_norm:
                return False

    if radar_id in ("digicams_premium", "mirrorless_cameras"):
        gen_markers_ii = ("mk ii", "mark ii", "mark 2", "m2", " ii ")
        gen_markers_iii = ("mk iii", "mark iii", "mark 3", "m3", " iii ")
        b_pad = f" {bname_norm} "
        c_pad = f" {ctx_norm} "
        if any(g in b_pad for g in gen_markers_iii) and not any(g in c_pad for g in gen_markers_iii):
            return False
        if any(g in b_pad for g in gen_markers_ii) and not any(g in c_pad for g in gen_markers_ii):
            return False
        if radar_id == "mirrorless_cameras" and not includes_lens:
            if any(lk in bname_l for lk in ("16-50", "18-55", "15-45", "+ obj", "con obj")):
                return False

    if radar_id == "apple_high_liquidity":
        if "mac mini" in q_norm:
            if "mac mini" not in bname_norm:
                return False
            if ("m2" in bname_norm) != ("m2" in ctx_norm):
                return False
            if ("m1" in bname_norm) != ("m1" in ctx_norm):
                return False
            if "16gb" in bname_l and "16gb" not in ctx_norm and "16 gb" not in ctx_norm:
                return False
            if ("512gb" in bname_l or "1tb" in bname_l) and not any(k in ctx_norm for k in ("512", "1tb", "1 tb")):
                return False
        elif "ipad" in q_norm:
            if "ipad" not in bname_norm:
                return False
            for gen_num in ("9", "10", "air 5"):
                if f"ipad {gen_num}" in q_norm and f"ipad {gen_num}" not in bname_norm:
                    return False
            if ("256gb" in bname_l or "128gb" in bname_l) and not any(k in ctx_norm for k in ("256", "128")):
                return False
            if any(cel in bname_l for cel in ("cellular", "4g", "5g", "lte")) and not any(cel in ctx_norm for cel in ("cellular", "4g", "5g", "lte", "sim")):
                return False

    if radar_id == "kindle_ereaders":
        if "oasis" in q_norm and "oasis" not in bname_norm:
            return False
        if "paperwhite" in q_norm and "paperwhite" not in bname_norm:
            return False
        if ("11" in bname_norm or "16gb" in bname_l) and not any(k in ctx_norm for k in ("11", "6.8", "m2l3ek", "16gb", "16 gb", "usb c", "usb-c")):
            return False

    return True


# ==================== MOTOR CEX ESPAÑA: ESPEJO LOCAL & SCORE ====================

CEX_MASTER_QUERIES = [
    "Nintendo Switch Consola",
    "Nintendo 3DS Consola",
    "PS Vita Consola",
    "Steam Deck",
    "PlayStation 5 Consola",
    "Pokemon",
    "GeForce RTX",
    "iPad",
    "Mac Mini",
    "Sony RX100",
    "Canon G7X",
    "Logitech G29"
]


def _score_cex_candidate(box_or_boxes, clean_q="", radar_id="generic", full_ctx="", includes_box=False):
    """
    Puntúa candidatos de CeX o selecciona la mejor ficha de una lista de candidatos válidos.
    Soporta:
      - box_or_boxes como dict: devuelve float con la puntuación calculada.
      - box_or_boxes como list: devuelve el mejor dict seleccionado (o None si la lista está vacía).
    """
    def _score_single(box):
        if not isinstance(box, dict):
            return -999.0

        bname = str(box.get("box_name") or "")
        bname_l = bname.lower()
        cat = str(box.get("category") or box.get("category_name") or "").lower()
        cash = float(box.get("cash") or box.get("cash_price") or 0.0)
        sell = float(box.get("sell") or box.get("sell_price") or 0.0)

        score = 0.0

        # 1. Relevancia léxica con clean_q
        q_norm = _normalize_cex_text(clean_q)
        b_norm = _normalize_cex_text(bname)
        q_tokens = [w for w in re.split(r"[^\w]+", q_norm) if len(w) >= 2 and w not in ("de", "la", "el", "los", "las", "un", "una", "con", "sin", "para", "por", "en")]

        if q_tokens:
            matches = sum(1 for w in q_tokens if w in b_norm)
            score += matches * 20.0
            if matches == len(q_tokens):
                score += 30.0  # Todos los tokens presentes
        if q_norm and q_norm in b_norm:
            score += 25.0  # Subcadena exacta

        # 2. Preferencias por estado, caja y categoría
        is_game = radar_id in ("pokemon_ds_gba", "pokemon_ds_3ds_gba") or "juegos" in cat
        has_periph = bool(re.search(r"pok[eé]walker|pod[oó]metro|caja\s+exterior|big\s+box|caja\s+cart[oó]n", full_ctx.lower()))

        is_sin_caja = any(k in bname_l for k in ("sin caja", "sincaja", "unboxed"))
        is_grade_b = any(k in bname_l for k in ("grado b", ", b", "/b"))
        is_grade_a = any(k in bname_l for k in ("grado a", ", a", "/a"))
        is_grade_c = any(k in bname_l for k in ("rebajada", ", c", "/c", "grado c"))
        is_boxed = any(k in bname_l for k in ("con caja", ", caja", "boxed"))

        if is_game:
            if not includes_box and is_sin_caja:
                score += 25.0
            elif has_periph and any(k in bname_l for k in ("pokewalker", "caja exterior", "big box")):
                score += 35.0
            elif not has_periph and any(k in bname_l for k in ("pokewalker", "caja exterior", "big box", "collector", "steelbook")):
                score -= 50.0
        else:
            if not includes_box:
                if is_sin_caja:
                    score += 30.0
                elif is_grade_b:
                    score += 15.0
                elif is_grade_a:
                    score -= 20.0
                elif is_grade_c:
                    score -= 10.0
            else:
                if is_boxed:
                    score += 30.0
                elif is_grade_b:
                    score += 15.0
                elif is_sin_caja:
                    score -= 30.0
                elif is_grade_a:
                    score += 10.0

        if cash > 0:
            score += 5.0
            # Preferir valoración conservadora a igualdad de condiciones
            score -= (cash * 0.001)

        return score

    if isinstance(box_or_boxes, list):
        if not box_or_boxes:
            return None
        return max(box_or_boxes, key=_score_single)
    elif isinstance(box_or_boxes, dict):
        return _score_single(box_or_boxes)
    return 0.0


def sync_cex_catalog_mirror(conn=None, force=False, master_queries=None) -> int:
    """
    Pre-calienta y sincroniza el espejo local de fichas CeX España en SQLite (cex_catalog_mirror).
    Si no han pasado más de 12 horas desde la última sincronización y force=False, no hace nada.
    Si force=True o han pasado >= 12 horas, consulta Algolia (hitsPerPage=50) para las 12 queries
    maestras e inserta/actualiza las fichas en cex_catalog_mirror.
    Devuelve el número total de fichas sincronizadas.
    """
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True

    try:
        c = conn.cursor()
        if not force:
            try:
                c.execute("""
                    SELECT count(*), max(last_synced)
                    FROM cex_catalog_mirror
                    WHERE last_synced >= datetime('now', '-12 hours')
                """)
                row = c.fetchone()
                if row and row[0] > 0:
                    return 0
            except Exception:
                pass

        queries_to_sync = master_queries if master_queries is not None else CEX_MASTER_QUERIES
        url = "https://search.webuy.io/1/indexes/prod_cex_es/query"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://es.webuy.com",
            "Referer": "https://es.webuy.com/"
        }
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

        total_synced = 0
        for q in queries_to_sync:
            try:
                r = requests.post(
                    url,
                    json={"query": q, "hitsPerPage": 50},
                    headers=headers,
                    proxies=proxies,
                    impersonate="safari17_0",
                    timeout=12
                )
                if r.status_code != 200:
                    continue

                hits = r.json().get("hits", [])
                for h in hits:
                    box_id = str(h.get("boxId") or "").strip()
                    if not box_id:
                        continue
                    bname = str(h.get("boxName") or "").strip()
                    cat = str(h.get("categoryFriendlyName") or "").strip()
                    cash = float(h.get("cashPriceCalculated") or h.get("cashBuyPrice") or h.get("cashPrice") or 0.0)
                    voucher = float(h.get("exchangePriceCalculated") or h.get("exchangePrice") or 0.0)
                    sell = float(h.get("sellPrice") or 0.0)

                    if cash <= 0 and sell <= 0:
                        continue

                    c.execute("""
                        INSERT OR REPLACE INTO cex_catalog_mirror
                        (box_id, box_name, category_name, cash_price, voucher_price, sell_price, last_synced)
                        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (box_id, bname, cat, cash, voucher, sell))
                    total_synced += 1

                conn.commit()
            except Exception as e:
                print(f"[WARN] Error sincronizando CeX mirror para '{q}': {e}")

        return total_synced
    except Exception as e:
        print(f"[WARN] sync_cex_catalog_mirror general error: {e}")
        return 0
    finally:
        if should_close:
            try:
                conn.close()
            except Exception:
                pass


def lookup_cex_catalog_mirror(
    conn, clean_q, target=None, title_context="", desc_context="",
    includes_box=False, includes_lens=True, return_all_variants=False,
    radar_id=""
):
    """
    Busca en el espejo local SQLite (cex_catalog_mirror) con latencia <1ms.
    Aplica _box_matches_query_and_radar y _score_cex_candidate en memoria.
    Devuelve dict con la mejor ficha de CeX (o list si return_all_variants=True), o None si no hay match.
    """
    if not conn or not clean_q:
        return None

    try:
        rid = (target or {}).get("id", "") if isinstance(target, dict) else (radar_id or "generic")
        q_norm = _normalize_cex_text(clean_q)
        cex_stop_words = {
            "de", "la", "el", "los", "las", "un", "una", "uno", "del", "con", "sin", "para", "por", "en",
            "consola", "tarjeta", "grafica", "videojuego", "reproductor", "volante", "portatil", "ordenador",
            "cargador", "funda", "cables", "caja"
        }
        q_tokens = [w for w in re.split(r"[^\w]+", q_norm) if len(w) >= 2 and w not in cex_stop_words]
        if not q_tokens:
            return None

        # Filtrar candidatos en SQLite con AND de los tokens principales para acotar a <1ms
        tokens = [w for w in re.split(r"[^\w]+", q_norm) if len(w) >= 3 and w not in cex_stop_words]
        if not tokens:
            tokens = [w for w in re.split(r"[^\w]+", q_norm) if len(w) >= 2 and w not in cex_stop_words]
        if not tokens:
            tokens = [w for w in re.split(r"[^\w]+", q_norm) if len(w) >= 2]

        c = conn.cursor()
        where_clauses = ["box_name LIKE ?" for _ in tokens]
        params = [f"%{w}%" for w in tokens]
        sql = f"""
            SELECT box_id, box_name, category_name, cash_price, voucher_price, sell_price
            FROM cex_catalog_mirror
            WHERE cash_price > 0 AND sell_price > 0 AND ({" AND ".join(where_clauses)})
        """
        c.execute(sql, params)
        rows = c.fetchall()

        if not rows and len(tokens) > 1:
            best_tokens = sorted(tokens, key=len, reverse=True)[:2]
            where_clauses = ["box_name LIKE ?" for _ in best_tokens]
            params = [f"%{w}%" for w in best_tokens]
            sql = f"""
                SELECT box_id, box_name, category_name, cash_price, voucher_price, sell_price
                FROM cex_catalog_mirror
                WHERE cash_price > 0 AND sell_price > 0 AND ({" AND ".join(where_clauses)})
            """
            c.execute(sql, params)
            rows = c.fetchall()

        if not rows:
            return None

        full_ctx = f"{title_context} {desc_context}".strip()
        t = target if target is not None else ({"id": rid} if rid else None)

        valid_boxes = []
        for r in rows:
            box_id, bname, cat, cash, voucher, sell = r[0], r[1], r[2], float(r[3]), float(r[4]), float(r[5])
            if not _box_matches_query_and_radar(
                bname, cat, clean_q, target=t, context_text=full_ctx, includes_lens=includes_lens
            ):
                continue

            valid_boxes.append({
                "box_name": bname,
                "cash": cash,
                "voucher": voucher,
                "sell": sell,
                "box_id": str(box_id or ""),
                "category": cat or "",
                "from_cache": True,
                "from_mirror": True
            })

        if not valid_boxes:
            return None

        if return_all_variants:
            valid_boxes.sort(
                key=lambda b: _score_cex_candidate(b, clean_q, radar_id=rid, full_ctx=full_ctx, includes_box=includes_box),
                reverse=True
            )
            return valid_boxes[:8]

        best_box = _score_cex_candidate(
            valid_boxes, clean_q, radar_id=rid, full_ctx=full_ctx, includes_box=includes_box
        )
        return best_box
    except Exception as e:
        print(f"[DEBUG] lookup_cex_catalog_mirror error: {e}")
        return None


def fetch_cex_live(
    conn, search_query, reference_price=0, return_all_variants=False,
    target=None, title_context="", desc_context="", includes_box=False, includes_lens=True
):
    if not search_query:
        return None

    clean_q = re.sub(r"\(.*?\)", "", search_query).strip()
    if (target or {}).get("id") == "simracing_wheels" or any(w in clean_q.lower() for w in ("g29", "g920", "g923", "t300")):
        clean_q = re.sub(r"\b(volante|pedales|y|con)\b", "", clean_q, flags=re.IGNORECASE)
        clean_q = re.sub(r"\s+", " ", clean_q).strip()
    if not clean_q:
        return None

    radar_id = (target or {}).get("id", "generic") if isinstance(target, dict) else "generic"
    cache_lookup_key = f"v43:{radar_id}:{clean_q.lower()}:b{int(bool(includes_box))}:l{int(bool(includes_lens))}"

    if conn and not return_all_variants:
        try:
            c = conn.cursor()
            c.execute(
                "SELECT box_name, cash_price, voucher_price, sell_price, box_id FROM cex_cache WHERE search_query = ?",
                (cache_lookup_key,)
            )
            row = c.fetchone()
            if row and row[1] > 0:
                return {
                    "box_name": row[0],
                    "cash": float(row[1]),
                    "voucher": float(row[2]),
                    "sell": float(row[3]),
                    "box_id": row[4] or "",
                    "from_cache": True
                }
        except Exception:
            pass

    # 1.5. Consultar espejo local cex_catalog_mirror en SQLite (<1ms)
    if conn:
        mirror_res = lookup_cex_catalog_mirror(
            conn=conn,
            clean_q=clean_q,
            target=target,
            title_context=title_context,
            desc_context=desc_context,
            includes_box=includes_box,
            includes_lens=includes_lens,
            return_all_variants=return_all_variants,
            radar_id=radar_id
        )
        if mirror_res:
            if not return_all_variants and isinstance(mirror_res, dict) and mirror_res.get("cash", 0) > 0:
                try:
                    c = conn.cursor()
                    c.execute(
                        """INSERT OR REPLACE INTO cex_cache
                           (search_query, box_name, cash_price, voucher_price, sell_price, box_id, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (cache_lookup_key, mirror_res["box_name"], mirror_res["cash"], mirror_res["voucher"], mirror_res["sell"], mirror_res.get("box_id", ""))
                    )
                    conn.commit()
                except Exception:
                    pass
            return mirror_res

    url = "https://search.webuy.io/1/indexes/prod_cex_es/query"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://es.webuy.com",
        "Referer": "https://es.webuy.com/"
    }
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    try:
        r = requests.post(
            url,
            json={"query": clean_q, "hitsPerPage": 18},
            headers=headers,
            proxies=proxies,
            impersonate="safari17_0",
            timeout=10
        )
        if r.status_code != 200:
            return None

        hits = r.json().get("hits", [])
        if not hits:
            return None

        q_lower = clean_q.lower()
        q_wants_accessory = any(ak in q_lower for ak in ACCESSORY_KEYWORDS_CEX) and radar_id != "simracing_wheels"
        full_ctx = f"{title_context} {desc_context}"

        valid_boxes = []
        for h in hits:
            cash = float(h.get("cashPriceCalculated") or h.get("cashBuyPrice") or h.get("cashPrice") or 0)
            voucher = float(h.get("exchangePriceCalculated") or h.get("exchangePrice") or 0)
            sell = float(h.get("sellPrice") or 0)
            bname = str(h.get("boxName") or "")
            cat = str(h.get("categoryFriendlyName") or "")
            if cash <= 0 or sell <= 0:
                continue

            bname_l = bname.lower()
            if not q_wants_accessory and radar_id != "simracing_wheels":
                bname_without_sin = re.sub(r"sin\s+(cargador|caja|lapiz|manual|dock|lector)", "", bname_l)
                if any(ak in bname_without_sin for ak in ACCESSORY_KEYWORDS_CEX) and "accesorio" in cat.lower():
                    continue
                if reference_price >= 30 and sell < 25:
                    continue

            if not _box_matches_query_and_radar(
                bname, cat, clean_q, target=target, context_text=full_ctx, includes_lens=includes_lens
            ):
                continue

            valid_boxes.append({
                "box_name": bname,
                "cash": cash,
                "voucher": voucher,
                "sell": sell,
                "box_id": str(h.get("boxId") or ""),
                "category": cat,
                "in_stock_online": bool(h.get("inStockOnline"))
            })

        if not valid_boxes:
            return None

        if return_all_variants:
            return valid_boxes[:8]

        top_relevant = valid_boxes[:10]
        best_box = _score_cex_candidate(
            top_relevant, clean_q, radar_id=radar_id, full_ctx=full_ctx, includes_box=includes_box
        )
        if not best_box:
            best_box = top_relevant[0]

        if conn:
            try:
                c = conn.cursor()
                c.execute(
                    """INSERT OR REPLACE INTO cex_cache
                       (search_query, box_name, cash_price, voucher_price, sell_price, box_id, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (cache_lookup_key, best_box["box_name"], best_box["cash"], best_box["voucher"], best_box["sell"], best_box["box_id"])
                )
                conn.commit()
            except Exception:
                pass

        best_box["from_cache"] = False
        return best_box
    except Exception:
        return None


def get_cex_Cached_or_live(
    conn, search_query, reference_price=0, return_all_variants=False,
    target=None, title_context="", desc_context="", includes_box=False, includes_lens=True,
    radar_id=""
):
    """
    Alias unificado y retrocompatible para consultar CeX con:
    1) Hit exacto en cex_cache (0ms)
    2) Búsqueda en espejo local cex_catalog_mirror con _box_matches_query_and_radar y _score_cex_candidate (0ms)
    3) Fallback a red en vivo con fetch_cex_live (~150ms)
    """
    t = target if target is not None else ({"id": radar_id} if radar_id else None)
    rid = (t or {}).get("id", "generic") if isinstance(t, dict) else (radar_id or "generic")

    if not search_query:
        return None

    clean_q = re.sub(r"\(.*?\)", "", search_query).strip()
    if rid == "simracing_wheels" or any(w in clean_q.lower() for w in ("g29", "g920", "g923", "t300")):
        clean_q = re.sub(r"\b(volante|pedales|y|con)\b", "", clean_q, flags=re.IGNORECASE)
        clean_q = re.sub(r"\s+", " ", clean_q).strip()

    if not clean_q:
        return None

    cache_lookup_key = f"v43:{rid}:{clean_q.lower()}:b{int(bool(includes_box))}:l{int(bool(includes_lens))}"

    # 1. Comprobar si ya existe en cex_cache exacta
    if conn and not return_all_variants:
        try:
            c = conn.cursor()
            c.execute(
                "SELECT box_name, cash_price, voucher_price, sell_price, box_id FROM cex_cache WHERE search_query = ?",
                (cache_lookup_key,)
            )
            row = c.fetchone()
            if row and row[1] > 0:
                return {
                    "box_name": row[0],
                    "cash": float(row[1]),
                    "voucher": float(row[2]),
                    "sell": float(row[3]),
                    "box_id": row[4] or "",
                    "from_cache": True
                }
        except Exception:
            pass

    # 2. Buscar primero en espejo local cex_catalog_mirror (<1ms)
    if conn:
        mirror_box = lookup_cex_catalog_mirror(
            conn=conn,
            clean_q=clean_q,
            target=t,
            title_context=title_context,
            desc_context=desc_context,
            includes_box=includes_box,
            includes_lens=includes_lens,
            return_all_variants=return_all_variants,
            radar_id=rid
        )
        if mirror_box:
            if not return_all_variants and isinstance(mirror_box, dict) and mirror_box.get("cash", 0) > 0:
                try:
                    c = conn.cursor()
                    c.execute(
                        """INSERT OR REPLACE INTO cex_cache
                           (search_query, box_name, cash_price, voucher_price, sell_price, box_id, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (cache_lookup_key, mirror_box["box_name"], mirror_box["cash"], mirror_box["voucher"], mirror_box["sell"], mirror_box.get("box_id", ""))
                    )
                    conn.commit()
                except Exception:
                    pass
            return mirror_box

    # 3. Fallback a fetch_cex_live en vivo
    return fetch_cex_live(
        conn=conn,
        search_query=search_query,
        reference_price=reference_price,
        return_all_variants=return_all_variants,
        target=t,
        title_context=title_context,
        desc_context=desc_context,
        includes_box=includes_box,
        includes_lens=includes_lens
    )


get_cex_cached_or_live = get_cex_Cached_or_live


def evaluate_bundle_cex_bonus(conn, bundled_queries: list, radar_id="pokemon_ds_3ds_gba") -> dict:
    """
    Consulta CeX España (con caché SQLite) para valorar videojuegos físicos o mandos extra detectados en un lote.
    Limita la búsqueda a un máximo de 3 extras para proteger la latencia del escáner.
    Devuelve un diccionario estructurado con:
      - 'extra_cash': float (suma en euros de recompra en efectivo CeX)
      - 'extra_voucher': float (suma en euros de vale de intercambio CeX)
      - 'matched_extras': list de dicts con box_name, cash, voucher, sell y query.
    """
    if not bundled_queries or not isinstance(bundled_queries, list):
        return {"extra_cash": 0.0, "extra_voucher": 0.0, "matched_extras": []}

    total_extra_cash = 0.0
    total_extra_voucher = 0.0
    matched_extras = []

    # Proteger latencia: evaluar un máximo de 3 extras
    queries_to_eval = [str(q).strip() for q in bundled_queries if str(q).strip()][:3]

    for game_q in queries_to_eval:
        try:
            cex_res = get_cex_Cached_or_live(conn, game_q, radar_id=radar_id)
            if cex_res and isinstance(cex_res, dict):
                cash = float(cex_res.get("cash", 0.0) or 0.0)
                voucher = float(cex_res.get("voucher", 0.0) or 0.0)
                box_name = str(cex_res.get("box_name") or game_q)
                if cash > 0 or voucher > 0:
                    total_extra_cash += cash
                    total_extra_voucher += voucher
                    matched_extras.append({
                        "query": game_q,
                        "box_name": box_name,
                        "cash": cash,
                        "voucher": voucher,
                        "sell": float(cex_res.get("sell", 0.0) or 0.0)
                    })
        except Exception as e:
            print(f"[WARN] Error valorando extra CeX '{game_q}': {e}")

    return {
        "extra_cash": round(total_extra_cash, 2),
        "extra_voucher": round(total_extra_voucher, 2),
        "matched_extras": matched_extras
    }


# ==================== SCRAPING MULTI-MERCADO ====================

class MilanunciosSessionManager:
    PROFILES = ["safari17_0", "safari18_0", "safari15_5", "safari15_3", "safari18_0_ios"]

    def __init__(self):
        self._lock = threading.Lock()
        self.session = None
        self.last_init = 0
        self.last_request_time = 0
        self.profile_idx = 0
        self.req_count = 0

    def pace_request(self, min_delay=3.0):
        with self._lock:
            now = time.time()
            elapsed = now - self.last_request_time
            if elapsed < min_delay:
                time.sleep(min_delay - elapsed + random.uniform(0.3, 0.9))
            self.last_request_time = time.time()

    def get_session(self, rotate=False):
        with self._lock:
            now = time.time()
            if rotate or self.session is None or (now - self.last_init) > 300 or self.req_count >= 20:
                if rotate or self.req_count >= 20:
                    self.profile_idx = (self.profile_idx + 1) % len(self.PROFILES)
                profile = self.PROFILES[self.profile_idx]
                proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
                self.session = requests.Session(impersonate=profile, proxies=proxies)
                self.last_init = now
                self.req_count = 0
            self.req_count += 1
            return self.session


_milanuncios_mgr = MilanunciosSessionManager()


def fetch_milanuncios(rotator, query_text, min_price, max_price):
    clean_q = re.sub(r'[^a-zA-Z0-9]+', '-', query_text.strip().lower()).strip('-')
    params = {
        "orden": "nuevos",
        "desde": str(int(min_price)),
        "hasta": str(int(max_price))
    }
    if clean_q:
        url = f"https://www.milanuncios.com/anuncios/{clean_q}.htm?{urllib.parse.urlencode(params)}"
    else:
        params["s"] = query_text
        url = f"https://www.milanuncios.com/anuncios/?{urllib.parse.urlencode(params)}"
    
    try:
        r = None
        for attempt in range(2):
            s = _milanuncios_mgr.get_session(rotate=(attempt > 0))
            _milanuncios_mgr.pace_request(min_delay=3.0)
            t0 = time.time()
            try:
                r = s.get(url, timeout=15)
                default_rate_limiter.record_request("MILANUNCIOS", time.time() - t0, r.status_code)
            except Exception:
                if attempt == 0:
                    continue
                return []

            # P7-5: Detección WAF / PerimeterX / DataDome (HTTP 405, 403, 429 o desafío CAPTCHA "Pardon Our Interruption")
            if r.status_code in (403, 405, 429) or "pardon our interruption" in r.text.lower():
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise WafBlockedError(f"Milanuncios WAF/Anti-Bot detectado (HTTP {r.status_code}, len={len(r.text)})")
            break

        if not r or r.status_code != 200:
            return []

        m = re.search(r'window\.__INITIAL_PROPS__\s*=\s*JSON\.parse\("((?:\\.|[^"\\])*)"\)', r.text)
        if not m:
            default_parser_tracker.record_empty("MILANUNCIOS", query_text, reason=f"HTML 200 ({len(r.text)} bytes) sin coincidencia de __INITIAL_PROPS__")
            return []

        raw_str = json.loads('"' + m.group(1) + '"')
        data = json.loads(raw_str)
        ads = data.get("adListPagination", {}).get("adList", {}).get("ads", [])
        results = []

        for ad in ads:
            ad_id = str(ad.get("id") or "")
            res_state = str(ad.get("isReserved") or "").upper()
            if not ad_id or res_state in ("TRUE", "RESERVED", "1"):
                continue

            seller_type = str(ad.get("sellerType") or ad.get("authorType") or "").lower()
            if "prof" in seller_type:
                continue

            p_val = ad.get("price", {}).get("cashPrice", {}).get("value")
            if p_val is None:
                continue
            price = float(p_val)
            if price < min_price or price > max_price:
                continue

            title = str(ad.get("title") or "").strip()
            desc = str(ad.get("description") or "").strip()
            rel_url = str(ad.get("url") or "")
            item_url = f"https://www.milanuncios.com{rel_url}" if rel_url.startswith("/") else rel_url

            loc = ad.get("location", {})
            city = (loc.get("city", {}) or {}).get("name") or (loc.get("province", {}) or {}).get("name") or "España"
            prov = (loc.get("province", {}) or {}).get("name") or ""
            is_local = "sevilla" in f"{city} {prov}".lower() or any(k in city.lower() for k in LOCAL_KEYWORDS)

            images = ad.get("images") or []
            img_urls = []
            for im in images:
                if isinstance(im, str):
                    img_urls.append(im)
                elif isinstance(im, dict):
                    u = im.get("url") or im.get("src")
                    if u:
                        img_urls.append(u)

            fees = 0.0 if is_local else 5.95
            user_obj = ad.get("user") or ad.get("author") or ad.get("seller") or {}
            user_id = str(user_obj.get("id") or ad.get("userId") or ad.get("authorId") or "")
            user_name = str(user_obj.get("name") or user_obj.get("nick") or ad.get("authorName") or "")
            seller_dict = {
                "name": user_name or "Particular Milanuncios",
                "id": user_id,
                "age_days": None,
                "listings_count": None,
                "rating": None,
                "sales": None
            }
            results.append({
                "platform": "MILANUNCIOS",
                "id": ad_id,
                "title": title,
                "description": desc,
                "price": price,
                "landed_cost": round(price + fees, 2),
                "fees": fees,
                "url": item_url,
                "checkout_url": item_url,
                "images": img_urls,
                "city": f"{city} ({prov})" if prov and prov != city else city,
                "is_local": is_local,
                "shipping_label": "📦 Milanuncios Express",
                "seller": seller_dict
            })
        return results
    except WafBlockedError:
        raise
    except Exception as e:
        print(f"[WARN] Error en Milanuncios '{query_text}': {e}")
        default_parser_tracker.record_parser_error("MILANUNCIOS", query_text, str(e))
        return []


def fetch_cashconverters(rotator, query_text, min_price, max_price):
    params = {"q": query_text, "srule": "newest"}
    url = f"https://www.cashconverters.es/es/es/search/?{urllib.parse.urlencode(params)}"
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
    profile = rotator.get_chrome_profile()

    headers = {
        "User-Agent": profile["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9"
    }
    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
        default_rate_limiter.record_request("CASHCONVERTERS", time.time() - t0, r.status_code)
        if r.status_code != 200:
            return []

        pattern = re.compile(
            r'<div class="product-tile[^"]*"[^>]*data-pid="(?P<pid>[^"]+)"[^>]*'
            r'data-product-datalayer="(?P<dl>[^"]+)"[^>]*>.*?'
            r'<a href="(?P<link>/es/es/comprar/[^"]+)"[^>]*>\s*'
            r'<img[^>]+src="(?P<img>https://images\.cashconverters\.es/[^"]+)"',
            re.DOTALL
        )
        results = []
        seen_pids = set()

        for m in pattern.finditer(r.text):
            pid = m.group("pid")
            if pid in seen_pids:
                continue
            seen_pids.add(pid)

            try:
                dl_raw = html.unescape(m.group("dl"))
                dl = json.loads(dl_raw)
            except Exception:
                continue

            price = float(dl.get("price") or 0)
            if price < min_price or price > max_price:
                continue

            title = str(dl.get("name") or "").strip().title()
            cond = str(dl.get("variant") or "Bueno").strip()
            cat = str(dl.get("category") or "").strip()
            link = m.group("link")
            full_url = f"https://www.cashconverters.es{link}"
            img_small = m.group("img")
            img_large = img_small.replace("?d=small", "?d=large")

            fees = 0.0 if price >= 30 else 2.95
            desc = f"Tienda Cash Converters (Estado: {cond} | Cat: {cat}). Incluye 3 años de garantía oficial."

            results.append({
                "platform": "CASHCONVERTERS",
                "id": pid,
                "title": f"{title} ({cond})",
                "description": desc,
                "price": price,
                "landed_cost": round(price + fees, 2),
                "fees": fees,
                "url": full_url,
                "checkout_url": full_url,
                "images": [img_large],
                "city": "Cash Converters ES (3 Años Garantía)",
                "is_local": False,
                "shipping_label": "🏪 Compra Directa Web + Garantía",
                "seller": {
                    "name": "Cash Converters España",
                    "id": "cash_converters_official",
                    "age_days": 3650,
                    "listings_count": 9999,
                    "rating": 5.0,
                    "sales": 9999
                }
            })
        return results
    except Exception as e:
        print(f"[WARN] Error en CashConverters '{query_text}': {e}")
        default_parser_tracker.record_parser_error("CASHCONVERTERS", query_text, str(e))
        return []


def fetch_todocoleccion(rotator, query_text, min_price, max_price):
    params = {"from": "top", "bu": query_text, "O": "r"}
    url = f"https://www.todocoleccion.net/buscador?{urllib.parse.urlencode(params)}"
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    t0 = time.time()
    try:
        r = requests.get(
            url,
            proxies=proxies,
            impersonate=rotator.get_safari_impersonation(),
            timeout=15
        )
        default_rate_limiter.record_request("TODOCOLECCION", time.time() - t0, r.status_code)
        if r.status_code != 200:
            return []

        pattern = re.compile(
            r'<a\s+id="lot-title-(?P<id>\d+)"\s+href="(?P<link>[^"]+)"[^>]*'
            r'data-image-url="(?P<img>[^"]+)"[^>]*>\s*(?P<title>.*?)\s*</a>.*?'
            r'<span class="card-price[^"]*">\s*(?P<price>[\d\.,]+)\s*€',
            re.DOTALL
        )
        results = []
        for m in pattern.finditer(r.text):
            lot_id = m.group("id")
            raw_price = m.group("price").replace(".", "").replace(",", ".")
            try:
                price = float(raw_price)
            except Exception:
                continue

            if price < min_price or price > max_price:
                continue

            title = html.unescape(re.sub(r"<[^>]+>", "", m.group("title"))).strip()
            link = html.unescape(m.group("link"))
            full_url = f"https://www.todocoleccion.net{link}" if link.startswith("/") else link
            img_url = html.unescape(m.group("img")).split("?")[0]
            fees = 4.50

            results.append({
                "platform": "TODOCOLECCION",
                "id": lot_id,
                "title": title,
                "description": f"Lote en TodoColección: {title}",
                "price": price,
                "landed_cost": round(price + fees, 2),
                "fees": fees,
                "url": full_url,
                "checkout_url": full_url,
                "images": [img_url] if img_url else [],
                "city": "TodoColección España",
                "is_local": False,
                "shipping_label": "📦 Envío Correos TC",
                "seller": None
            })
        return results
    except Exception as e:
        print(f"[WARN] Error en TodoColección '{query_text}': {e}")
        default_parser_tracker.record_parser_error("TODOCOLECCION", query_text, str(e))
        return []


class EbaySessionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.session = None
        self.last_init = 0

    def get_session(self):
        with self._lock:
            now = time.time()
            if self.session is None or (now - self.last_init) > 900:
                proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
                self.session = requests.Session(impersonate="safari17_0", proxies=proxies)
                self.last_init = now
                try:
                    self.session.get("https://www.ebay.es/", timeout=10)
                except Exception:
                    pass
            return self.session


_ebay_mgr = EbaySessionManager()


def fetch_ebay_es(rotator, query_text, min_price, max_price):
    encoded_q = urllib.parse.quote_plus(query_text)
    url = f"https://www.ebay.es/sch/i.html?_nkw={encoded_q}&LH_BIN=1&_sop=10"
    try:
        r = None
        for attempt in range(2):
            s = _ebay_mgr.get_session()
            t0 = time.time()
            try:
                r = s.get(url, timeout=15)
                default_rate_limiter.record_request("EBAY", time.time() - t0, r.status_code)
                if r.status_code == 200 and len(r.text) >= 5000:
                    break
                if attempt == 0:
                    time.sleep(1.0)
                    continue
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return []

        if not r or r.status_code != 200 or len(r.text) < 5000:
            return []

        card_pattern = re.compile(
            r'data-listingid=["\']?(?P<id>\d{9,15})["\']?[^>]*>(?P<body>.*?)</li>',
            re.DOTALL
        )
        results = []
        seen_ids = set()

        for m in card_pattern.finditer(r.text):
            item_id = m.group("id")
            if item_id in seen_ids or item_id.startswith("2500"):
                continue
            seen_ids.add(item_id)
            body = m.group("body")

            m_title = re.search(r'alt="([^"]{6,200})"', body)
            if not m_title:
                m_title = re.search(r'class=["\']?s-card__title["\']?[^>]*>.*?<span[^>]*>([^<]+)</span>', body, re.DOTALL)
            if not m_title:
                continue
            title = html.unescape(m_title.group(1)).strip()
            if "shop on ebay" in title.lower():
                continue

            if re.search(r'\b(?:\d+\s*(?:pujas?|bids?)|subasta|auction)\b', body, re.IGNORECASE):
                continue

            non_eu_hints = ["japón", "japan", "estados unidos", "united states", "china", "hong kong", "taiwan"]
            if any(h in body.lower() for h in non_eu_hints):
                continue

            m_price = re.search(r'(\d{1,4}[\.,]\d{2})\s*(?:EUR|€)', body)
            if not m_price:
                continue
            try:
                price = float(m_price.group(1).replace(".", "").replace(",", ".") if "," in m_price.group(1) else m_price.group(1))
            except Exception:
                continue

            if price < min_price or price > max_price:
                continue

            m_img = re.search(r'(https://i\.ebayimg\.com/images/g/[^"\s>]+)', body)
            img_url = m_img.group(1) if m_img else ""

            fees = 0.0
            m_ship = re.search(r'\+\s*(\d{1,3}[\.,]\d{2})\s*(?:EUR|€)', body)
            if m_ship:
                try:
                    fees = float(m_ship.group(1).replace(",", "."))
                except Exception:
                    fees = 4.95

            item_url = f"https://www.ebay.es/itm/{item_id}"

            m_usr = re.search(r'https://www\.ebay\.es/usr/([^"?\s/]+)', body)
            m_feed = re.search(r'\(([\d\.,]+)\)\s*([\d\.,]+)%', body)
            m_seller_text = re.search(r'class=["\']?s-item__seller-info[^"\']*["\']?[^>]*>(.*?)</span>', body, re.DOTALL)

            seller_name = m_usr.group(1) if m_usr else ""
            seller_sales = None
            seller_rating = None
            if m_feed:
                try:
                    seller_sales = int(m_feed.group(1).replace(".", "").replace(",", ""))
                    seller_rating = float(m_feed.group(2).replace(",", "."))
                except Exception:
                    pass
            elif m_seller_text:
                txt = m_seller_text.group(1)
                m_f2 = re.search(r'\(([\d\.,]+)\)\s*([\d\.,]+)%', txt)
                if m_f2:
                    try:
                        seller_sales = int(m_f2.group(1).replace(".", "").replace(",", ""))
                        seller_rating = float(m_f2.group(2).replace(",", "."))
                    except Exception:
                        pass
                parts = txt.split()
                if parts and not seller_name:
                    seller_name = parts[0]

            seller_dict = {
                "name": seller_name or "Vendedor eBay",
                "id": seller_name or "",
                "age_days": None,
                "listings_count": None,
                "rating": seller_rating,
                "sales": seller_sales
            } if (seller_name or seller_sales is not None or seller_rating is not None) else None

            results.append({
                "platform": "EBAY",
                "id": item_id,
                "title": title,
                "description": f"Anuncio ¡Cómpralo Ya! en eBay España/UE: {title}",
                "price": price,
                "landed_cost": round(price + fees, 2),
                "fees": fees,
                "url": item_url,
                "checkout_url": item_url,
                "images": [img_url] if img_url else [],
                "city": "eBay ES/UE",
                "is_local": False,
                "shipping_label": "🌐 Envío eBay",
                "seller": seller_dict
            })
        return results
    except Exception as e:
        print(f"[WARN] Error en eBay ES '{query_text}': {e}")
        default_parser_tracker.record_parser_error("EBAY", query_text, str(e))
        return []


# ==================== DESCARGA Y PROCESADO DE IMÁGENES ====================

def _fetch_single_image(url, imp="safari17_0", proxies=None):
    if not url:
        return None
    try:
        resp = requests.get(url, proxies=proxies, impersonate=imp, timeout=5)
        if resp.status_code == 200 and resp.content and len(resp.content) > 1500:
            return resp.content
    except Exception:
        pass
    return None


class CompressedImageBytes(bytes):
    """
    Subclase de bytes optimizada en memoria para tuberías de IA y almacenamiento (v5.2).
    Mantiene compatibilidad total con operaciones de bytes (len, write, BytesIO, index),
    soporta desempaquetado de tupla (bytes, mime_type) y expone metadatos/acceso dict.
    """
    mime_type = "image/jpeg"

    def __new__(cls, data: bytes, mime_type: str = "image/jpeg"):
        b = super().__new__(cls, data)
        b.mime_type = mime_type
        return b

    def __iter__(self):
        yield bytes(self)
        yield self.mime_type

    def __getitem__(self, key):
        if key == "mime_type":
            return self.mime_type
        if key in ("bytes", "data"):
            return bytes(self)
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key == "mime_type":
            return self.mime_type
        if key in ("bytes", "data"):
            return bytes(self)
        return default


def compress_image_to_jpeg_640(img_bytes, max_dim=384, quality=76, max_side=None):
    """
    Redimensiona y comprime una imagen en memoria a un máximo de 384x384 JPEG Q=76 (TASK-04).
    En Gemini Vision, dimensiones <=384px consumen exactamente 1 tile = 258 tokens fijos.
    Fallback a los bytes originales si PIL falla.
    """
    if not img_bytes:
        return None
    effective_max = max_side if max_side is not None else max_dim
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            img = img.convert("RGB")
            if img.width > effective_max or img.height > effective_max:
                img.thumbnail((effective_max, effective_max), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return CompressedImageBytes(out.getvalue(), "image/jpeg")
    except Exception:
        if isinstance(img_bytes, bytes):
            return CompressedImageBytes(img_bytes, "image/jpeg")
        return img_bytes


def download_item_images(image_urls, rotator=None, max_images=3, force_safari=False):
    if not image_urls:
        return []
    urls = [u for u in image_urls[:max_images] if u]
    if not urls:
        return []

    imp = "safari17_0"
    if not force_safari and rotator:
        try:
            imp = rotator.get_chrome_profile().get("impersonate", "safari17_0")
        except Exception:
            imp = "safari17_0"
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    raw_results = []
    with ThreadPoolExecutor(max_workers=min(len(urls), 3)) as executor:
        futures = [executor.submit(_fetch_single_image, u, imp, proxies) for u in urls]
        done, not_done = concurrent.futures.wait(futures, timeout=8.0)
        for fut in not_done:
            fut.cancel()
        for fut in futures:
            if fut in done:
                try:
                    if not fut.cancelled() and fut.exception() is None:
                        res = fut.result()
                        if res is not None:
                            raw_results.append(res)
                except Exception:
                    pass

    compressed_results = []
    for b in raw_results:
        if b is not None:
            comp = compress_image_to_jpeg_640(b)
            if comp:
                compressed_results.append(comp)

    return compressed_results


def prepare_image_for_ai(img_bytes, max_dim=384, quality=76):
    if not img_bytes:
        return None
    if isinstance(img_bytes, CompressedImageBytes) or getattr(img_bytes, "mime_type", None) == "image/jpeg":
        raw = bytes(img_bytes)
        if len(raw) > 0:
            return base64.b64encode(raw).decode("ascii")
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            img = img.convert("RGB")
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:
        if isinstance(img_bytes, bytes):
            try:
                return base64.b64encode(img_bytes).decode("ascii")
            except Exception:
                pass
        return None


def enhance_and_clean_image(img_bytes):
    with Image.open(io.BytesIO(img_bytes)) as img:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img = img.convert("RGB")
        w, h = img.size
        crop_x, crop_y = int(w * 0.025), int(h * 0.025)
        if w > 240 and h > 240:
            img = img.crop((crop_x, crop_y, w - crop_x, h - crop_y))
            img = img.resize((w, h), Image.Resampling.LANCZOS)

        img = ImageOps.autocontrast(img, cutoff=0.8)
        img = ImageEnhance.Brightness(img).enhance(1.04)
        img = ImageEnhance.Contrast(img).enhance(1.06)
        img = ImageEnhance.Color(img).enhance(1.06)
        img = ImageEnhance.Sharpness(img).enhance(1.20)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue()


# ==================== PUERTA 1: CRIBA POR TEXTO (FLASH-LITE) ====================

def prescreen_item_text(platform, title, description, price, landed_cost, target, conn=None, health_monitor=None):
    """
    PUERTA 1: Criba ultra-ligera solo por texto con Gemini 2.5 Flash-Lite (0 fotos, thinkingBudget 0).
    Rechaza anuncios que el propio texto delata como accesorios, cajas vacías, modelos erróneos o estafas.
    Retorna: (decision, reject_reason, confidence, from_cache)
      decision: 'reject' | 'proceed' | 'unsure'
    """
    if not GEMINI_API_KEY and not OPENROUTER_API_KEY:
        return "proceed", "", "alta", False

    radar_id = target.get("id", target.get("name", "")) if isinstance(target, dict) else ""
    cache_key = build_robust_cache_key(radar_id, title, description, price, prefix="prescreen_v1")

    if conn:
        try:
            c = conn.cursor()
            c.execute("SELECT decision, reject_reason, confidence FROM ai_prescreen_cache WHERE cache_key = ?", (cache_key,))
            row = c.fetchone()
            if row:
                return row[0], row[1], row[2], True
        except Exception:
            pass

    clean_title_str = html.unescape(title or "").strip()[:90]
    clean_desc_raw = html.unescape(description or "").strip()
    clean_desc_raw = re.sub(r"https?://\S+", "", clean_desc_raw)
    clean_desc = re.sub(r"\s+", " ", clean_desc_raw).strip()[:250]

    target_name = (target.get("name", "") if isinstance(target, dict) else "")[:50]
    guide = (target.get("ai_valuation_guide", "") if isinstance(target, dict) else "")[:250]

    prompt = (
        f"Filtro rápido arbitraje ({platform}).\n"
        f"Radar: {target_name} | Título: {clean_title_str} | Precio: {price:.0f}€ (Total casa: {landed_cost:.0f}€)\n"
        f"Desc: {clean_desc or 'Sin descripción'}\n"
        f"Guía: {guide}\n\n"
        f"INSTRUCCIONES: Si el texto indica que NO es el producto buscado (caja vacía, funda, manual, cables, "
        f"juego suelto, pantalla rota, para reparar/piezas/bloqueo iCloud/drift, pago externo bizum/transferencia/wasap), "
        f"pon decision='reject'. Si es funcional y completo, pon decision='proceed'. Ante duda, 'unsure'.\n"
        f"IMPORTANTE: Cero texto conversacional ni explicaciones. Responde ÚNICAMENTE un JSON válido:\n"
        f'{{"decision": "reject"|"proceed"|"unsure", "reject_reason": "", "confidence": "alta"|"media"|"baja"}}'
    )

    res_obtained = False
    decision = "proceed"
    reject_reason = ""
    confidence = "media"

    try:
        res = ai_gateway_call(
            prompt=prompt,
            candidate_models=["gemini-3.5-flash-lite", "gemini-flash-lite-latest"],
            openrouter_models=["google/gemini-2.5-flash-lite", "openai/gpt-4o-mini"],
            max_tokens=150,
            temperature=0.1,
            conn=conn,
            platform=platform,
            radar_id=radar_id,
            call_type="prescreen",
            timeout=12,
            enforce_budget=True,
            schema_defaults=None
        )
        if res and isinstance(res, dict):
            decision = str(res.get("decision", "proceed")).lower()
            reject_reason = str(res.get("reject_reason", ""))
            confidence = str(res.get("confidence", "media")).lower()
            res_obtained = True
    except BudgetExceededError as e:
        print(f"[PRESCREEN] Cortafuegos IA diario activo: {e}. Fail-open.")
        return "proceed", "budget_exceeded_fail_open", "baja", False
    except Exception as e:
        print(f"[PRESCREEN] Error en llamada ai_gateway_call ({platform}): {e}")

    if not res_obtained:
        return "proceed", "ai_unavailable_fail_open", "baja", False

    if decision not in ("reject", "proceed", "unsure"):
        decision = "proceed"

    if conn and res_obtained:
        try:
            c = conn.cursor()
            c.execute(
                """INSERT OR REPLACE INTO ai_prescreen_cache (cache_key, decision, reject_reason, confidence, evaluated_at)
                   VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (cache_key, decision, reject_reason, confidence)
            )
            conn.commit()
        except Exception:
            pass

    return decision, reject_reason, confidence, False


def pre_gate_cex_offer_margin(conn, item, target, config=None):
    """
    Pre-Gating Matemático CeX (TASK-03) antes de Puerta 1 / Puerta 2.
    Descarta en <1ms a coste 0.00€ artículos en zona de oferta (+30%) cuyo margen sea matemáticamente imposible.
    Devuelve: (proceed: bool, reason: str)
    """
    if not isinstance(item, dict) or not isinstance(target, dict):
        return True, "invalid_item_or_target"

    raw_price = item.get("price")
    if isinstance(raw_price, dict):
        raw_price = raw_price.get("amount") or 0.0
    try:
        price = float(raw_price or 0.0)
    except (ValueError, TypeError):
        price = 0.0
    direct_max = float(target.get("direct_buy_max_price") or target.get("max_price") or 0.0)

    # 1. Si está en zona de compra directa (<= direct_buy_max_price), proceder siempre
    if price <= direct_max or price <= 0.0:
        return True, "direct_zone"

    title = str(item.get("title") or "")
    desc = str(item.get("description") or item.get("desc") or "")
    text = f"{title} {desc}".lower()

    # 2. Si el anuncio tiene potencial de lote o juegos extras, permitir análisis
    bundle_markers = ("pack", "lote", "bundle", "+ ", "juegos", "mandos", "juego", "mando")
    if any(m in text for m in bundle_markers):
        return True, "bundle_potential"

    # 3. Consulta rápida al espejo local SQLite CeX (<1ms)
    if not conn:
        return True, "no_db_conn"

    try:
        mirror_match = lookup_cex_catalog_mirror(conn, title, target=target)
        if not mirror_match:
            return True, "no_mirror_match"

        cex_cash = float(mirror_match.get("cash") or mirror_match.get("cash_price") or 0.0)
        cex_sell = float(mirror_match.get("sell") or mirror_match.get("sell_price") or 0.0)

        if cex_cash <= 0.0 and cex_sell <= 0.0:
            return True, "cex_prices_zero"

        # 4. Cálculo del precio mínimo realista tras descuento por regateo + landed cost
        max_discount_pct = float((config or {}).get("max_haggle_discount_pct", 0.35))
        min_achievable_buy = price * (1.0 - max_discount_pct)
        min_achievable_landed = float(estimate_landed_cost(min_achievable_buy))

        # 5. Techo de salida: CeX cash (o reventa rápida si no hay precio en efectivo)
        max_exit = cex_cash if cex_cash > 0.0 else (cex_sell * 0.85)
        min_profit = float(target.get("min_profit", 15.0))

        if (max_exit - min_achievable_landed) < (min_profit * 0.70):
            return False, f"pre_gate_margin_impossible (ask={price:.1f}€, min_haggle={min_achievable_landed:.1f}€, cex_cash={cex_cash:.1f}€, max_exit={max_exit:.1f}€)"

        return True, "margin_viable"
    except Exception as e:
        return True, f"pre_gate_exception: {e}"


# ==================== INTELIGENCIA DE MERCADO LOCAL & REPUTACIÓN ====================

def record_market_observation(radar_name, platform, price, conn=None):
    """Registra una observación de precio de mercado para estadísticas de mediana y desviación típica."""
    if not radar_name or not price or float(price) <= 0:
        return
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        c = conn.cursor()
        c.execute(
            """INSERT INTO price_history (radar_name, platform, price, recorded_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            (str(radar_name), str(platform).upper(), float(price))
        )
        conn.commit()
    except Exception as e:
        print(f"[WARN] Error registrando price_history: {e}")
    finally:
        if should_close:
            conn.close()


def get_local_market_stats(radar_name, days=14, conn=None):
    """
    Calcula mediana, media, desviación típica y conteo de precios observados en los últimos 'days' días para un radar.
    Devuelve: {"median": float, "mean": float, "std": float, "count": int}
    """
    if not radar_name:
        return {"median": 0.0, "mean": 0.0, "std": 0.0, "count": 0}
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        c = conn.cursor()
        c.execute(
            """SELECT price FROM price_history
               WHERE radar_name = ? AND recorded_at >= datetime('now', ?)
               ORDER BY price ASC""",
            (str(radar_name), f"-{int(days)} days")
        )
        rows = [float(r[0]) for r in c.fetchall() if r[0] is not None and float(r[0]) > 0]
        n = len(rows)
        if n == 0:
            return {"median": 0.0, "mean": 0.0, "std": 0.0, "count": 0}

        mean_val = sum(rows) / n
        if n % 2 == 1:
            median_val = rows[n // 2]
        else:
            median_val = (rows[n // 2 - 1] + rows[n // 2]) / 2.0

        if n > 1:
            variance = sum((x - mean_val) ** 2 for x in rows) / (n - 1)
            std_val = math.sqrt(variance)
        else:
            std_val = 0.0

        return {
            "median": round(median_val, 2),
            "mean": round(mean_val, 2),
            "std": round(std_val, 2),
            "count": n
        }
    except Exception as e:
        print(f"[WARN] Error calculando estadísticas locales de mercado: {e}")
        return {"median": 0.0, "mean": 0.0, "std": 0.0, "count": 0}
    finally:
        if should_close:
            conn.close()


def update_seller_reputation(seller_id, platform, risk_score=0.0, is_fraud=False, conn=None):
    """
    Actualiza el perfil y reputación del vendedor en la tabla seller_reputation.
    """
    if not seller_id:
        return
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    try:
        c = conn.cursor()
        sid = str(seller_id).strip()
        plat = str(platform).upper()
        c.execute("SELECT total_seen, fraud_flags, avg_risk_score FROM seller_reputation WHERE seller_id = ?", (sid,))
        row = c.fetchone()
        if row:
            total_seen, fraud_flags, old_avg = row
            new_seen = total_seen + 1
            new_flags = fraud_flags + (1 if is_fraud else 0)
            new_avg = round(((old_avg * total_seen) + float(risk_score)) / new_seen, 2)
            c.execute(
                """UPDATE seller_reputation
                   SET total_seen = ?, fraud_flags = ?, avg_risk_score = ?, last_seen = CURRENT_TIMESTAMP
                   WHERE seller_id = ?""",
                (new_seen, new_flags, new_avg, sid)
            )
        else:
            c.execute(
                """INSERT INTO seller_reputation (seller_id, platform, total_seen, fraud_flags, avg_risk_score, last_seen)
                   VALUES (?, ?, 1, ?, ?, CURRENT_TIMESTAMP)""",
                (sid, plat, (1 if is_fraud else 0), round(float(risk_score), 2))
            )
        conn.commit()
    except Exception as e:
        print(f"[WARN] Error actualizando seller_reputation: {e}")
    finally:
        if should_close:
            conn.close()


# ==================== AUDITORÍA MULTIMODAL GEMINI ====================

class ModelPair(str):
    def __new__(cls, gemini_model, openrouter_model):
        obj = super().__new__(cls, gemini_model)
        obj.gemini_model = gemini_model
        obj.openrouter_model = openrouter_model
        return obj

    def __iter__(self):
        yield self.gemini_model
        yield self.openrouter_model

    def __getitem__(self, item):
        if isinstance(item, int):
            return [self.gemini_model, self.openrouter_model][item]
        return super().__getitem__(item)


def select_multimodal_model(item_or_price=None, target_or_title=None, desc="", target=None, budget_status="NORMAL", config=None, price=None, title="", **kwargs):
    """
    Enrutamiento inteligente de modelos multimodales (CORRECION_FALLOS.md 2.2):
    - 95%+ de llamadas utilizan modelos ultraligeros ('gemini-3.5-flash-lite').
    - Se elimina el modelo 'gemini-3.8-flash' (responsable del 42.27% del gasto en auditoría).
    - Escala a pro_model ('gemini-3.5-flash') ÚNICAMENTE cuando es un lote complejo y con ticket >= 150€.
    - Anuncios unitarios rutinarios (PS5, pantallas, Steam Deck, consolas sueltas) usan SIEMPRE fast_model.
    Soporta firmas:
      1) select_multimodal_model(item: dict, target: dict, config: Optional[dict] = None)
      2) select_multimodal_model(price, title="", desc="", target=None, budget_status="NORMAL")
      3) select_multimodal_model(price=45.0, title="...", desc="...", target=...)
    """
    if price is not None:
        p = float(price or 0.0)
        t = str(title or target_or_title or "")
        d = str(desc or "")
        tgt = target or {}
        cfg = config or {}
        b_status = budget_status
    elif isinstance(item_or_price, dict):
        item = item_or_price
        raw_p = item.get("price")
        if isinstance(raw_p, dict):
            raw_p = raw_p.get("amount") or 0.0
        try:
            p = float(raw_p or 0.0)
        except (ValueError, TypeError):
            p = 0.0
        t = str(item.get("title") or "")
        d = str(item.get("description") or item.get("desc") or "")
        tgt = target_or_title if isinstance(target_or_title, dict) else (target or {})
        cfg = desc if isinstance(desc, dict) else (config or {})
        b_status = budget_status
    else:
        p = float(item_or_price or 0.0)
        t = str(title or target_or_title or "")
        d = str(desc or "")
        tgt = target or {}
        cfg = config or {}
        b_status = budget_status

    fast_model = str(cfg.get("ai_fast_model", "gemini-3.5-flash-lite"))
    if "2.5" in fast_model or "latest" in fast_model:
        fast_model = "gemini-3.5-flash-lite"
    pro_model = str(cfg.get("ai_pro_model", "gemini-3.5-flash"))
    if "2.5" in pro_model or "3.8" in pro_model or "latest" in pro_model:
        pro_model = "gemini-3.5-flash"

    if b_status == "DEGRADED":
        return ModelPair(fast_model, "google/gemini-2.5-flash-lite")

    text = f"{t} {d}".lower()

    # Detección de lotes complejos con múltiples extras
    is_complex_lot = any(w in text for w in ("lote", "pack", " bundle", "+ juegos", "con juegos", "+ mandos", "con mandos"))

    # Regla de degradación estricta (CORRECION_FALLOS.md 2.2):
    # Prohibido usar modelos pesados o escalar por precio unitario alto.
    # El 95%+ de llamadas usan modelos Flash-Lite / Mini.
    # Escala a gemini-3.5-flash ÚNICAMENTE para lotes complejos combinados con precio alto (>=150€ con lote).
    if is_complex_lot and p >= 150.0:
        primary_gemini = pro_model
        primary_openrouter = "google/gemini-2.5-flash"
    else:
        primary_gemini = fast_model
        primary_openrouter = "google/gemini-2.5-flash-lite"

    return ModelPair(primary_gemini, primary_openrouter)


def analyze_and_generate_kit(
    platform, title, description, price, landed_cost, is_foreign, target,
    conn=None, health_monitor=None, extra_meta="", raw_images=None, call_type="vision"
):
    if not GEMINI_API_KEY and not OPENROUTER_API_KEY:
        print("[ERROR] Falta GEMINI_API_KEY u OPENROUTER_API_KEY.")
        return None

    if not isinstance(target, dict):
        if isinstance(conn, dict):
            target, conn = conn, None
        else:
            target = {}

    clean_title_str = html.unescape(title or "").strip()[:90]
    clean_desc_raw = html.unescape(description or "").strip()
    clean_desc_raw = re.sub(r"https?://\S+", "", clean_desc_raw)
    clean_desc = re.sub(r"\s+", " ", clean_desc_raw).strip()[:400]

    radar_id = target.get('id', target.get('name', ''))

    img_sig = "noimg"
    if raw_images and len(raw_images) > 0 and raw_images[0]:
        img_sig = hashlib.sha256(raw_images[0][:4096]).hexdigest()[:16]

    # v45: invalida veredictos cacheados con el prompt que anclaba la reventa a la mediana de compra (A-003)
    cache_key = build_robust_cache_key(radar_id, clean_title_str, clean_desc, price, img_sig=img_sig, prefix="v45")

    if conn:
        try:
            c = conn.cursor()
            c.execute("SELECT result_json FROM ai_eval_cache WHERE cache_key = ?", (cache_key,))
            cached_row = c.fetchone()
            if cached_row:
                return json.loads(cached_row[0])
        except Exception:
            pass

    # Control de Presupuesto Diario (P1-4)
    budget_status, budget_ratio = check_ai_budget_status(conn, health_monitor=health_monitor)
    if budget_status == "PAUSED":
        print(f"[BUDGET PAUSA] Presupuesto diario alcanzado ({budget_ratio*100:.0f}%). Análisis aplazado para '{clean_title_str}'")
        return _budget_pause_verdict(clean_title_str)

    preferred_gemini, preferred_openrouter = select_multimodal_model(
        price, clean_title_str, clean_desc, target=target, budget_status=budget_status
    )
    is_lite = "lite" in str(preferred_gemini).lower()
    text_low = f"{clean_title_str} {clean_desc}".lower()
    has_bundle_words = any(w in text_low for w in ("lote", "pack", "bundle", "juegos", "mandos", "+"))

    if budget_status == "DEGRADED":
        max_imgs_allowed = 1
        target_img_dim = 384
    elif is_lite:
        target_img_dim = 384
        max_imgs_allowed = 1 if (not has_bundle_words and price < 60.0) else 2
    else:
        target_img_dim = 512
        max_imgs_allowed = 3

    b64_images = []
    for img_b in (raw_images or [])[:max_imgs_allowed]:
        b64_str = prepare_image_for_ai(img_b, max_dim=target_img_dim)
        if b64_str:
            b64_images.append(b64_str)

    mode = target.get("mode", "resale")
    valuation_rules = (target.get("ai_valuation_guide", "") if isinstance(target, dict) else "")[:450]
    meta_suffix = f" | Meta: {extra_meta.strip()[:80]}" if extra_meta else ""
    num_imgs = len(b64_images)

    target_name = (target.get("name", "") if isinstance(target, dict) else "")[:50]
    if mode == "personal":
        prompt = (
            f"Auditor Hardware Homelab ({platform}).\n"
            f"Radar: {target_name} | Ítem: {clean_title_str} ({price:.0f}€, landed: {landed_cost:.0f}€){meta_suffix}\n"
            f"Desc: {clean_desc or 'Sin descripción'}\n"
            f"Fotos: {num_imgs} | Guía: {valuation_rules}\n\n"
            f"INSTRUCCIONES: Verifica SMART sano y >1TB. is_valid_deal=true si es funcional.\n"
            f"IMPORTANTE: Cero texto conversacional. Responde SOLO un JSON válido:\n"
            f'{{"is_valid_deal": bool, "is_pro_seller": bool, "has_defect_or_missing_parts": bool, '
            f'"visual_confidence": "alta"|"media"|"baja", "identified_model": "", "cex_search_query": "", '
            f'"visual_notes": "", "reject_reason": "", "estimated_resale_price": int, '
            f'"value_add_tip": "", "fast_buy_message": ""}}'
        )
    else:
        prompt = (
            f"Auditor Arbitraje Segunda Mano España ({platform}) con CeX.\n"
            f"Radar: {target_name} | Ítem: {clean_title_str} | {price:.0f}€ (Casa: {landed_cost:.0f}€){meta_suffix}\n"
            f"Desc: {clean_desc or 'Sin descripción'} | Fotos: {num_imgs}\n"
            f"Guía: {valuation_rules}\n\n"
            f"REGLAS INSPECCIÓN:\n"
            f"1. FOTOS: Si solo hay juego/funda/caja vacía o consola incompleta (ej: Switch sin Joy-Cons) -> is_valid_deal=false, has_defect_or_missing_parts=true.\n"
            f"2. DEFECTOS/ESTAFAS: Pago externo (bizum, virement, wasap), iCloud, drift, avería -> is_valid_deal=false.\n"
            f"3. 3 NIVELES: Pon is_valid_deal=true si es auténtico, funcional y completo (Nivel 1 CeX, Nivel 2 Colchón CeX o Nivel 3 P2P).\n"
            f"4. CEX: 'cex_search_query' con nombre canónico del modelo en fotos.\n"
            f"5. EXTRAS: 'bundled_games_queries' con juegos/mandos extra (máx 4) o [].\n\n"
            f"IMPORTANTE: Cero texto conversacional ni saludos. Responde ÚNICAMENTE un JSON válido con estas claves exactas:\n"
            f'{{"is_valid_deal": bool, "is_pro_seller": bool, "has_defect_or_missing_parts": bool, '
            f'"missing_original_charger": bool, "includes_original_box": bool, "includes_kit_lens": bool, '
            f'"visual_confidence": "alta"|"media"|"baja", "identified_model": "", "cex_search_query": "", '
            f'"bundled_games_queries": ["juego o accesorio extra en español, máx 4, o []"], '
            f'"visual_notes": "", "reject_reason": "", "estimated_resale_price": int, '
            f'"value_add_tip": "", "fast_buy_message": ""}}'
        )

    # H3 (A-003): price_history guarda precios PEDIDOS, no de venta, y mezcla submodelos.
    # Se presenta como referencia de compra y nunca como ancla de 'estimated_resale_price'.
    local_stats = get_local_market_stats(radar_id, days=14, conn=conn) if (conn and radar_id) else None
    if local_stats and local_stats.get("count", 0) >= 3:
        prompt += (
            f"\n\nReferencia de COMPRA (precio pedido típico en anuncios del radar, 14 días, mezcla submodelos): "
            f"{local_stats['median']:.0f}€ (n={local_stats['count']}). "
            f"NO es precio de reventa: no lo uses para fijar 'estimated_resale_price' ni para rechazar.\n"
        )

    if budget_status == "DEGRADED":
        candidate_models = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
    elif "lite" in str(preferred_gemini).lower():
        candidate_models = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
    else:
        candidate_models = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]

    openrouter_models = [preferred_openrouter]
    for fallback_m in ("google/gemini-2.5-flash-lite", "openai/gpt-4o-mini", "google/gemini-2.5-flash"):
        if fallback_m not in openrouter_models:
            openrouter_models.append(fallback_m)

    vision_schema_defaults = {
        "is_valid_deal": False,
        "is_pro_seller": False,
        "has_defect_or_missing_parts": False,
        "visual_confidence": "media",
        "identified_model": "",
        "cex_search_query": "",
        "bundled_games_queries": [],
        "visual_notes": "",
        "reject_reason": "",
        "estimated_resale_price": 0,
        "value_add_tip": "",
        "fast_buy_message": ""
    }

    parsed_result = None
    try:
        parsed_result = ai_gateway_call(
            prompt=prompt,
            images_b64=b64_images,
            candidate_models=candidate_models,
            openrouter_models=openrouter_models,
            max_tokens=450,
            temperature=0.1,
            conn=conn,
            platform=platform,
            radar_id=radar_id,
            call_type=call_type,
            timeout=15,
            enforce_budget=True,
            schema_defaults=None
        )
    except BudgetExceededError as e:
        print(f"[ANALYZE] Cortafuegos diario activo: {e}. Descartando análisis de visión.")
        if health_monitor and hasattr(health_monitor, "send_health_embed"):
            health_monitor.send_health_embed("🚨 Cortafuegos Presupuesto IA", str(e), 0xE74C3C)
        return _budget_pause_verdict(clean_title_str)
    except Exception as e:
        print(f"[ANALYZE] Excepción en llamada de visión ({platform}): {e}")

    if parsed_result and isinstance(parsed_result, dict):
        parsed_result = repair_and_parse_json(json.dumps(parsed_result, ensure_ascii=False), schema_defaults=vision_schema_defaults)
        bgq = parsed_result.get("bundled_games_queries")
        if not isinstance(bgq, list):
            parsed_result["bundled_games_queries"] = []
        else:
            parsed_result["bundled_games_queries"] = [str(q).strip() for q in bgq if str(q).strip()][:4]

    if parsed_result and isinstance(parsed_result, dict) and not parsed_result.get("_api_error") and conn:
        try:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO ai_eval_cache (cache_key, result_json, evaluated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (cache_key, json.dumps(parsed_result, ensure_ascii=False))
            )
            conn.commit()
        except Exception:
            pass

    if parsed_result and isinstance(parsed_result, dict):
        if parsed_result.get("is_valid_deal") is True and not parsed_result.get("fraud_warning"):
            if radar_id and price > 0:
                record_market_observation(radar_id, platform, price, conn=conn)
                identified = str(parsed_result.get("identified_model") or "").strip().lower()
                if identified:
                    submodel_key = f"{radar_id}:{identified}"
                    record_market_observation(submodel_key, platform, price, conn=conn)

    return parsed_result


def classify_deal_tier(
    total_buy_cost, cex_cash_total=0.0, cex_voucher_total=0.0,
    estimated_market_value=0.0, fraud_risk_score=0, buy_confidence=8
):
    """
    Clasificación Oficial de Chollos en 3 Niveles (v5.2):
    - NIVEL 1: Arbitraje Puro CeX (Riesgo Cero / Efectivo Inmediato)
    - NIVEL 2: Chollo con Colchón CeX (Rentabilidad P2P + CeX cubre >= 70% cash o >= 90% voucher)
    - NIVEL 3: Oportunidad de Mercado / Lote (Reventa P2P pura)
    """
    total_buy_cost = float(total_buy_cost or 0.0)
    cex_cash_total = float(cex_cash_total or 0.0)
    cex_voucher_total = float(cex_voucher_total or 0.0)
    estimated_market_value = float(estimated_market_value or 0.0)
    fraud_risk_score = int(fraud_risk_score or 0)
    buy_confidence = int(buy_confidence or 8)

    if total_buy_cost <= 0:
        return {
            "deal_tier": None,
            "tier_code": "REJECT",
            "tier_label": "RECHAZADO (Coste inválido)",
            "cex_cash_net": 0.0,
            "cex_coverage_pct": 0.0,
            "p2p_net_profit": 0.0,
            "p2p_roi_pct": 0.0,
            "is_deal": False
        }

    # Métrica 1: Liquidez Instantánea CeX
    cex_cash_net = round(cex_cash_total - total_buy_cost, 2)
    cex_coverage_pct = round((cex_cash_total / total_buy_cost) * 100.0, 1)

    # Métrica 2: Reventa Particular P2P (0.92 = 8% comisión/negociación)
    p2p_net_profit = round((estimated_market_value * 0.92) - total_buy_cost, 2)
    p2p_roi_pct = round((p2p_net_profit / total_buy_cost) * 100.0, 1)

    # 🟢 NIVEL 1: ARBITRAJE PURO CeX (Riesgo Cero / Efectivo Inmediato)
    # Condición: cex_cash_net >= 12.0 y (cex_cash_net / total_buy_cost) >= 0.18 y fraud_risk_score <= 2
    if cex_cash_net >= 12.0 and (cex_cash_net / total_buy_cost) >= 0.18 and fraud_risk_score <= 2:
        return {
            "deal_tier": 1,
            "tier_code": "NIVEL_1",
            "tier_label": "🟢 NIVEL 1: ARBITRAJE CeX",
            "cex_cash_net": cex_cash_net,
            "cex_coverage_pct": cex_coverage_pct,
            "p2p_net_profit": p2p_net_profit,
            "p2p_roi_pct": p2p_roi_pct,
            "is_deal": True
        }

    # 🔵 NIVEL 2: CHOLLO CON COLCHÓN CeX (Alta Rentabilidad P2P + Suelo Garantizado)
    # Condición: (cex_coverage_pct >= 70.0 o cex_voucher_total >= total_buy_cost * 0.90) Y p2p_net_profit >= 25.0 Y p2p_roi_pct >= 35.0
    has_cex_cushion = (cex_coverage_pct >= 70.0) or (cex_voucher_total >= (total_buy_cost * 0.90))
    if has_cex_cushion and p2p_net_profit >= 25.0 and (p2p_roi_pct >= 35.0 or cex_coverage_pct >= 100.0) and fraud_risk_score <= 3:
        return {
            "deal_tier": 2,
            "tier_code": "NIVEL_2",
            "tier_label": f"🔵 NIVEL 2: CHOLLO + COLCHÓN CeX (Cubre {cex_coverage_pct:.0f}%)",
            "cex_cash_net": cex_cash_net,
            "cex_coverage_pct": cex_coverage_pct,
            "p2p_net_profit": p2p_net_profit,
            "p2p_roi_pct": p2p_roi_pct,
            "is_deal": True
        }

    # 🟣 NIVEL 3: OPORTUNIDAD DE MERCADO / LOTE (Reventa Particular Pura)
    # Condición: p2p_net_profit >= 30.0, p2p_roi_pct >= 45.0, buy_confidence >= 8 y fraud_risk_score <= 2
    if p2p_net_profit >= 30.0 and p2p_roi_pct >= 45.0 and buy_confidence >= 8 and fraud_risk_score <= 2:
        return {
            "deal_tier": 3,
            "tier_code": "NIVEL_3",
            "tier_label": "🟣 NIVEL 3: REVENTA P2P",
            "cex_cash_net": cex_cash_net,
            "cex_coverage_pct": cex_coverage_pct,
            "p2p_net_profit": p2p_net_profit,
            "p2p_roi_pct": p2p_roi_pct,
            "is_deal": True
        }

    return {
        "deal_tier": None,
        "tier_code": "REJECT",
        "tier_label": "RECHAZADO (Márgenes insuficientes)",
        "cex_cash_net": cex_cash_net,
        "cex_coverage_pct": cex_coverage_pct,
        "p2p_net_profit": p2p_net_profit,
        "p2p_roi_pct": p2p_roi_pct,
        "is_deal": False
    }


def evaluate_deal_math_and_score(
    price, landed_cost, target, ai_data, cex_data,
    min_profit=35.0, min_personal_savings=25.0, min_score=8, filter_pro_seller=True
):
    """
    Motor Matemático Dual v4.3 (Compra Directa >=8/10 + Oportunidad de Chollo mediante Oferta 6-7/10):
    1. Si la IA detecta defectos, estafas, faltan piezas o confianza visual baja -> RECHAZADO SIEMPRE.
    2. Evalúa primero si al precio actual es un CHOLLO DIRECTO (`score >= 8`, amortizado en CeX y con beneficio >= min_profit).
    3. Si no llega a >=8 al precio actual pero el producto es 100% válido, calcula la OFERTA RECOMENDADA (`recommended_offer`)
       que garantiza amortización en CeX y beneficio neto >= `min_profit`. Si la rebaja necesaria es <= 35% (realista en Vinted/Wallapop),
       lo aprueba como `deal_type = 'offer_opportunity'` (nota 6 o 7/10) con su cabecera propia de Oportunidad de Chollo.
    """
    if not ai_data or not isinstance(ai_data, dict):
        return False, 1, 0.0, 0.0, 0.0, "Fallo en respuesta de IA"

    mode = target.get("mode", "resale")
    is_valid = bool(ai_data.get("is_valid_deal", False))
    is_pro = bool(ai_data.get("is_pro_seller", False)) if filter_pro_seller else False
    has_defect = bool(ai_data.get("has_defect_or_missing_parts", False))
    vis_conf = str(ai_data.get("visual_confidence") or "media").lower().strip()
    identified = str(ai_data.get("identified_model") or "").strip()

    if not is_valid:
        return False, 1, 0.0, 0.0, 0.0, (ai_data.get("reject_reason") or "Descartado por inspección IA")
    if is_pro:
        return False, 2, 0.0, 0.0, 0.0, "Tienda o vendedor profesional"
    if has_defect:
        return False, 2, 0.0, 0.0, 0.0, (ai_data.get("reject_reason") or "Con defectos o faltan piezas clave")
    if vis_conf == "baja":
        return False, 3, 0.0, 0.0, 0.0, (ai_data.get("reject_reason") or "Confianza visual baja en las fotos del anuncio")

    est_fees = max(0.0, round(landed_cost - price, 2))
    direct_max_cap = float(target.get("direct_buy_max_price") or target.get("max_price", 9999))
    id_text = f"{identified} {ai_data.get('cex_search_query', '')}".lower()

    for cap_rule in target.get("submodel_price_caps", []):
        max_sub_p = float(cap_rule.get("max_price", 9999))
        match_tokens = cap_rule.get("match_any", [])
        unless_tokens = cap_rule.get("unless_any", [])
        if any(_token_in_text(mt, id_text) for mt in match_tokens):
            if not (unless_tokens and any(_token_in_text(ut, id_text) for ut in unless_tokens)):
                if max_sub_p <= 5:
                    return False, 1, 0.0, 0.0, 0.0, f"Submodelo '{identified}' bloqueado por configuración"
                direct_max_cap = min(direct_max_cap, max_sub_p)

    ai_est_price = float(ai_data.get("estimated_resale_price", 0) or 0)
    allow_offers = bool(target.get("offer_sniping", True))
    max_haggle_discount_pct = float(target.get("max_haggle_discount_pct", 35.0))

    # ==================== MODO COMPRA PERSONAL (SERVIDOR HOMELAB) ====================
    if mode == "personal":
        optimal_price = ai_est_price if ai_est_price > 0 else (cex_data["sell"] if cex_data else price)
        best_net_profit = round(optimal_price - landed_cost, 2)
        req_margin = float(target.get("min_savings_eur", min_personal_savings))

        if price <= direct_max_cap and best_net_profit >= req_margin:
            # P7-3: Freno de inverosimilitud para modo personal (seguro ante cex_data None)
            ref_val = float(((cex_data.get("cash", 0) or cex_data.get("sell", 0)) if cex_data else 0) or optimal_price)
            is_anomalously_low = bool(ref_val >= 150.0 and price > 0 and price < (0.35 * ref_val))
            if is_anomalously_low:
                score = 7
                ai_data["deal_score"] = score
                ai_data["deal_type"] = "high_risk_opportunity"
                ai_data["is_anomalously_low"] = True
                ai_data["recommended_offer"] = 0
                return True, score, optimal_price, best_net_profit, 0.0, "OK_PRECIO_ANOMALO"

            roi_pct = (best_net_profit / max(landed_cost, 1.0)) * 100.0
            if vis_conf == "alta" and best_net_profit >= 45 and roi_pct >= 50:
                score = 10
            elif best_net_profit >= 32 and roi_pct >= 35:
                score = 9
            else:
                score = 8
            ai_data["deal_score"] = score
            ai_data["deal_type"] = "direct_chollo"
            ai_data["recommended_offer"] = 0
            return True, score, optimal_price, best_net_profit, 0.0, "OK"

        if allow_offers and optimal_price > (req_margin + est_fees + 10):
            target_offer = int(min(direct_max_cap, round(optimal_price - req_margin - est_fees)))
            if 12 <= target_offer < price:
                disc_pct = ((price - target_offer) / price) * 100.0
                if disc_pct <= max_haggle_discount_pct:
                    offer_landed = round(target_offer + est_fees, 2)
                    offer_profit = round(optimal_price - offer_landed, 2)
                    score = 7 if disc_pct <= 20.0 else 6
                    ai_data["deal_score"] = score
                    ai_data["deal_type"] = "offer_opportunity"
                    ai_data["recommended_offer"] = target_offer
                    ai_data["offer_landed_cost"] = offer_landed
                    ai_data["offer_discount_pct"] = round(disc_pct)
                    return True, score, optimal_price, offer_profit, 0.0, "OPORTUNIDAD_OFERTA"

        return False, 5, optimal_price, best_net_profit, 0.0, f"Ahorro insuficiente (+{best_net_profit:.0f}€ < +{req_margin:.0f}€)"

    # ==================== MODO REVENTA CON RESPALDO CEX Y MERCADO P2P (3 NIVELES v5.2) ====================
    missing_charger = bool(ai_data.get("missing_original_charger", False))
    charger_penalty = 12.0 if missing_charger else 0.0
    effective_cost = round(landed_cost + charger_penalty, 2)

    cex_cash = float(cex_data["cash"]) if (cex_data and cex_data.get("cash")) else 0.0
    cex_voucher = float(cex_data["voucher"]) if (cex_data and cex_data.get("voucher")) else 0.0
    cex_sell = float(cex_data["sell"]) if (cex_data and cex_data.get("sell")) else 0.0

    # P7-2: Penalización si es cartucho suelto (sin caja)
    is_retro_game = target.get("id") == "pokemon_ds_gba" or (cex_data and "juegos" in str(cex_data.get("category", "")).lower())
    includes_box = bool(ai_data.get("includes_original_box", True))
    cex_bname = str(cex_data.get("box_name", "")).lower() if cex_data else ""
    case_penalty = 12.0 if (is_retro_game and not includes_box and "sin caja" not in cex_bname) else 0.0
    ai_data["case_penalty"] = case_penalty

    cex_sell_effective = max(0.0, cex_sell - case_penalty)
    cex_realistic_cap = round(cex_sell_effective * 0.84) if cex_sell_effective > 0 else 0.0

    if ai_est_price > 0:
        optimal_price = ai_est_price
    elif cex_realistic_cap > 0:
        optimal_price = max(max(0.0, cex_cash - case_penalty), cex_realistic_cap)
    else:
        optimal_price = price

    vis_score_num = 10 if vis_conf == "alta" else (8 if vis_conf == "media" else 4)
    fraud_risk_num = int(ai_data.get("fraud_risk_score", 0) or 0)

    # Clasificación en 3 Niveles Oficiales v5.2
    tier_res = classify_deal_tier(
        total_buy_cost=effective_cost,
        cex_cash_total=max(0.0, cex_cash - case_penalty),
        cex_voucher_total=max(0.0, cex_voucher - case_penalty),
        estimated_market_value=optimal_price,
        fraud_risk_score=fraud_risk_num,
        buy_confidence=vis_score_num
    )

    ai_data["deal_tier"] = tier_res["deal_tier"]
    ai_data["tier_code"] = tier_res["tier_code"]
    ai_data["deal_tier_label"] = tier_res["tier_label"]
    ai_data["cex_cash_net"] = tier_res["cex_cash_net"]
    ai_data["cex_coverage_pct"] = tier_res["cex_coverage_pct"]
    ai_data["p2p_net_profit"] = tier_res["p2p_net_profit"]
    ai_data["p2p_roi_pct"] = tier_res["p2p_roi_pct"]

    # 1. ¿Es un CHOLLO DIRECTO (NIVEL 1, 2 o 3) al precio actual?
    if tier_res["is_deal"] and price <= direct_max_cap:
        # Freno de inverosimilitud (Anti-Anzuelo)
        is_anomalously_low = bool(cex_cash >= 150.0 and price > 0 and price < (0.45 * cex_cash))
        if is_anomalously_low:
            score = 7
            ai_data["deal_score"] = score
            ai_data["deal_type"] = "high_risk_opportunity"
            ai_data["is_anomalously_low"] = True
            ai_data["recommended_offer"] = 0
            return True, score, optimal_price, tier_res["p2p_net_profit"], charger_penalty, "OK_PRECIO_ANOMALO"

        if tier_res["deal_tier"] == 1:
            score = 10
            best_profit = tier_res["cex_cash_net"]
        elif tier_res["deal_tier"] == 2:
            score = 9
            best_profit = tier_res["p2p_net_profit"]
        else:
            score = 8
            best_profit = tier_res["p2p_net_profit"]

        ai_data["deal_score"] = score
        ai_data["deal_type"] = "direct_chollo"
        ai_data["recommended_offer"] = 0
        return True, score, optimal_price, best_profit, charger_penalty, f"OK_{tier_res['tier_code']}"

    # 2. Evaluación como OPORTUNIDAD DE CHOLLO (HACER OFERTA)
    req_margin = float(target.get("min_profit_eur", min_profit))
    net_optimal_p2p = optimal_price * 0.92
    if allow_offers and net_optimal_p2p > (req_margin + est_fees + 10):
        target_offer = int(min(direct_max_cap, math.floor(net_optimal_p2p - req_margin - est_fees)))
        if 12 <= target_offer < price:
            disc_pct = ((price - target_offer) / price) * 100.0
            if 6.0 <= disc_pct <= max_haggle_discount_pct:
                offer_landed = round(target_offer + est_fees, 2)
                offer_profit = round((optimal_price * 0.92) - offer_landed, 2)
                if offer_profit >= req_margin:
                    score = 7 if disc_pct <= 20.0 else 6
                    ai_data["deal_score"] = score
                    ai_data["deal_type"] = "offer_opportunity"
                    ai_data["recommended_offer"] = target_offer
                    ai_data["offer_landed_cost"] = offer_landed
                    ai_data["offer_discount_pct"] = round(disc_pct)
                    return True, score, optimal_price, offer_profit, charger_penalty, "OPORTUNIDAD_OFERTA"

    return (
        False, 5, optimal_price, tier_res["p2p_net_profit"], charger_penalty,
        f"Márgenes insuficientes ({tier_res['tier_label']})"
    )


# ==================== GUARDADO DE KIT Y REGISTRO EN BD ====================

def build_kit_folder_name(platform, item_id, ai_data):
    model_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", (ai_data or {}).get("identified_model", "item"))[:28]
    plat_tag = platform.upper()[:6]
    return f"{datetime.now().strftime('%Y%m%d_%H%M')}_{plat_tag}_{model_slug}_{item_id}"


def save_resale_kit(kit_folder, platform, item_id, title, item_url, ai_data, optimal_price, landed_cost, enhanced_photos, image_urls=None):
    kit_path = os.path.join(KITS_DIR, kit_folder)
    os.makedirs(kit_path, exist_ok=True)

    clean_title = re.sub(r"\s+", " ", (title or "").strip())
    identified = re.sub(r"\s+", " ", ((ai_data or {}).get("identified_model") or clean_title).strip())
    seo_title = identified[:48]
    tip = re.sub(r"\s+", " ", ((ai_data or {}).get("value_add_tip") or "Venta rápida en 48h").strip())
    visual_notes = re.sub(r"\s+", " ", ((ai_data or {}).get("visual_notes") or "").strip())

    txt_content = (
        f"=== KIT DE REVENTA ({platform.upper()} • BOTI SNIPER v4.3) ===\n"
        f"Producto original: {clean_title}\n"
        f"Modelo identificado: {identified}\n"
        f"Inspección visual IA: {visual_notes}\n"
        f"URL origen: {item_url}\n"
        f"Coste real en casa: {landed_cost:.0f}€\n"
        f"Precio óptimo recomendado en Wallapop: {optimal_price:.0f}€\n"
        f"Tip de Valor Añadido: {tip}\n\n"
        f"--- TÍTULO SEO ---\n"
        f"{seo_title}\n"
    )

    with open(os.path.join(kit_path, "ANUNCIO_LISTO.txt"), "w", encoding="utf-8") as f:
        f.write(txt_content)

    meta_obj = {
        "kit_folder": kit_folder,
        "platform": platform.upper(),
        "item_id": str(item_id),
        "title": title,
        "identified_model": (ai_data or {}).get("identified_model", title),
        "visual_notes": visual_notes,
        "item_url": item_url,
        "image_urls": image_urls or [],
        "landed_cost": round(landed_cost, 2),
        "optimal_price": round(optimal_price, 2),
        "seo_title": seo_title,
        "value_add_tip": tip,
        "created_at": datetime.now().isoformat()
    }
    with open(os.path.join(kit_path, "meta.json"), "w", encoding="utf-8") as mf:
        json.dump(meta_obj, mf, ensure_ascii=False, indent=2)

    for i, p_bytes in enumerate(enhanced_photos or [], start=1):
        with open(os.path.join(kit_path, f"foto_{i}_limpia.jpg"), "wb") as f:
            f.write(p_bytes)

    return kit_folder


# ==================== CARTERA: COMPRAS, VENTAS E HISTORIAL DESHACIBLE ====================
# Estados de un deal: alerted -> bought -> sold, o alerted -> discarded, o bought -> cancelled
# (la compra no llegó a hacerse). Cada cambio guarda una foto previa de la fila en deal_events,
# así que cualquier acción (incluido borrar) se puede deshacer.

DEAL_STATUS_LABELS = {
    "alerted": "🔔 Alertado",
    "bought": "📦 En stock",
    "sold": "✅ Vendido",
    "discarded": "🚫 Descartado",
    "cancelled": "↩️ Compra cancelada",
}


def ensure_deal_events_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT,
            action TEXT,
            old_status TEXT,
            new_status TEXT,
            snapshot_json TEXT,
            note TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_events_deal ON deal_events(deal_id, id)")


def get_deal(conn, deal_id):
    c = conn.cursor()
    c.execute("SELECT * FROM deals WHERE deal_id = ?", (str(deal_id),))
    row = c.fetchone()
    if not row:
        return None
    return dict(zip([d[0] for d in c.description], row))


def _log_deal_event(conn, deal_id, action, old_status, new_status, snapshot, note=""):
    ensure_deal_events_table(conn)
    cur = conn.execute(
        "INSERT INTO deal_events (deal_id, action, old_status, new_status, snapshot_json, note) VALUES (?, ?, ?, ?, ?, ?)",
        (str(deal_id), action, old_status, new_status,
         json.dumps(snapshot, ensure_ascii=False, default=str) if snapshot else "", str(note or "")[:300])
    )
    return cur.lastrowid


def _require_deal(conn, deal_id, allowed_status=None):
    deal = get_deal(conn, deal_id)
    if not deal:
        raise ValueError("No existe ese anuncio en la base de datos.")
    if allowed_status and deal.get("status") not in allowed_status:
        current = DEAL_STATUS_LABELS.get(deal.get("status"), deal.get("status"))
        raise ValueError(f"El anuncio está en estado {current}; esta acción no aplica.")
    return deal


def mark_deal_bought(conn, deal_id, bought_price, notes=None):
    """Registra la compra con el precio realmente pagado (envío incluido)."""
    deal = _require_deal(conn, deal_id, allowed_status=("alerted", "discarded", "cancelled", "bought"))
    price_val = float(bought_price)
    if price_val <= 0:
        raise ValueError("El precio de compra debe ser mayor que 0 €.")
    conn.execute(
        """UPDATE deals SET status = 'bought', bought_price = ?,
               bought_at = CASE WHEN status = 'bought' AND bought_at IS NOT NULL THEN bought_at ELSE CURRENT_TIMESTAMP END,
               notes = COALESCE(?, notes), status_changed_at = CURRENT_TIMESTAMP
           WHERE deal_id = ?""",
        (price_val, (notes.strip() if isinstance(notes, str) and notes.strip() else None), deal["deal_id"])
    )
    _log_deal_event(conn, deal["deal_id"], "buy", deal.get("status"), "bought", deal)
    conn.commit()
    return get_deal(conn, deal["deal_id"])


def create_manual_deal(conn, title, bought_price, target_price=None, platform="MANUAL", notes="", item_url=""):
    """Compra hecha fuera de los radares."""
    price_val = float(bought_price)
    if price_val <= 0:
        raise ValueError("El precio de compra debe ser mayor que 0 €.")
    deal_id = f"MAN_{int(time.time() * 1000)}"
    target_val = float(target_price) if target_price and float(target_price) > 0 else round(price_val * 1.35, 2)
    conn.execute(
        """INSERT INTO deals (deal_id, platform, title, identified_model, price, landed_cost, bought_price,
                              optimal_price, status, mode, notes, item_url, bought_at, status_changed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'bought', 'resale', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (deal_id, str(platform or "MANUAL").upper()[:20], title.strip()[:200], title.strip()[:120],
         price_val, price_val, price_val, target_val, str(notes or "")[:500], str(item_url or ""))
    )
    _log_deal_event(conn, deal_id, "create_manual", None, "bought", None)
    conn.commit()
    return get_deal(conn, deal_id)


def cancel_deal_purchase(conn, deal_id, reason=""):
    """La compra no se llegó a realizar: sale del stock y de las estadísticas, pero queda en el historial."""
    deal = _require_deal(conn, deal_id, allowed_status=("bought",))
    note_txt = f"Compra cancelada: {reason.strip()}" if reason and reason.strip() else "Compra cancelada"
    conn.execute(
        """UPDATE deals SET status = 'cancelled', status_changed_at = CURRENT_TIMESTAMP,
               notes = TRIM(COALESCE(notes, '') || CASE WHEN COALESCE(notes, '') = '' THEN '' ELSE ' | ' END || ?)
           WHERE deal_id = ?""",
        (note_txt[:200], deal["deal_id"])
    )
    _log_deal_event(conn, deal["deal_id"], "cancel_purchase", "bought", "cancelled", deal, reason)
    conn.commit()
    return get_deal(conn, deal["deal_id"])


def mark_deal_sold(conn, deal_id, sold_price, sale_fees=0.0):
    deal = _require_deal(conn, deal_id, allowed_status=("bought", "sold"))
    sold_val = float(sold_price)
    fees_val = max(0.0, float(sale_fees or 0.0))
    if sold_val <= 0:
        raise ValueError("El precio de venta debe ser mayor que 0 €.")
    bought_val = float(deal.get("bought_price") or 0.0) or float(deal.get("landed_cost") or deal.get("price") or 0.0)
    conn.execute(
        """UPDATE deals SET status = 'sold', sold_price = ?, sale_fees = ?, bought_price = ?,
               sold_at = CURRENT_TIMESTAMP, status_changed_at = CURRENT_TIMESTAMP
           WHERE deal_id = ?""",
        (sold_val, fees_val, bought_val, deal["deal_id"])
    )
    _log_deal_event(conn, deal["deal_id"], "sell", deal.get("status"), "sold", deal)
    conn.commit()
    return get_deal(conn, deal["deal_id"])


def update_deal_fields(conn, deal_id, bought_price=None, sold_price=None, target_price=None, notes=None, sale_fees=None):
    """Corrige datos de un deal sin cambiar su estado."""
    deal = _require_deal(conn, deal_id)
    sets, params = [], []
    for col, val in (("bought_price", bought_price), ("sold_price", sold_price),
                     ("optimal_price", target_price), ("sale_fees", sale_fees)):
        if val is not None:
            if float(val) < 0:
                raise ValueError("Los importes no pueden ser negativos.")
            sets.append(f"{col} = ?")
            params.append(float(val))
    if notes is not None:
        sets.append("notes = ?")
        params.append(str(notes)[:500])
    if not sets:
        raise ValueError("No has indicado ningún campo que cambiar.")
    conn.execute(f"UPDATE deals SET {', '.join(sets)} WHERE deal_id = ?", params + [deal["deal_id"]])
    _log_deal_event(conn, deal["deal_id"], "edit", deal.get("status"), deal.get("status"), deal)
    conn.commit()
    return get_deal(conn, deal["deal_id"])


def delete_deal(conn, deal_id):
    """Borra el deal. Se guarda una copia en deal_events para poder deshacerlo."""
    deal = _require_deal(conn, deal_id)
    _log_deal_event(conn, deal["deal_id"], "delete", deal.get("status"), None, deal)
    conn.execute("DELETE FROM deals WHERE deal_id = ?", (deal["deal_id"],))
    conn.commit()
    return deal


def _restore_deal_snapshot(conn, snapshot):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(deals)").fetchall()]
    data = {k: v for k, v in snapshot.items() if k in cols}
    if "deal_id" not in data:
        raise ValueError("La copia guardada está incompleta.")
    names = list(data.keys())
    placeholders = ", ".join("?" for _ in names)
    updates = ", ".join(f"{n} = excluded.{n}" for n in names if n != "deal_id")
    conn.execute(
        f"INSERT INTO deals ({', '.join(names)}) VALUES ({placeholders}) "
        f"ON CONFLICT(deal_id) DO UPDATE SET {updates}",
        [data[n] for n in names]
    )


def undo_last_deal_change(conn, deal_id=None):
    """Deshace el último cambio de un deal (o el último cambio global si deal_id es None),
    incluido un borrado. Devuelve (accion_deshecha, deal_restaurado)."""
    ensure_deal_events_table(conn)
    # Pila: el último cambio aún no deshecho (cada 'undo' guarda en note el id del evento que revirtió)
    q = ("SELECT id, deal_id, action, snapshot_json FROM deal_events WHERE action != 'undo' "
         "AND CAST(id AS TEXT) NOT IN (SELECT note FROM deal_events WHERE action = 'undo')")
    params = []
    if deal_id:
        q += " AND deal_id = ?"
        params.append(str(deal_id))
    q += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(q, params).fetchone()
    if not row:
        raise ValueError("No hay cambios que deshacer.")
    ev_id, ev_deal, action, snap_json = row
    current = get_deal(conn, ev_deal)
    if action == "create_manual":
        conn.execute("DELETE FROM deals WHERE deal_id = ?", (ev_deal,))
    else:
        if not snap_json:
            raise ValueError("Ese cambio no guardó copia y no se puede deshacer.")
        _restore_deal_snapshot(conn, json.loads(snap_json))
    restored = get_deal(conn, ev_deal)
    _log_deal_event(conn, ev_deal, "undo", (current or {}).get("status"), (restored or {}).get("status"),
                    current, note=str(ev_id))
    conn.commit()
    return action, restored


def get_portfolio_summary(conn):
    """Resumen de cartera: stock, capital invertido, ventas, beneficio neto (con comisiones) y del mes."""
    c = conn.cursor()
    c.execute(
        """SELECT deal_id, platform, COALESCE(NULLIF(identified_model, ''), title, deal_id), bought_price,
                  optimal_price, mode, COALESCE(bought_at, created_at), item_url, COALESCE(notes, '')
           FROM deals WHERE status = 'bought' ORDER BY COALESCE(bought_at, created_at) DESC"""
    )
    stock = []
    for did, plat, name, b_p, o_p, mode, b_at, url, notes in c.fetchall():
        b_p = float(b_p or 0)
        o_p = float(o_p or 0) or round(b_p * 1.35, 2)
        days_in_stock = None
        try:
            days_in_stock = max(0, (datetime.now() - datetime.fromisoformat(str(b_at)[:19])).days)
        except Exception:
            pass
        stock.append({"deal_id": did, "platform": plat or "", "model": str(name).strip(), "bought_price": b_p,
                      "optimal_price": o_p, "mode": mode or "resale", "bought_at": b_at, "item_url": url or "",
                      "notes": notes, "days_in_stock": days_in_stock})

    def _agg(where):
        c.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(bought_price), 0), COALESCE(SUM(sold_price), 0),
                       COALESCE(SUM(sale_fees), 0) FROM deals WHERE status = 'sold' {where}"""
        )
        n, cost, rev, fees = c.fetchone()
        cost, rev, fees = float(cost), float(rev), float(fees)
        profit = rev - cost - fees
        return {"count": int(n), "cost": cost, "revenue": rev, "fees": fees, "profit": profit,
                "roi": (profit / cost * 100.0) if cost > 0 else 0.0}

    total = _agg("")
    month = _agg("AND COALESCE(sold_at, created_at) >= datetime('now', 'start of month')")
    c.execute(
        """SELECT deal_id, COALESCE(NULLIF(identified_model, ''), title, deal_id), platform, bought_price,
                  sold_price, COALESCE(sale_fees, 0), COALESCE(sold_at, created_at)
           FROM deals WHERE status = 'sold' ORDER BY COALESCE(sold_at, created_at) DESC LIMIT 5"""
    )
    recent_sold = [
        {"deal_id": r[0], "model": str(r[1]).strip(), "platform": r[2] or "", "bought_price": float(r[3] or 0),
         "sold_price": float(r[4] or 0), "sale_fees": float(r[5] or 0), "sold_at": r[6]}
        for r in c.fetchall()
    ]
    resale_stock = [s for s in stock if s["mode"] != "personal"]
    invested = sum(s["bought_price"] for s in stock)
    latent = sum(max(0.0, s["optimal_price"] - s["bought_price"]) for s in resale_stock)
    return {"stock": stock, "invested": invested, "latent_profit": latent,
            "latent_roi": (latent / invested * 100.0) if invested > 0 else 0.0,
            "sold": total, "month": month, "recent_sold": recent_sold}


def search_deals(conn, text="", statuses=None, limit=25, offset=0):
    """Busca deals por texto (título, modelo, ID o URL) y estado. Devuelve (filas, total)."""
    where, params = [], []
    if statuses:
        where.append(f"status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    for w in [w for w in re.split(r"\s+", (text or "").strip()) if w][:4]:
        where.append("(title LIKE ? OR identified_model LIKE ? OR deal_id LIKE ? OR item_id = ? OR item_url LIKE ?)")
        kw = f"%{w}%"
        params.extend([kw, kw, kw, w, kw])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*) FROM deals {where_sql}", params)
    total = int(c.fetchone()[0])
    c.execute(
        f"""SELECT deal_id, platform, COALESCE(NULLIF(identified_model, ''), title, deal_id), status, price,
                   bought_price, sold_price, optimal_price, item_url,
                   COALESCE(status_changed_at, created_at), created_at
            FROM deals {where_sql}
            ORDER BY COALESCE(status_changed_at, created_at) DESC LIMIT ? OFFSET ?""",
        params + [int(limit), int(offset)]
    )
    cols = ["deal_id", "platform", "model", "status", "price", "bought_price", "sold_price",
            "optimal_price", "item_url", "updated_at", "created_at"]
    return [dict(zip(cols, r)) for r in c.fetchall()], total


def count_stale_alerts(conn, days=30):
    return int(conn.execute(
        "SELECT COUNT(*) FROM deals WHERE status IN ('alerted', 'discarded') AND created_at < datetime('now', ?)",
        (f"-{int(days)} days",)
    ).fetchone()[0])


def purge_stale_alerts(conn, days=30):
    """Borra alertas viejas que nunca se compraron. Compras, ventas y cancelaciones se conservan."""
    cur = conn.execute(
        "DELETE FROM deals WHERE status IN ('alerted', 'discarded') AND created_at < datetime('now', ?)",
        (f"-{int(days)} days",)
    )
    conn.commit()
    return cur.rowcount


def export_deals_csv(conn, statuses=("bought", "sold", "cancelled")):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["deal_id", "estado", "plataforma", "producto", "precio_anuncio", "precio_compra", "fecha_compra",
                "precio_venta", "comisiones_venta", "fecha_venta", "beneficio_neto", "objetivo_venta", "notas", "url"])
    q = f"""SELECT deal_id, status, platform, COALESCE(NULLIF(identified_model, ''), title), price, bought_price,
                   bought_at, sold_price, COALESCE(sale_fees, 0), sold_at, optimal_price, COALESCE(notes, ''), item_url
            FROM deals WHERE status IN ({', '.join('?' for _ in statuses)})
            ORDER BY COALESCE(bought_at, created_at)"""
    for r in conn.execute(q, list(statuses)).fetchall():
        did, st, plat, name, price, b_p, b_at, s_p, fees, s_at, opt, notes, url = r
        profit = (float(s_p or 0) - float(b_p or 0) - float(fees or 0)) if st == "sold" else ""
        w.writerow([did, st, plat, name, price, b_p, b_at or "", s_p if st == "sold" else "", fees if st == "sold" else "",
                    s_at or "", round(profit, 2) if profit != "" else "", opt, notes, url])
    return buf.getvalue()


def register_deal_in_db(
    conn, deal_id, platform, item_id, target, title, description, price, landed_cost, fees,
    optimal_price, net_profit, ai_data, cex_data, item_url, checkout_url, image_url, kit_folder,
    seller_city="España", is_local=False, image_urls=None, item_vec=None, seller_id=""
):
    try:
        urls_list = image_urls if isinstance(image_urls, list) and image_urls else ([image_url] if image_url else [])
        deal_type = (ai_data or {}).get("deal_type", "direct_chollo")
        rec_offer = float((ai_data or {}).get("recommended_offer", 0) or 0)
        c = conn.cursor()
        c.execute(
            # Upsert: una re-alerta (p. ej. tras rebaja) actualiza los datos del anuncio, pero NUNCA
            # pisa una compra/venta registrada ni su kit (antes INSERT OR REPLACE borraba la cartera).
            """INSERT INTO deals (
                deal_id, platform, item_id, radar_id, radar_name, mode, title, description,
                price, landed_cost, fees, optimal_price, net_profit, deal_score,
                identified_model, cex_search_query, cex_box_name, cex_cash, cex_voucher, cex_sell,
                item_url, checkout_url, image_url, kit_folder, seller_city, is_local,
                fast_msg, haggle_msg, seo_title, seo_description, value_add_tip,
                image_urls_json, visual_notes, deal_type, recommended_offer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(deal_id) DO UPDATE SET
                platform = excluded.platform, item_id = excluded.item_id, radar_id = excluded.radar_id,
                radar_name = excluded.radar_name, mode = excluded.mode, title = excluded.title,
                description = excluded.description, price = excluded.price, landed_cost = excluded.landed_cost,
                fees = excluded.fees, optimal_price = excluded.optimal_price, net_profit = excluded.net_profit,
                deal_score = excluded.deal_score, identified_model = excluded.identified_model,
                cex_search_query = excluded.cex_search_query, cex_box_name = excluded.cex_box_name,
                cex_cash = excluded.cex_cash, cex_voucher = excluded.cex_voucher, cex_sell = excluded.cex_sell,
                item_url = excluded.item_url, checkout_url = excluded.checkout_url, image_url = excluded.image_url,
                kit_folder = COALESCE(NULLIF(deals.kit_folder, ''), excluded.kit_folder),
                seller_city = excluded.seller_city, is_local = excluded.is_local,
                fast_msg = excluded.fast_msg, haggle_msg = excluded.haggle_msg, seo_title = excluded.seo_title,
                seo_description = excluded.seo_description, value_add_tip = excluded.value_add_tip,
                image_urls_json = excluded.image_urls_json, visual_notes = excluded.visual_notes,
                deal_type = excluded.deal_type, recommended_offer = excluded.recommended_offer,
                status = CASE WHEN deals.status IN ('bought', 'sold') THEN deals.status ELSE 'alerted' END""",
            (
                deal_id,
                platform.upper(),
                str(item_id),
                target.get("id", "radar"),
                target.get("name", ""),
                target.get("mode", "resale"),
                title,
                (description or "")[:1000],
                float(price),
                float(landed_cost),
                float(fees),
                float(optimal_price),
                float(net_profit),
                int(ai_data.get("deal_score", 8)),
                ai_data.get("identified_model", title),
                ai_data.get("cex_search_query", ""),
                (cex_data or {}).get("box_name", ""),
                float((cex_data or {}).get("cash", 0)),
                float((cex_data or {}).get("voucher", 0)),
                float((cex_data or {}).get("sell", 0)),
                item_url,
                checkout_url or item_url,
                image_url or "",
                kit_folder,
                seller_city,
                1 if is_local else 0,
                ai_data.get("fast_buy_message", ""),
                ai_data.get("haggle_message", ""),
                ai_data.get("identified_model", title[:48]),
                "",
                ai_data.get("value_add_tip", ""),
                json.dumps(urls_list[:6], ensure_ascii=False),
                ai_data.get("visual_notes", ""),
                deal_type,
                rec_offer
            )
        )
        conn.commit()

        # Asegurar que el deal guardado tiene su embedding registrado sin llamada extra si item_vec ya existe
        try:
            item_key = f"{platform.upper()}:{item_id}"
            save_item_embedding(
                conn, item_key, target.get("id", ""),
                vector_or_text=item_vec if item_vec is not None else f"{title}\n{description}",
                model="models/gemini-embedding-2",
                verdict_json=json.dumps(ai_data, ensure_ascii=False) if ai_data else "",
                title=title, price=price,
                seller_id=seller_id,
                precomputed_vec=item_vec
            )
        except Exception:
            pass
    except Exception as e:
        print(f"[WARN] Error guardando deal en BD ({deal_id}): {e}")


# ==================== ALERTAS DISCORD CON 2ª NOTIFICACIÓN PUSH DE VEREDICTO IA ====================

SCORE_BADGES = {
    10: "🟢 NIVEL 1 • ARBITRAJE PURO CEX (RIESGO CERO / EFECTIVO DIRECTO)",
    9: "🔵 NIVEL 2 • CHOLLO CON COLCHÓN CEX (ALTA RENTABILIDAD P2P)",
    8: "🟣 NIVEL 3 • OPORTUNIDAD DE MERCADO / LOTE (REVENTA P2P)",
    7: "🤝 7/10 • OPORTUNIDAD DE CHOLLO (OFERTA FÁCIL -15%)",
    6: "🎯 6/10 • OPORTUNIDAD DE CHOLLO (OFERTA AGRESIVA)"
}


def send_flash_discord_alert(
    platform, item_id, title, price, landed_cost=0.0, item_url="",
    image_url="", seller_city="España", is_local=False, target=None
):
    """
    Fase 1 ('Flash Alert' sin IA, ~50ms):
    - Solo se dispara si el anuncio está dentro del rango de COMPRA DIRECTA (`price <= direct_buy_max_price`).
      (Si el anuncio es más caro y solo se escanea para hacer oferta, espera a que la IA lo verifique).
    - Incluye el enlace directo y de Checkout en la primera línea para abrirlo con 1 toque desde la notificación.
    """
    if isinstance(target, dict):
        direct_cap = float(target.get("direct_buy_max_price") or target.get("max_price", 9999))
        if price > direct_cap:
            return None

    clean_item_url, clean_checkout_url = normalize_and_build_links(platform, item_id, item_url)
    clean_t = re.sub(r"\s+", " ", (title or "").strip())
    loc_str = f" 🔥 **EN MANO ({seller_city})**" if is_local else f" ({seller_city})"
    user_ping = f"<@{ALERT_USER_ID}> " if ALERT_USER_ID else ""

    deeplink = build_native_deeplink(platform, clean_item_url, item_id)
    app_link_str = f"📲 **App Directa:** [Abrir en App Nativa]({deeplink})\n" if deeplink else ""

    flash_content = (
        f"⚡ **[FLASH SIN IA] {clean_t} — {price:.0f}€** {user_ping}\n"
        f"{app_link_str}"
        f"👉 **ABRIR ANUNCIO:** {clean_item_url}\n"
        f"⏳ *Puesto en casa: ~{landed_cost:.0f}€{loc_str} • Auditando con IA (recibirás 2ª notificación en 3s con la decisión)...*"
    )

    btn_row = [
        {"type": 2, "style": 5, "label": f"⚡ Abrir Anuncio ({price:.0f}€)", "url": clean_item_url}
    ]
    if clean_checkout_url and clean_checkout_url != clean_item_url:
        btn_row.append({"type": 2, "style": 5, "label": "💳 Checkout Directo 1-Tap", "url": clean_checkout_url})
    btn_row.append({"type": 2, "style": 4, "label": "Descartar", "emoji": {"name": "🚫"}, "custom_id": f"deal:hide:flash_{item_id}"})

    flash_components = [{"type": 1, "components": btn_row}]

    if BOT_TOKEN and ALERTS_CHANNEL_ID:
        bot_api_url = f"https://discord.com/api/v10/channels/{ALERTS_CHANNEL_ID}/messages"
        bot_headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        try:
            r = HTTP_SESSION.post(
                bot_api_url,
                headers=bot_headers,
                json={"content": flash_content, "components": flash_components},
                timeout=6
            )
            if r.status_code in (200, 201):
                msg_id = r.json().get("id")
                if msg_id:
                    return {"via": "bot", "message_id": str(msg_id)}
        except Exception as e:
            print(f"[WARN] Fallo enviando Flash Alert por Bot API: {e}")

    if WEBHOOK_URL and "http" in WEBHOOK_URL:
        sep = "&" if "?" in WEBHOOK_URL else "?"
        wh_wait_url = f"{WEBHOOK_URL}{sep}wait=true"
        try:
            r = HTTP_SESSION.post(
                wh_wait_url,
                json={"content": flash_content},
                timeout=6
            )
            if r.status_code in (200, 201):
                msg_id = r.json().get("id")
                if msg_id:
                    return {"via": "webhook", "message_id": str(msg_id)}
        except Exception as e:
            print(f"[WARN] Fallo enviando Flash Alert por Webhook: {e}")

    return None


def resolve_flash_discord_alert(
    flash_message, approved=False, reason="", delete_on_reject=False,
    title="", price=0.0, item_url=""
):
    """
    Fase 2 en caso de RECHAZO tras un Flash Alert:
    - Envía un NUEVO mensaje (`POST`) para que a tu móvil le salte una 2ª notificación push en tiempo real
      ("🛑 ¡CANCELA LA COMPRA! DESCARTADO POR IA") mientras estás en la pantalla de pago de Vinted/Wallapop.
    - Además elimina o tacha el mensaje Flash original.
    """
    if not flash_message or not isinstance(flash_message, dict):
        return
    msg_id = flash_message.get("message_id")
    via = flash_message.get("via")
    if not msg_id:
        return

    clean_t = re.sub(r"\s+", " ", (title or "").strip())[:70]
    clean_reason = re.sub(r"\s+", " ", (reason or "No supera auditoría IA/CeX").strip())[:180]
    user_ping = f"<@{ALERT_USER_ID}> " if ALERT_USER_ID else ""

    stop_push_content = (
        f"🛑 **¡DETÉN LA COMPRA! RECHAZADO POR IA** ❌ {user_ping}\n"
        f"⚠️ **Motivo:** `{clean_reason}`\n"
        f"📦 ~~{clean_t} ({price:.0f}€)~~"
    )

    if via == "bot" and BOT_TOKEN and ALERTS_CHANNEL_ID:
        base_url = f"https://discord.com/api/v10/channels/{ALERTS_CHANNEL_ID}/messages"
        old_msg_url = f"{base_url}/{msg_id}"
        bot_headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        try:
            # 1. Borramos el mensaje Flash antiguo para no duplicar
            HTTP_SESSION.delete(old_msg_url, headers=bot_headers, timeout=5)
            # 2. Publicamos un POST NUEVO para forzar la notificación push en el móvil
            dismiss_comp = [
                {
                    "type": 1,
                    "components": [
                        {"type": 2, "style": 4, "label": "Borrar aviso", "emoji": {"name": "🚫"}, "custom_id": f"deal:hide:rej_{msg_id}"}
                    ]
                }
            ]
            HTTP_SESSION.post(
                base_url,
                headers=bot_headers,
                json={"content": stop_push_content, "components": dismiss_comp},
                timeout=6
            )
        except Exception as e:
            print(f"[WARN] Error enviando notificación Push de rechazo (Bot): {e}")

    elif via == "webhook" and WEBHOOK_URL and "http" in WEBHOOK_URL:
        base_wh = WEBHOOK_URL.split("?")[0].rstrip("/")
        old_msg_url = f"{base_wh}/messages/{msg_id}"
        try:
            HTTP_SESSION.delete(old_msg_url, timeout=5)
            HTTP_SESSION.post(
                WEBHOOK_URL,
                json={"content": stop_push_content},
                timeout=6
            )
        except Exception as e:
            print(f"[WARN] Error enviando notificación Push de rechazo (Webhook): {e}")


def send_discord_alert(
    conn, platform, item_id, title, description, price, landed_cost, fees,
    target, ai_data, cex_data, enhanced_cover_bytes=None, kit_folder="", optimal_price=0.0,
    charger_penalty=0.0, net_profit=0.0, item_url="", checkout_url=None, image_url="",
    seller_city="España", is_local=False, country_tag="ES", shipping_label="📦 Envío disponible",
    price_drop_info=None, image_urls=None, flash_message=None, fraud_banner="", seller=None,
    item_vec=None, seller_id=""
):
    plat = platform.upper()
    deal_id = f"{plat[:3]}_{item_id}"[:40]
    mode = target.get("mode", "resale")

    clean_item_url, clean_checkout_url = normalize_and_build_links(plat, item_id, item_url, checkout_url)

    score = int(ai_data.get("deal_score", 8))
    deal_type = ai_data.get("deal_type", "direct_chollo")
    recommended_offer = int(round(float(ai_data.get("recommended_offer", 0) or 0)))
    offer_landed_cost = float(ai_data.get("offer_landed_cost", landed_cost) or landed_cost)
    offer_disc_pct = int(ai_data.get("offer_discount_pct", 0) or 0)

    # Generar siempre el mensaje de oferta en el idioma del vendedor
    haggle_target_price = recommended_offer if recommended_offer > 0 else max(15, int(round(price * 0.84)))
    haggle_msg = generate_multilingual_haggle_msg(
        title, description, country_tag, haggle_target_price, ai_data.get("identified_model", title)
    )
    ai_data["haggle_message"] = haggle_msg

    s_id = seller_id or (str(seller.get("id") or seller.get("user_id") or "") if isinstance(seller, dict) else (str(seller) if seller else ""))
    register_deal_in_db(
        conn, deal_id, plat, item_id, target, title, description, price, landed_cost, fees,
        optimal_price, net_profit, ai_data, cex_data, clean_item_url, clean_checkout_url, image_url,
        kit_folder, seller_city=seller_city, is_local=is_local, image_urls=image_urls,
        item_vec=item_vec, seller_id=s_id
    )

    if deal_type == "high_risk_opportunity":
        score_badge = "⚠️ 7/10 • ALTO RIESGO / PRECIO ANÓMALO (REVISAR CON CAUTELA)"
    elif ai_data.get("deal_tier_label"):
        score_badge = ai_data["deal_tier_label"]
    else:
        score_badge = SCORE_BADGES.get(score, f"⭐ {score}/10 • CHOLLO VERIFICADO")
    identified_model = ai_data.get("identified_model", title)
    vis_conf = str(ai_data.get("visual_confidence", "alta")).upper()
    vis_notes = ai_data.get("visual_notes") or "Producto verificado visualmente en fotos."
    charger_warn = "\n⚠️ **OJO:** Sin cargador original (-12€ descontados del margen)" if charger_penalty > 0 else ""
    case_penalty = float(ai_data.get("case_penalty", 0.0) or 0.0)
    case_warn = "\n⚠️ **OJO:** Cartucho suelto sin caja (-12€ ajustados en PVP CeX)" if case_penalty > 0 else ""

    drop_banner = ""
    if price_drop_info:
        drop_banner = (
            f"📉 **¡BAJADA DE PRECIO DETECTADA!** Antes: ~~{price_drop_info['old_price']:.0f}€~~ "
            f"➔ Ahora: **{price:.0f}€** (**-{price_drop_info['drop_eur']:.0f}€ / -{price_drop_info['drop_pct']}%**)\n"
        )

    loc_badge = f"📍 **{seller_city}** 🔥 *(¡EN MANO SEVILLA!)*" if is_local else f"📍 {seller_city} ({country_tag})"
    ship_badge = f"{shipping_label}: +{fees:.1f}€" if fees > 0 else "🤝 Trato en mano / Envío Gratis (0€)"

    search_term_used = re.sub(r"\(.*?\)", "", ai_data.get("cex_search_query", identified_model[:35])).strip()
    if cex_data and cex_data.get("box_id"):
        cex_link = f"https://es.webuy.com/product-detail?id={urllib.parse.quote(cex_data['box_id'])}"
    else:
        cex_link = f"https://es.webuy.com/search?stext={urllib.parse.quote(search_term_used)}"

    base_cost_for_cex = (offer_landed_cost if deal_type == "offer_opportunity" else landed_cost) + charger_penalty + case_penalty

    if cex_data and cex_data.get("cash", 0) > 0:
        net_cash = cex_data["cash"] - base_cost_for_cex
        net_voucher = cex_data["voucher"] - base_cost_for_cex
        sign_c = "+" if net_cash >= 0 else ""
        sign_v = "+" if net_voucher >= 0 else ""
        safety_tag = "🟢 **GANANCIA DIRECTA EN ACTO**" if net_cash >= 5 else "🛡️ **100% AMORTIZADO EN CEX**"
        cost_ref_label = f"con oferta de {recommended_offer}€" if deal_type == "offer_opportunity" else "al precio actual"
        bundle_bullet = ""
        if ai_data.get("bundled_matches"):
            extras_lines = [f"  • {m['box_name'][:38]}: +{m['cash']:.0f}€ cash / +{m['voucher']:.0f}€ vale" for m in ai_data["bundled_matches"]]
            bundle_bullet = f"\n🎮 **Extras CeX sumados ({len(ai_data['bundled_matches'])}):**\n" + "\n".join(extras_lines)

        cex_field_val = (
            f"🏷️ **Ficha CeX:** `{cex_data['box_name']}`\n"
            f"💵 **Efectivo CeX:** **{cex_data['cash']:.0f}€** (`{sign_c}{net_cash:.0f}€` neto {cost_ref_label}) | "
            f"🎟️ **Vale CeX:** **{cex_data['voucher']:.0f}€** (`{sign_v}{net_voucher:.0f}€`) *(PVP CeX: {cex_data['sell']:.0f}€)*\n"
            f"{safety_tag}{bundle_bullet} — [Abrir Ficha Oficial CeX]({cex_link})"
        )
    else:
        cex_field_val = f"{target.get('cex_cash_ref', 'Sin referencia CeX')}\n[🔍 Buscar en CeX]({cex_link})"

    risk_banner = ""
    if deal_type == "high_risk_opportunity" or ai_data.get("is_anomalously_low"):
        risk_banner = (
            "🚨 **AVISO DE SEGURIDAD (PRECIO ANÓMALAMENTE BAJO):**\n"
            "El precio está muy por debajo de la cotización real en efectivo de CeX (posible anzuelo, estafa o cuenta hackeada). "
            "Paga **EXCLUSIVAMENTE con protección de envíos dentro de la aplicación**. Jamás aceptes Bizum, transferencia bancaria ni trato externo.\n"
        )
    fb_block = f"{risk_banner}\n{fraud_banner}\n" if (risk_banner or fraud_banner) else ""
    seller_line = ""
    if seller and isinstance(seller, dict) and seller.get("name"):
        s_name = seller.get("name")
        s_parts = []
        if seller.get("rating") is not None:
            r_val = float(seller["rating"])
            s_parts.append(f"{r_val:.1f}%" if r_val > 5.0 else f"⭐ {r_val:.1f}")
        if seller.get("sales") is not None:
            s_parts.append(f"{seller['sales']} ventas/opiniones")
        if seller.get("age_days") is not None:
            s_parts.append(f"{seller['age_days']:.0f}d")
        extra_str = f" ({' · '.join(s_parts)})" if s_parts else ""
        seller_line = f"👤 **Vendedor:** `{s_name}`{extra_str}\n"

    ai_text = (
        f"{fb_block}"
        f"{drop_banner}"
        f"🏆 **Rating:** **`{score_badge}`**\n"
        f"🔎 **Modelo Verificado:** `{identified_model}` *(Confianza visual: **{vis_conf}**)*\n"
        f"👁️ **Inspección Fotos:** {vis_notes}\n"
        f"{seller_line}"
        f"💡 **Tip:** {ai_data.get('value_add_tip', 'Oportunidad rápida')}{charger_warn}{case_warn}"
    )

    fast_msg = ai_data.get("fast_buy_message") or (
        "Hola! Me interesa, funciona todo bien? Si está disponible te lo compro ahora mismo por la aplicación."
    )

    # Si es un 10/10, el enlace principal lleva directo al Checkout 1-Tap; en caso contrario lleva directo al anuncio
    primary_action_url = clean_checkout_url if (score == 10 and deal_type not in ("offer_opportunity", "high_risk_opportunity")) else clean_item_url
    user_ping = f"<@{ALERT_USER_ID}> " if ALERT_USER_ID else ""

    # ==================== CABECERA DISTINTA PARA OPORTUNIDAD DE CHOLLO (HACER OFERTA) ====================
    if deal_type == "offer_opportunity":
        header_title = (
            f"🤝 [OPORTUNIDAD DE CHOLLO • {plat}] ({score}/10): {identified_model[:42]} "
            f"— Ofertar {recommended_offer}€ (Anuncio: {price:.0f}€) ➔ +{net_profit:.0f}€"
        )
        color = PLATFORM_COLORS["OFFER_OPPORTUNITY"]
        cost_field_name = "🎯 Estrategia de Oferta"
        cost_field_val = (
            f"🏷️ **Anuncio:** ~~{price:.0f}€~~ ➔ **Ofertar: `{recommended_offer}€`** (**-{offer_disc_pct}%**)\n"
            f"💳 **Puesto en casa si acepta:** **{offer_landed_cost:.0f}€**"
        )
        profit_block = (
            f"💰 **Reventa Rápida (<48h):** **{optimal_price:.0f}€** *(Beneficio limpio si acepta: **`+{net_profit:.0f}€`**)*\n"
            f"🗣️ **Mensaje de Oferta Listo (1 toque para copiar):**\n```text\n{haggle_msg}\n```"
        )
        block_title = "🤝 Plan de Regateo y Rentabilidad"
        push_notification_content = (
            f"🤝 **[OPORTUNIDAD DE CHOLLO • OFERTAR {recommended_offer}€]** ({score}/10) {user_ping}\n"
            f"👉 **ABRIR ANUNCIO PARA OFERTAR:** {clean_item_url}\n"
            f"💡 **{identified_model}** listado a **{price:.0f}€** • Lanza oferta de **{recommended_offer}€** (-{offer_disc_pct}%) ➔ **Ganas +{net_profit:.0f}€ limpios**"
        )
    elif deal_type == "high_risk_opportunity":
        header_title = f"⚠️ [ALTO RIESGO • {plat}] ({score}/10): {identified_model[:50]} — {price:.0f}€ (Precio Anómalo)"
        color = 0xE67E22
        cost_field_name = "💳 Coste Puesto en Casa"
        cost_field_val = f"**{landed_cost:.0f}€** *(Anuncio: {price:.0f}€)*"
        profit_block = (
            f"💰 **Reventa Teórica:** **{optimal_price:.0f}€** *(Margen teórico: **`+{net_profit:.0f}€`**)*\n"
            f"⚠️ **Atención:** Proceder solo con pago protegido por la app y verificar el perfil del vendedor.\n"
            f"💬 **Mensaje Consulta:**\n```text\n{fast_msg}\n```"
        )
        block_title = "⚠️ Evaluación de Riesgo y Rentabilidad"
        push_notification_content = (
            f"⚠️ **[ALTO RIESGO • PRECIO ANÓMALO]** ({score}/10) {identified_model} a **{price:.0f}€** {user_ping}\n"
            f"👉 **REVISAR CON CAUTELA:** {clean_item_url}\n"
            f"⚠️ Margen de +{net_profit:.0f}€ pero precio muy bajo. Pagar solo con protección dentro de la app."
        )
    elif mode == "personal":
        header_title = f"💾 [SERVIDOR PERSONAL • {plat}] ({score}/10): {identified_model} — {price:.0f}€ (Ahorras +{net_profit:.0f}€)"
        color = PLATFORM_COLORS["PERSONAL"]
        cost_field_name = "💳 Coste Puesto en Casa"
        cost_field_val = f"**{landed_cost:.0f}€** *(Anuncio: {price:.0f}€)*"
        profit_block = (
            f"🏷️ **Valor normal de mercado:** **{optimal_price:.0f}€** | **Ahorro neto real:** **`+{net_profit:.0f}€`**\n"
            f"💬 **Mensaje rápido:**\n```text\n{fast_msg}\n```"
        )
        block_title = "🖥️ Análisis de Ahorro para tu Servidor"
        push_notification_content = (
            f"💾 **[CHOLLO SERVIDOR • {score}/10] {identified_model} — {price:.0f}€ (Ahorras +{net_profit:.0f}€)** {user_ping}\n"
            f"👉 **IR AL ANUNCIO:** {primary_action_url}"
        )
    else:
        emoji_plat = "🚨" if score == 10 else {"WALLAPOP": "🔥", "VINTED": "💎", "MILANUNCIOS": "🟢", "CASHCONVERTERS": "🏪", "EBAY": "🌐", "TODOCOLECCION": "🏺"}.get(plat, "🔥")
        header_title = f"{emoji_plat} [{plat}] ({score}/10): {identified_model[:55]} — {price:.0f}€ (Limpio +{net_profit:.0f}€)"
        color = 0xFFAA00 if score == 10 else (0x2ECC71 if is_local else PLATFORM_COLORS.get(plat, 0x3498DB))
        cost_field_name = "💳 Coste Puesto en Casa"
        cost_field_val = f"**{landed_cost:.0f}€** *(Anuncio: {price:.0f}€)*"
        profit_block = (
            f"💰 **Reventa Rápida (<48h):** **{optimal_price:.0f}€** *(Beneficio limpio: **`+{net_profit:.0f}€`**)*\n"
            f"📁 **Kit bajo demanda:** Pulsa **✅ ¡Comprado!** para limpiar fotos y generar anuncio\n"
            f"💬 **Mensaje Compra Rápida:**\n```text\n{fast_msg}\n```"
        )
        block_title = "📦 Rentabilidad y Acción Rápida"

        if score == 10:
            push_notification_content = (
                f"🚨 **¡SIGUE CON LA COMPRA! 10/10 COMPRA A CIEGAS (+{net_profit:.0f}€ LIMPIOS)** 🚨 {user_ping}\n"
                f"⚡ **ABRIR ANUNCIO:** {clean_item_url}\n"
                f"🔗 **Modelo:** `{identified_model}` ({price:.0f}€)"
            )
        else:
            push_notification_content = (
                f"✅ **¡APROBADO POR IA ({score}/10)! SIGUE CON LA COMPRA (+{net_profit:.0f}€ LIMPIOS)** {user_ping}\n"
                f"👉 **ABRIR ANUNCIO:** {clean_item_url}\n"
                f"🔗 **Modelo:** `{identified_model}` ({price:.0f}€)"
            )

    clean_embed_desc = re.sub(r"\s+", " ", html.unescape(description or "Sin descripción.")).strip()
    footer_text = f"Boti Sniper v4.3 (Visión Multimodal + Offer Sniper) • ID: {deal_id} • {datetime.now().strftime('%H:%M:%S')}".replace("\n", " ").strip()

    deeplink = build_native_deeplink(plat, clean_item_url, item_id)
    deeplink_header = f"📲 **App Directa:** [Abrir en App Nativa]({deeplink})\n\n" if deeplink else ""
    full_embed_desc = f"{deeplink_header}{clean_embed_desc[:220]}..." if len(clean_embed_desc) > 220 else f"{deeplink_header}{clean_embed_desc}"

    cex_cash_val = float(cex_data.get("cash", 0.0) or 0.0) if cex_data else 0.0
    cex_cash_net = float(ai_data.get("cex_cash_net", round(cex_cash_val - landed_cost, 2)))
    cex_coverage = float(ai_data.get("cex_coverage_pct", round((cex_cash_val / max(1.0, landed_cost)) * 100.0, 1)))

    p2p_net = float(ai_data.get("p2p_net_profit", net_profit))
    p2p_roi = float(ai_data.get("p2p_roi_pct", round((p2p_net / max(1.0, landed_cost)) * 100.0, 1)))
    p2p_market = float(ai_data.get("estimated_resale_price", optimal_price))

    sign_c = "+" if cex_cash_net >= 0 else ""
    sign_p = "+" if p2p_net >= 0 else ""

    dual_breakdown_field = {
        "name": "📊 Valoración Dual (CeX vs Reventa Particular)",
        "value": (
            f"💵 **CeX Efectivo:** **{cex_cash_val:.0f}€** (Neto: `{sign_c}{cex_cash_net:.0f}€` | Cubre: **{cex_coverage:.0f}%**)\n"
            f"📈 **Reventa P2P:** **{p2p_market:.0f}€** (Beneficio Neto: `{sign_p}{p2p_net:.0f}€` | ROI: **{p2p_roi:.0f}%**)"
        ),
        "inline": False
    }

    embed = {
        "title": header_title[:250],
        "url": primary_action_url,
        "color": color,
        "description": full_embed_desc.strip(),
        "fields": [
            {"name": "🎯 Radar", "value": target["name"], "inline": True},
            {"name": cost_field_name, "value": cost_field_val, "inline": True},
            {"name": "🚚 Entrega / Origen", "value": f"{loc_badge}\n{ship_badge}", "inline": True},
            {"name": "🧠 Auditoría Visual & Técnica IA", "value": ai_text[:1020], "inline": False},
            {"name": "🏪 Suelo de Amortización CeX España", "value": cex_field_val[:1020], "inline": False},
            dual_breakdown_field,
            {"name": block_title, "value": profit_block[:1020], "inline": False}
        ],
        "footer": {"text": footer_text}
    }

    if not enhanced_cover_bytes and image_url:
        embed["image"] = {"url": image_url}

    has_distinct_checkout = bool(clean_checkout_url and clean_checkout_url != clean_item_url)
    row1_buttons = []
    if deal_type == "offer_opportunity":
        row1_buttons.append({"type": 2, "style": 5, "label": f"🤝 Abrir y Ofertar {recommended_offer}€", "url": clean_item_url})
        if has_distinct_checkout:
            row1_buttons.append({"type": 2, "style": 5, "label": f"⚡ Checkout Directo ({price:.0f}€)", "url": clean_checkout_url})
    elif deal_type == "high_risk_opportunity":
        row1_buttons.append({"type": 2, "style": 5, "label": "⚠️ Inspeccionar Anuncio (Precaución)", "url": clean_item_url})
        if has_distinct_checkout:
            row1_buttons.append({"type": 2, "style": 5, "label": f"🛒 Pago Protegido ({price:.0f}€)", "url": clean_checkout_url})
    else:
        if has_distinct_checkout:
            row1_buttons.append({"type": 2, "style": 5, "label": f"⚡ Pago Directo 1-Tap ({price:.0f}€)", "url": clean_checkout_url})
            row1_buttons.append({"type": 2, "style": 5, "label": "💬 Abrir Anuncio / Chat", "url": clean_item_url})
        else:
            row1_buttons.append({"type": 2, "style": 5, "label": f"⚡ Comprar en {plat.capitalize()} ({price:.0f}€)", "url": clean_item_url})

    row1_buttons.append({"type": 2, "style": 5, "label": "🏪 Ficha CeX", "url": cex_link})

    components = [
        {"type": 1, "components": row1_buttons},
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "¡Comprado! -> Reventa", "emoji": {"name": "✅"}, "custom_id": f"deal:buy:{deal_id}"},
                {"type": 2, "style": 1, "label": "Mensajes / Regateo 1-Clic", "emoji": {"name": "📋"}, "custom_id": f"deal:msg:{deal_id}"},
                {"type": 2, "style": 2, "label": "Grados CeX", "emoji": {"name": "📊"}, "custom_id": f"deal:cex:{deal_id}"},
                {"type": 2, "style": 4, "label": "Descartar", "emoji": {"name": "🚫"}, "custom_id": f"deal:hide:{deal_id}"}
            ]
        }
    ]

    # Si había un mensaje Flash previo (Fase 1), lo borramos y enviamos un POST NUEVO
    # para que salte la 2ª notificación push en el móvil con el veredicto de la IA.
    if flash_message and isinstance(flash_message, dict) and flash_message.get("message_id"):
        f_id = flash_message["message_id"]
        f_via = flash_message.get("via")
        if f_via == "bot" and BOT_TOKEN and ALERTS_CHANNEL_ID:
            del_url = f"https://discord.com/api/v10/channels/{ALERTS_CHANNEL_ID}/messages/{f_id}"
            try:
                HTTP_SESSION.delete(del_url, headers={"Authorization": f"Bot {BOT_TOKEN}"}, timeout=5)
            except Exception:
                pass
        elif f_via == "webhook" and WEBHOOK_URL and "http" in WEBHOOK_URL:
            base_wh = WEBHOOK_URL.split("?")[0].rstrip("/")
            del_url = f"{base_wh}/messages/{f_id}"
            try:
                HTTP_SESSION.delete(del_url, timeout=5)
            except Exception:
                pass

    if BOT_TOKEN and ALERTS_CHANNEL_ID:
        bot_api_url = f"https://discord.com/api/v10/channels/{ALERTS_CHANNEL_ID}/messages"
        bot_headers = {"Authorization": f"Bot {BOT_TOKEN}"}
        try:
            if enhanced_cover_bytes:
                embed["image"] = {"url": "attachment://portada_chollo.jpg"}
                payload_json = json.dumps({"content": push_notification_content, "embeds": [embed], "components": components})
                mp = CurlMime()
                mp.addpart(name="payload_json", content_type="application/json", data=payload_json.encode("utf-8"))
                mp.addpart(name="files[0]", content_type="image/jpeg", filename="portada_chollo.jpg", data=enhanced_cover_bytes)
                r = requests.post(bot_api_url, headers=bot_headers, multipart=mp, timeout=15)
            else:
                r = HTTP_SESSION.post(
                    bot_api_url,
                    headers={**bot_headers, "Content-Type": "application/json"},
                    json={"content": push_notification_content, "embeds": [embed], "components": components},
                    timeout=10
                )
            if r.status_code in (200, 201):
                return
        except Exception as e:
            print(f"[WARN] Error enviando alerta por Bot API: {e}")

    if WEBHOOK_URL and "http" in WEBHOOK_URL:
        try:
            if enhanced_cover_bytes:
                embed["image"] = {"url": "attachment://portada_chollo.jpg"}
                payload_json = json.dumps({"content": push_notification_content, "embeds": [embed]})
                mp = CurlMime()
                mp.addpart(name="payload_json", content_type="application/json", data=payload_json.encode("utf-8"))
                mp.addpart(name="file", content_type="image/jpeg", filename="portada_chollo.jpg", data=enhanced_cover_bytes)
                requests.post(WEBHOOK_URL, multipart=mp, timeout=15)
            else:
                HTTP_SESSION.post(WEBHOOK_URL, json={"content": push_notification_content, "embeds": [embed]}, timeout=10)
        except Exception:
            pass