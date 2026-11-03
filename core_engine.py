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
import base64
import shutil
import hashlib
import sqlite3
import urllib.parse
from datetime import datetime
from curl_cffi import requests, CurlMime
from PIL import Image, ImageEnhance, ImageOps

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
ALERTS_CHANNEL_ID = os.getenv("ALERTS_CHANNEL_ID", "1564559740434911366").strip()
ALERT_USER_ID = os.getenv("ALERT_USER_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.5-flash").strip()
AI_ANALYST_MODEL = os.getenv("AI_ANALYST_MODEL", "gemini-2.5-flash").strip()
PROXY_URL = os.getenv("PROXY_URL", "").strip()

DB_PATH = os.getenv("DB_PATH", "/app/data/sniper.db" if os.path.exists("/app") else "./data/sniper.db")
CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config.json" if os.path.exists("/app/config.json") else "./config.json")
KITS_DIR = os.getenv("KITS_DIR", "/app/kits" if os.path.exists("/app") else "./kits")

LOCAL_KEYWORDS = [
    "sevilla", "mairena", "alcala", "alcalá", "viso", "carmona",
    "dos hermanas", "utrera", "camas", "tomares", "bormujos", "rinconada",
    "san juan de aznalfarache", "coria del rio", "gines", "castilleja"
]

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


# ==================== BASE DE DATOS Y CACHÉ ====================

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(KITS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
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
        CREATE TABLE IF NOT EXISTS ai_eval_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT,
            evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        c.execute("DELETE FROM market_seen_items WHERE seen_at < datetime('now', '-45 days')")
        c.execute("DELETE FROM seen_items WHERE seen_at < datetime('now', '-45 days')")
        c.execute("DELETE FROM vinted_seen_items WHERE seen_at < datetime('now', '-45 days')")
        c.execute("DELETE FROM cex_cache WHERE updated_at < datetime('now', '-3 days')")
        c.execute("DELETE FROM ai_eval_cache WHERE evaluated_at < datetime('now', '-7 days')")

        c.execute("CREATE INDEX IF NOT EXISTS idx_market_seen_at ON market_seen_items(seen_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_at)")
    except Exception as e:
        print(f"[WARN] Mantenimiento de BD: {e}")

    conn.commit()
    return conn


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


def is_duplicate_recent_deal(conn, platform, title, price, hours=24):
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
        for r_title, r_price in c.fetchall():
            r_norm = re.sub(r"[^a-z0-9]", "", html.unescape(r_title or "").lower())
            if r_norm == norm_t and abs(float(r_price or 0) - float(price or 0)) <= 10.0:
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
    if plat == "WALLAPOP":
        c.execute(
            "INSERT OR REPLACE INTO seen_items (item_id, title, price, seen_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (sid, title, p)
        )
    elif plat == "VINTED":
        c.execute(
            "INSERT OR REPLACE INTO vinted_seen_items (item_id, title, price, seen_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (sid, title, p)
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


def load_config(expand_for_offers=True):
    """
    Carga config.json y amplía automáticamente el techo de escaneo un +30% (offer_scan_multiplier)
    para que los snipers capten también anuncios candidatos a 'Oportunidad de Chollo (Hacer Oferta)'
    conservando `direct_buy_max_price` como el tope estricto de compra directa y Flash Alerts.
    """
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

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

def normalize_and_build_links(platform, item_id, item_url, checkout_url=None):
    """
    Corrige los enlaces de todas las plataformas:
    - En Vinted convierte dominios extranjeros (.fr, .it, .pt, .be) a vinted.es para abrir la App
      española directamente y genera el enlace real de Checkout 1-Tap (transaction/buy/new).
    - En Wallapop, eBay, Milanuncios y CashConverters asegura URLs limpias y directas.
    """
    plat = (platform or "").upper()
    sid = str(item_id or "").strip()
    clean_item_url = (item_url or "").strip()
    clean_checkout_url = (checkout_url or "").strip()

    if plat == "VINTED":
        if not sid and clean_item_url:
            m_id = re.search(r"/items/(\d+)", clean_item_url)
            if m_id:
                sid = m_id.group(1)
        if clean_item_url:
            clean_item_url = re.sub(r"https?://(?:www\.)?vinted\.[a-z.]+/", "https://www.vinted.es/", clean_item_url)
        elif sid:
            clean_item_url = f"https://www.vinted.es/items/{sid}"

        if sid:
            clean_checkout_url = f"https://www.vinted.es/transaction/buy/new?source_screen=item&transaction%5Bitem_id%5D={sid}"
        else:
            clean_checkout_url = clean_item_url

    elif plat == "WALLAPOP":
        if not clean_item_url and sid:
            clean_item_url = f"https://es.wallapop.com/item/{sid}"
        if not clean_checkout_url:
            clean_checkout_url = clean_item_url

    else:
        if not clean_checkout_url:
            clean_checkout_url = clean_item_url

    return clean_item_url, clean_checkout_url


def generate_multilingual_haggle_msg(title, description, country_tag, offer_price, model_name=""):
    """
    Detecta el idioma del anuncio (FR / IT / ES) y genera el mensaje de oferta persuasivo
    exacto por `offer_price`€ listo para copiar y pegar.
    """
    text_l = f"{title or ''} {description or ''}".lower()
    ctag = (country_tag or "ES").upper()
    offer_int = int(round(offer_price))

    is_fr = ctag in ("FR", "BE") or any(w in text_l for w in ("bonjour", "vend", "vends", "état", "etat", "avec", "boite", "boîte", "manette", "envoi"))
    is_it = ctag == "IT" or any(w in text_l for w in ("vendo", "ottimo", "stato", "scatola", "perfettamente", "spedizione", "senza", "funzionante"))

    if is_fr:
        return (
            f"Bonjour ! Je suis très intéressé. Si vous acceptez une offre à {offer_int}€, "
            f"je vous l'achète immédiatement via Vinted (paiement instantané). Merci beaucoup !"
        )
    if is_it:
        return (
            f"Ciao! Sono molto interessato. Se accetti un'offerta di {offer_int}€, "
            f"procedo subito con l'acquisto immediato tramite l'app. Grazie mille!"
        )
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
    "case for ", "cover for ", "box for ", "charger for ", "bag for ", "game for "
)

PRE_AI_DEFECT_TOKENS = (
    # Español + Anti-Estafa Pagos Externos
    "sin probar", "no funciona", "no enciende", "no carga", "no da imagen", "no da video", "no lee",
    "lector roto", "lector no funciona", "bisagra rota", "bisagra partida", "pantalla partida",
    "pantalla quebrada", "pantalla rota", "pantalla dañada", "pantalla en negro", "pantalla blanca",
    "mancha en pantalla", "lineas en pantalla", "líneas en pantalla", "cristal rajado", "cristal roto",
    "error e18", "error e32", "luz roja", "luz azul", "joycon drift", "con drift", "sin consola",
    "solo tablet", "solo la pantalla", "sin joycon", "sin joy-con", "sin joycons", "sin mandos",
    "cartucho vacio", "cartucho vacío", "falsificacion", "falsificación", "bloqueo icloud",
    "bloqueado icloud", "cuenta icloud", "para piezas", "para reparar", "a reparar", "averiado",
    "averiada", "defectuoso", "defectuosa", "tactil no funciona", "táctil no funciona",
    "solo la consola no", "busco ", "compro ", "pantalla superior a reemplazar",
    # Francés + Anti-Scam Vinted FR
    "hors service", "pour pieces", "pour pièces", "en panne", "ne fonctionne pas", "ne s'allume pas",
    "ne charge pas", "ecran hs", "écran hs", "ecran casse", "écran cassé", "ecran fissure",
    "écran fissuré", "fissure", "charniere cassee", "charnière cassée", "fonctionne partiellement",
    "ne repondent pas", "ne répondent pas", "plusieurs defauts", "plusieurs défauts",
    "changer la coque", "changer la batterie", "a reparer", "à réparer", "sans console",
    "sans joycon", "sans joy-con", "sans manette", "vendu seul sans",
    "virement bancaire", "virement bancaires", "uniquement via virement", "aucune remise en main propre",
    "ne pas acheter directement", "avant d'acheter", "avant d’acheter",
    # Italiano + Anti-Scam Vinted IT
    "non funzionante", "non si accende", "non carica", "per pezzi", "per parti", "pezzi di ricambio",
    "da riparare", "schermo rotto", "schermo crepato", "display rotto", "vetro rotto", "crepa",
    "cerniera rotta", "manca joystick", "senza joycon", "senza joy-con", "solo corpo tablet",
    "difettoso", "difettosa", "guasto", "guasta", "prima di acquistare", "non procedere", "bonifico bancario",
    # Portugués / Inglés
    "avariado", "avariada", "para pecas", "para peças", "ecra partido", "ecrã partido",
    "for parts", "not working", "broken screen", "cracked screen", "faulty", "spares or repair",
    "no joycons", "tablet only"
)

CONSOLE_RADAR_IDS = {
    "new_3ds_family", "switch_oled", "switch_std_lite",
    "ps_vita", "retro_handhelds", "steam_deck_handhelds", "ps5_consoles_gta6"
}

CONSOLE_HARDWARE_WORDS = (
    "consola", "console", "new 3ds", "new nintendo 3ds", "3ds xl", "3ds ll",
    "2ds xl", "2ds ll", "new 2ds", "switch oled", "switch lite",
    "ps vita 1000", "ps vita 2000", "psvita 1000", "psvita 2000", "pch-100", "pch-200",
    "gba sp", "advance sp", "ags-001", "ags-101", "game boy micro", "gameboy micro",
    "steam deck", "steamdeck", "rog ally", "legion go", "ps5", "playstation 5",
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


def passes_filters(title, description, price, target, global_blacklist, extra_text=""):
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

    # Detector anti-estafa de números de teléfono camuflados con guiones/espacios en Vinted
    if re.search(r"\b\d{3}[\s\-\.]\d{2}[\s\-\.]\d{2}[\s\-\.]\d{2,3}\b", text):
        return False

    for defect in PRE_AI_DEFECT_TOKENS:
        if defect in text:
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
    "carrying case", "pouch", "stand", "grip", "skin", "stylus", "volante",
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

STRICT_MODEL_TOKENS = (
    "a6000", "a6100", "a6400", "zv-e10", "zve10", "x-t10", "xt10", "x-t20", "xt20",
    "x-t30", "xt30", "x100", "g7x", "g9x", "g5x", "sx720", "sx730", "sx740", "rx100",
    "3060", "3070", "3080", "4060", "4070", "g29", "g920", "g923", "t300",
    "heartgold", "soulsilver", "platino", "blanco 2", "negro 2", "esmeralda",
    "cristal", "rojo fuego", "verde hoja", "ags-001", "ags-101", "micro",
    "dsi xl", "steam deck", "rog ally", "legion go", "paperwhite", "oasis"
)


def _normalize_cex_text(s):
    s = html.unescape(s or "").lower()
    s = re.sub(r"[\-_/+]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _box_matches_query_and_radar(bname, cat, clean_q, target=None, context_text="", includes_lens=True):
    bname_l = bname.lower()
    bname_norm = _normalize_cex_text(bname)
    cat_l = (cat or "").lower()
    q_norm = _normalize_cex_text(clean_q)
    ctx_norm = _normalize_cex_text(f"{clean_q} {context_text}")
    radar_id = (target or {}).get("id", "") if isinstance(target, dict) else ""

    if radar_id in RADAR_ALLOWED_CEX_CATEGORIES:
        allowed_cats = RADAR_ALLOWED_CEX_CATEGORIES[radar_id]
        if not any(ac in cat_l for ac in allowed_cats):
            return False

    if radar_id in CONSOLE_RADAR_IDS:
        if "juegos" in cat_l or "accesorios" in cat_l:
            return False
        bname_no_sin = re.sub(r"sin\s+(cargador|caja|lapiz|manual|dock|lector)", "", bname_l)
        bname_no_sin = re.sub(r"\+\s*joy-?con", "", bname_no_sin)
        if any(ak in bname_no_sin for ak in ("bolsa", "funda", "protector", "cargador", "mando", "dock", "volante", "stand", "grip", "cover plate")):
            return False
        for gt in VIDEO_GAME_TITLES_AND_TOKENS:
            gt_s = gt.strip()
            if len(gt_s) >= 4 and gt_s in ctx_norm and gt_s not in bname_norm:
                return False
        for spec_tok in SPECIAL_EDITION_CONSOLE_TOKENS:
            if spec_tok in bname_l and spec_tok not in ctx_norm:
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

    if "switch" in q_norm or radar_id in ("switch_oled", "switch_std_lite"):
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
        sin_caja_or_b = [
            b for b in top_relevant
            if any(k in b["box_name"].lower() for k in ("sin caja", "sincaja", "grado b", ", b", "/b", "unboxed"))
            and not any(k in b["box_name"].lower() for k in (", c", "/c", "grado c", "rebajada"))
        ]
        rebajada_list = [
            b for b in top_relevant
            if any(k in b["box_name"].lower() for k in ("rebajada", ", c", "/c", "grado c"))
        ]
        boxed_standard = [
            b for b in top_relevant
            if any(k in b["box_name"].lower() for k in ("con caja", ", caja", "grado b", ", b", "/b"))
            and not any(k in b["box_name"].lower() for k in (", a", "/a", "grado a"))
        ]

        if not includes_box and sin_caja_or_b:
            sin_caja_or_b.sort(key=lambda x: x["cash"])
            best_box = sin_caja_or_b[0]
        elif includes_box and boxed_standard:
            boxed_standard.sort(key=lambda x: x["cash"])
            best_box = boxed_standard[0]
        elif sin_caja_or_b:
            sin_caja_or_b.sort(key=lambda x: x["cash"])
            best_box = sin_caja_or_b[0]
        elif rebajada_list:
            rebajada_list.sort(key=lambda x: x["cash"])
            best_box = rebajada_list[0]
        else:
            non_a = [b for b in top_relevant if not any(k in b["box_name"].lower() for k in (", a", "grado a"))]
            pool = non_a if non_a else top_relevant
            pool.sort(key=lambda x: x["cash"])
            best_box = pool[0]

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


# ==================== SCRAPING MULTI-MERCADO ====================

def fetch_milanuncios(rotator, query_text, min_price, max_price):
    params = {
        "s": query_text,
        "orden": "nuevos",
        "desde": str(int(min_price)),
        "hasta": str(int(max_price))
    }
    url = f"https://www.milanuncios.com/anuncios/?{urllib.parse.urlencode(params)}"
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
    profile = rotator.get_chrome_profile()

    try:
        r = requests.get(url, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
        if r.status_code != 200:
            return []

        m = re.search(r'window\.__INITIAL_PROPS__\s*=\s*JSON\.parse\("((?:\\.|[^"\\])*)"\)', r.text)
        if not m:
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
                "shipping_label": "📦 Milanuncios Express"
            })
        return results
    except Exception as e:
        print(f"[WARN] Error en Milanuncios '{query_text}': {e}")
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
    try:
        r = requests.get(url, headers=headers, proxies=proxies, impersonate=profile["impersonate"], timeout=15)
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
                "shipping_label": "🏪 Compra Directa Web + Garantía"
            })
        return results
    except Exception as e:
        print(f"[WARN] Error en CashConverters '{query_text}': {e}")
        return []


def fetch_todocoleccion(rotator, query_text, min_price, max_price):
    params = {"from": "top", "bu": query_text, "O": "r"}
    url = f"https://www.todocoleccion.net/buscador?{urllib.parse.urlencode(params)}"
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    try:
        r = requests.get(
            url,
            proxies=proxies,
            impersonate=rotator.get_safari_impersonation(),
            timeout=15
        )
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
                "shipping_label": "📦 Envío Correos TC"
            })
        return results
    except Exception as e:
        print(f"[WARN] Error en TodoColección '{query_text}': {e}")
        return []


class EbaySessionManager:
    def __init__(self):
        self.session = None
        self.last_init = 0

    def get_session(self):
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
    url = f"https://www.ebay.es/sch/i.html?_nkw={encoded_q}&LH_BIN=1&_sop=10&_rss=1"
    s = _ebay_mgr.get_session()

    try:
        r = s.get(url, timeout=15)
        if r.status_code != 200 or len(r.text) < 5000:
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
                "shipping_label": "🌐 Envío eBay"
            })
        return results
    except Exception as e:
        print(f"[WARN] Error en eBay ES '{query_text}': {e}")
        return []


# ==================== DESCARGA Y PROCESADO DE IMÁGENES ====================

def download_item_images(image_urls, rotator=None, max_images=3, force_safari=False):
    if not image_urls:
        return []
    raw_photos = []
    imp = "safari17_0"
    if not force_safari and rotator:
        try:
            imp = rotator.get_chrome_profile().get("impersonate", "safari17_0")
        except Exception:
            imp = "safari17_0"
    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

    for u in image_urls[:max_images]:
        if not u:
            continue
        try:
            resp = requests.get(u, proxies=proxies, impersonate=imp, timeout=9)
            if resp.status_code == 200 and resp.content and len(resp.content) > 1500:
                raw_photos.append(resp.content)
        except Exception:
            pass
    return raw_photos


def prepare_image_for_ai(img_bytes, max_dim=640):
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            img = img.convert("RGB")
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=78, optimize=True)
            return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:
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


# ==================== AUDITORÍA MULTIMODAL GEMINI 2.5 FLASH ====================

def analyze_and_generate_kit(
    platform, title, description, price, landed_cost, is_foreign, target,
    conn=None, health_monitor=None, extra_meta="", raw_images=None
):
    if not GEMINI_API_KEY and not OPENROUTER_API_KEY:
        print("[ERROR] Falta GEMINI_API_KEY u OPENROUTER_API_KEY.")
        return None

    if not isinstance(target, dict):
        if isinstance(conn, dict):
            target, conn = conn, None
        else:
            target = {}

    clean_title_str = html.unescape(title or "").strip()
    clean_desc_raw = html.unescape(description or "").strip()
    clean_desc_raw = re.sub(r"https?://\S+", "", clean_desc_raw)
    clean_desc = re.sub(r"\s+", " ", clean_desc_raw).strip()[:1000]

    img_sig = "noimg"
    if raw_images and len(raw_images) > 0 and raw_images[0]:
        img_sig = hashlib.md5(raw_images[0][:4096]).hexdigest()[:10]

    cache_seed = f"v43_{target.get('id', '')}_{clean_title_str.lower()}_{int(price)}_{clean_desc[:180].lower()}_{img_sig}"
    cache_key = hashlib.md5(cache_seed.encode("utf-8", errors="ignore")).hexdigest()

    if conn:
        try:
            c = conn.cursor()
            c.execute("SELECT result_json FROM ai_eval_cache WHERE cache_key = ?", (cache_key,))
            cached_row = c.fetchone()
            if cached_row:
                return json.loads(cached_row[0])
        except Exception:
            pass

    b64_images = []
    for img_b in (raw_images or [])[:3]:
        b64_str = prepare_image_for_ai(img_b)
        if b64_str:
            b64_images.append(b64_str)

    mode = target.get("mode", "resale")
    valuation_rules = target.get("ai_valuation_guide", "")[:1400]
    meta_suffix = f" | Meta: {extra_meta.strip()}" if extra_meta else ""
    num_imgs = len(b64_images)

    if mode == "personal":
        prompt = (
            f"Eres el Auditor Jefe de Hardware para un Servidor Linux Homelab ({platform}).\n"
            f"Categoría buscada: {target['name']}\n"
            f"Título: {clean_title_str}\n"
            f"Coste puesto en casa: {landed_cost:.0f}€ (Anuncio: {price:.0f}€){meta_suffix}\n"
            f"Descripción completa: {clean_desc or 'Sin descripción'}\n"
            f"Fotos adjuntas: {num_imgs}\n"
            f"Reglas del radar: {valuation_rules}\n\n"
            f"INSTRUCCIONES DE INSPECCIÓN VISUAL Y TÉCNICA:\n"
            f"1. Mira las fotos adjuntas y lee las etiquetas del disco. Verifica que cumpla las reglas del radar (>1TB si es HDD, salud SMART buena).\n"
            f"2. IMPORTANTE: Evalúa is_valid_deal=true si el hardware es el modelo correcto y está en buen estado físico/SMART, INCLUSO SI el precio actual es algo elevado (ya que nuestro motor calculará después si merece la pena hacer una oferta a la baja).\n"
            f"Responde SOLO un JSON válido con esta estructura exacta:\n"
            f'{{"is_valid_deal": bool, "is_pro_seller": bool, "has_defect_or_missing_parts": bool, '
            f'"visual_confidence": "alta"|"media"|"baja", "identified_model": "", "cex_search_query": "", '
            f'"visual_notes": "", "reject_reason": "", "estimated_resale_price": int, '
            f'"value_add_tip": "", "fast_buy_message": ""}}'
        )
    else:
        prompt = (
            f"Eres un Auditor Senior de Arbitraje de Segunda Mano en España ({platform}) con respaldo de CeX (WeBuy España).\n"
            f"Radar objetivo: {target['name']}\n"
            f"Título del anuncio: {clean_title_str}\n"
            f"Precio anuncio: {price:.0f}€ | Coste total puesto en casa: {landed_cost:.0f}€{meta_suffix}\n"
            f"Descripción completa del vendedor: {clean_desc or 'Sin descripción'}\n"
            f"Imágenes adjuntas del producto: {num_imgs}\n"
            f"Reglas específicas del radar: {valuation_rules}\n\n"
            f"REGLAS ESTRICTAS DE INSPECCIÓN VISUAL Y DETECCIÓN DE FALSOS POSITIVOS (OBLIGATORIO):\n"
            f"1. INSPECCIONA LAS FOTOS ADJUNTAS AL DETALLE: Verifica qué se vende realmente.\n"
            f"   - Si las fotos muestran solo un videojuego, funda, caja vacía o accesorio suelto -> pon is_valid_deal=false.\n"
            f"   - En Nintendo Switch: si aparece SOLO la pantalla/tablet sin Joy-Cons -> pon is_valid_deal=false y has_defect_or_missing_parts=true.\n"
            f"   - En Mac Mini: si es Intel antiguo (2010-2018) -> pon is_valid_deal=false.\n"
            f"2. DEFECTOS O ESTAFAS EN CUALQUIER IDIOMA (ES/FR/IT/PT/EN): Si pide pago por transferencia ('virement bancaire', 'bonifico'), escribir por WhatsApp antes de comprar ('prima di acquistare', 'avant d\\'acheter'), o tiene averías/grietas/drift/bloqueo iCloud -> pon is_valid_deal=false y has_defect_or_missing_parts=true.\n"
            f"3. REGLA CLAVE PARA OPORTUNIDADES DE OFERTA: Si el producto es 100% auténtico, completo, sin defectos y no es estafa, pon is_valid_deal=true AUNQUE su precio actual supere ligeramente el tope de compra directa (nuestro motor matemático calculará si es un chollo directo o una Oportunidad para Hacer Oferta).\n"
            f"4. CONSULTA CEX EXACTA ('cex_search_query'): Escribe el nombre canónico limpio del modelo exacto verificado en fotos.\n\n"
            f"Responde ÚNICAMENTE un objeto JSON válido con estas claves exactas:\n"
            f'{{"is_valid_deal": bool, "is_pro_seller": bool, "has_defect_or_missing_parts": bool, '
            f'"missing_original_charger": bool, "includes_original_box": bool, "includes_kit_lens": bool, '
            f'"visual_confidence": "alta"|"media"|"baja", "identified_model": "", "cex_search_query": "", '
            f'"visual_notes": "", "reject_reason": "", "estimated_resale_price": int, '
            f'"value_add_tip": "", "fast_buy_message": ""}}'
        )

    parsed_result = None

    if GEMINI_API_KEY:
        preferred_model = AI_ANALYST_MODEL if ("gemini" in AI_ANALYST_MODEL.lower() and "/" not in AI_ANALYST_MODEL) else "gemini-2.5-flash"
        candidate_models = [preferred_model]
        for fallback_m in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            if fallback_m not in candidate_models:
                candidate_models.append(fallback_m)

        parts = [{"text": prompt}]
        for b64_img in b64_images:
            parts.append({
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": b64_img
                }
            })

        for model in candidate_models:
            thinking_budget = 256 if "2.5-flash" in model and "lite" not in model else 0
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1,
                    "maxOutputTokens": 650,
                    "thinkingConfig": {"thinkingBudget": thinking_budget}
                }
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
            try:
                resp = requests.post(url, json=payload, timeout=22)
                if resp.status_code == 200:
                    candidates = resp.json().get("candidates", [])
                    if candidates:
                        for p in candidates[0].get("content", {}).get("parts", []):
                            if "text" in p and not p.get("thought", False):
                                clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", p["text"].strip(), flags=re.MULTILINE)
                                parsed_result = json.loads(clean)
                                break
                    if parsed_result:
                        break
                elif resp.status_code == 404:
                    continue
                elif health_monitor:
                    health_monitor.notify_ai_error(resp.status_code, resp.text)
                    break
            except Exception as e:
                print(f"[WARN] Error Gemini ({model} / {platform}): {e}")
                break

    if not parsed_result and OPENROUTER_API_KEY:
        fallback_model = AI_MODEL if "/" in AI_MODEL else "google/gemini-2.5-flash"
        user_content = [{"type": "text", "text": prompt}]
        for b64_img in b64_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
            })
        payload = {
            "model": fallback_model,
            "messages": [{"role": "user", "content": user_content}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "max_tokens": 550
        }
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://homelab.local"
        }
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=22)
            if resp.status_code == 200:
                raw_text = resp.json()["choices"][0]["message"].get("content", "")
                clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)
                parsed_result = json.loads(clean)
            elif health_monitor:
                health_monitor.notify_ai_error(resp.status_code, resp.text)
        except Exception as e:
            print(f"[WARN] Error OpenRouter ({platform}): {e}")

    if parsed_result and conn:
        try:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO ai_eval_cache (cache_key, result_json, evaluated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (cache_key, json.dumps(parsed_result, ensure_ascii=False))
            )
            conn.commit()
        except Exception:
            pass

    return parsed_result


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

    # ==================== MODO REVENTA CON RESPALDO CEX ESPAÑA ====================
    if not cex_data or float(cex_data.get("cash", 0) or 0) <= 0 or float(cex_data.get("sell", 0) or 0) <= 0:
        return False, 4, 0.0, 0.0, 0.0, f"Sin respaldo de compra en CeX España para '{identified or 'este modelo'}'"

    missing_charger = bool(ai_data.get("missing_original_charger", False))
    charger_penalty = 12.0 if missing_charger else 0.0
    effective_cost = round(landed_cost + charger_penalty, 2)

    cex_cash = float(cex_data["cash"])
    cex_voucher = float(cex_data["voucher"])
    cex_sell = float(cex_data["sell"])

    cex_net_cash = round(cex_cash - effective_cost, 2)
    cex_net_voucher = round(cex_voucher - effective_cost, 2)

    cex_realistic_cap = round(cex_sell * 0.84)
    if ai_est_price > 0:
        optimal_price = max(cex_cash, min(ai_est_price, cex_realistic_cap))
    else:
        optimal_price = max(cex_cash, cex_realistic_cap)

    net_resale_profit = round(optimal_price - effective_cost - 2.0, 2)
    best_net_profit = round(max(net_resale_profit, cex_net_cash), 2)
    req_margin = float(target.get("min_profit_eur", min_profit))

    # 1. ¿Es un CHOLLO DIRECTO (>= 8/10) comprando al precio actual del anuncio?
    if (
        price <= direct_max_cap
        and cex_net_cash >= -2.0
        and cex_net_voucher >= 8.0
        and best_net_profit >= req_margin
    ):
        roi_pct = (best_net_profit / max(effective_cost, 1.0)) * 100.0
        if (
            vis_conf == "alta"
            and not missing_charger
            and (cex_net_cash >= 20.0 or (cex_net_cash >= 12.0 and best_net_profit >= 75.0))
            and best_net_profit >= 55.0
            and roi_pct >= 45.0
        ):
            score = 10
        elif (
            vis_conf in ("alta", "media")
            and (cex_net_cash >= 8.0 or (cex_net_cash >= 4.0 and best_net_profit >= 55.0))
            and best_net_profit >= 45.0
            and roi_pct >= 32.0
        ):
            score = 9
        elif best_net_profit >= req_margin and roi_pct >= 20.0:
            score = 8
        else:
            score = 7

        if score >= min_score:
            ai_data["deal_score"] = score
            ai_data["deal_type"] = "direct_chollo"
            ai_data["recommended_offer"] = 0
            return True, score, optimal_price, best_net_profit, charger_penalty, "OK"

    # 2. Evaluación como OPORTUNIDAD DE CHOLLO (HACER OFERTA A LA BAJA)
    # Calculamos la oferta máxima que cumple a la vez:
    # a) 100% amortizado en efectivo CeX (cex_cash >= offer_landed + charger_penalty)
    # b) Beneficio neto de reventa >= req_margin (+35€ limpios)
    # c) No superar el tope del submodelo (`direct_max_cap`)
    if allow_offers:
        max_landed_cex_safe = cex_cash - charger_penalty
        max_landed_profit_safe = optimal_price - req_margin - 2.0 - charger_penalty
        ideal_landed = min(max_landed_cex_safe, max_landed_profit_safe)
        recommended_offer = int(min(direct_max_cap, round(ideal_landed - est_fees)))

        # Si el anuncio está ligeramente por debajo o por encima pero necesita al menos 8€ de rebaja
        if recommended_offer >= (price - 5):
            recommended_offer = int(price * 0.85)

        if 18 <= recommended_offer < price:
            disc_eur = price - recommended_offer
            disc_pct = (disc_eur / price) * 100.0

            if 6.0 <= disc_pct <= max_haggle_discount_pct:
                offer_landed = round(recommended_offer + est_fees, 2)
                offer_eff_cost = round(offer_landed + charger_penalty, 2)
                offer_resale_profit = round(optimal_price - offer_eff_cost - 2.0, 2)
                offer_cex_net = round(cex_cash - offer_eff_cost, 2)
                offer_best_profit = round(max(offer_resale_profit, offer_cex_net), 2)

                if offer_cex_net >= -2.0 and offer_best_profit >= req_margin:
                    score = 7 if disc_pct <= 20.0 else 6
                    ai_data["deal_score"] = score
                    ai_data["deal_type"] = "offer_opportunity"
                    ai_data["recommended_offer"] = recommended_offer
                    ai_data["offer_landed_cost"] = offer_landed
                    ai_data["offer_discount_pct"] = round(disc_pct)
                    return True, score, optimal_price, offer_best_profit, charger_penalty, "OPORTUNIDAD_OFERTA"

    return (
        False, 5, optimal_price, best_net_profit, charger_penalty,
        f"No rentable ni ofertando -35% (Precio: {price:.0f}€ | Efectivo CeX: {cex_cash:.0f}€ | Reventa: {optimal_price:.0f}€)"
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


def register_deal_in_db(
    conn, deal_id, platform, item_id, target, title, description, price, landed_cost, fees,
    optimal_price, net_profit, ai_data, cex_data, item_url, checkout_url, image_url, kit_folder,
    seller_city="España", is_local=False, image_urls=None
):
    try:
        urls_list = image_urls if isinstance(image_urls, list) and image_urls else ([image_url] if image_url else [])
        deal_type = (ai_data or {}).get("deal_type", "direct_chollo")
        rec_offer = float((ai_data or {}).get("recommended_offer", 0) or 0)
        c = conn.cursor()
        c.execute(
            """INSERT OR REPLACE INTO deals (
                deal_id, platform, item_id, radar_id, radar_name, mode, title, description,
                price, landed_cost, fees, optimal_price, net_profit, deal_score,
                identified_model, cex_search_query, cex_box_name, cex_cash, cex_voucher, cex_sell,
                item_url, checkout_url, image_url, kit_folder, seller_city, is_local,
                fast_msg, haggle_msg, seo_title, seo_description, value_add_tip,
                image_urls_json, visual_notes, deal_type, recommended_offer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    except Exception as e:
        print(f"[WARN] Error guardando deal en BD ({deal_id}): {e}")


# ==================== ALERTAS DISCORD CON 2ª NOTIFICACIÓN PUSH DE VEREDICTO IA ====================

SCORE_BADGES = {
    10: "🚨 10/10 • COMPRA A CIEGAS (ARBITRAJE PURO CEX)",
    9: "🔥 9/10 • CHOLLAZO RESPALDADO EN EFECTIVO CEX",
    8: "✅ 8/10 • CHOLLO SEGURO (COSTE AMORTIZADO EN CEX)",
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

    flash_content = (
        f"⚡ **[FLASH SIN IA] {clean_t} — {price:.0f}€** {user_ping}\n"
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
            r = requests.post(
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
            r = requests.post(
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
            requests.delete(old_msg_url, headers=bot_headers, timeout=5)
            # 2. Publicamos un POST NUEVO para forzar la notificación push en el móvil
            dismiss_comp = [
                {
                    "type": 1,
                    "components": [
                        {"type": 2, "style": 4, "label": "Borrar aviso", "emoji": {"name": "🚫"}, "custom_id": f"deal:hide:rej_{msg_id}"}
                    ]
                }
            ]
            requests.post(
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
            requests.delete(old_msg_url, timeout=5)
            requests.post(
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
    price_drop_info=None, image_urls=None, flash_message=None
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

    register_deal_in_db(
        conn, deal_id, plat, item_id, target, title, description, price, landed_cost, fees,
        optimal_price, net_profit, ai_data, cex_data, clean_item_url, clean_checkout_url, image_url,
        kit_folder, seller_city=seller_city, is_local=is_local, image_urls=image_urls
    )

    score_badge = SCORE_BADGES.get(score, f"⭐ {score}/10 • CHOLLO VERIFICADO")
    identified_model = ai_data.get("identified_model", title)
    vis_conf = str(ai_data.get("visual_confidence", "alta")).upper()
    vis_notes = ai_data.get("visual_notes") or "Producto verificado visualmente en fotos."
    charger_warn = "\n⚠️ **OJO:** Sin cargador original (-12€ descontados del margen)" if charger_penalty > 0 else ""

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

    base_cost_for_cex = (offer_landed_cost if deal_type == "offer_opportunity" else landed_cost) + charger_penalty

    if cex_data and cex_data.get("cash", 0) > 0:
        net_cash = cex_data["cash"] - base_cost_for_cex
        net_voucher = cex_data["voucher"] - base_cost_for_cex
        sign_c = "+" if net_cash >= 0 else ""
        sign_v = "+" if net_voucher >= 0 else ""
        safety_tag = "🟢 **GANANCIA DIRECTA EN ACTO**" if net_cash >= 5 else "🛡️ **100% AMORTIZADO EN CEX**"
        cost_ref_label = f"con oferta de {recommended_offer}€" if deal_type == "offer_opportunity" else "al precio actual"
        cex_field_val = (
            f"🏷️ **Ficha CeX:** `{cex_data['box_name']}`\n"
            f"💵 **Efectivo CeX:** **{cex_data['cash']:.0f}€** (`{sign_c}{net_cash:.0f}€` neto {cost_ref_label}) | "
            f"🎟️ **Vale CeX:** **{cex_data['voucher']:.0f}€** (`{sign_v}{net_voucher:.0f}€`) *(PVP CeX: {cex_data['sell']:.0f}€)*\n"
            f"{safety_tag} — [Abrir Ficha Oficial CeX]({cex_link})"
        )
    else:
        cex_field_val = f"{target.get('cex_cash_ref', 'Sin referencia CeX')}\n[🔍 Buscar en CeX]({cex_link})"

    ai_text = (
        f"{drop_banner}"
        f"🏆 **Rating:** **`{score_badge}`**\n"
        f"🔎 **Modelo Verificado:** `{identified_model}` *(Confianza visual: **{vis_conf}**)*\n"
        f"👁️ **Inspección Fotos:** {vis_notes}\n"
        f"💡 **Tip:** {ai_data.get('value_add_tip', 'Oportunidad rápida')}{charger_warn}"
    )

    fast_msg = ai_data.get("fast_buy_message") or (
        "Hola! Me interesa, funciona todo bien? Si está disponible te lo compro ahora mismo por la aplicación."
    )

    # Si es un 10/10, el enlace principal lleva directo al Checkout 1-Tap; en caso contrario lleva directo al anuncio
    primary_action_url = clean_checkout_url if (score == 10 and deal_type != "offer_opportunity") else clean_item_url
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
                f"⚡ **CHECKOUT DIRECTO 1-TAP:** {clean_checkout_url}\n"
                f"🔗 **Ver Anuncio:** {clean_item_url} • **Modelo:** `{identified_model}` ({price:.0f}€)"
            )
        else:
            push_notification_content = (
                f"✅ **¡APROBADO POR IA ({score}/10)! SIGUE CON LA COMPRA (+{net_profit:.0f}€ LIMPIOS)** {user_ping}\n"
                f"👉 **ABRIR ANUNCIO:** {clean_item_url}\n"
                f"💳 **Checkout Rápido:** {clean_checkout_url} • **Modelo:** `{identified_model}` ({price:.0f}€)"
            )

    clean_embed_desc = re.sub(r"\s+", " ", html.unescape(description or "Sin descripción.")).strip()
    footer_text = f"Boti Sniper v4.3 (Visión Multimodal + Offer Sniper) • ID: {deal_id} • {datetime.now().strftime('%H:%M:%S')}".replace("\n", " ").strip()

    embed = {
        "title": header_title[:250],
        "url": primary_action_url,
        "color": color,
        "description": (f"{clean_embed_desc[:220]}..." if len(clean_embed_desc) > 220 else clean_embed_desc),
        "fields": [
            {"name": "🎯 Radar", "value": target["name"], "inline": True},
            {"name": cost_field_name, "value": cost_field_val, "inline": True},
            {"name": "🚚 Entrega / Origen", "value": f"{loc_badge}\n{ship_badge}", "inline": True},
            {"name": "🧠 Auditoría Visual & Técnica IA", "value": ai_text[:1020], "inline": False},
            {"name": "🏪 Suelo de Amortización CeX España", "value": cex_field_val[:1020], "inline": False},
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
                requests.delete(del_url, headers={"Authorization": f"Bot {BOT_TOKEN}"}, timeout=5)
            except Exception:
                pass
        elif f_via == "webhook" and WEBHOOK_URL and "http" in WEBHOOK_URL:
            base_wh = WEBHOOK_URL.split("?")[0].rstrip("/")
            del_url = f"{base_wh}/messages/{f_id}"
            try:
                requests.delete(del_url, timeout=5)
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
                r = requests.post(
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
                requests.post(WEBHOOK_URL, json={"content": push_notification_content, "embeds": [embed]}, timeout=10)
        except Exception:
            pass