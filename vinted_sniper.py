# -*- coding: utf-8 -*-
"""
Vinted Sniper Engine v4.2 (Boti Arbitrage & Homelab Suite).
- Embudo en 3 fases: Pre-Filtro Determinista -> Visión Multimodal Gemini 2.5 Flash (3 fotos) -> Suelo de Amortización CeX España.
- Sticky Sessions realistas (rotación cada 120 rondas o tras cooldown/0 resultados) para evasión DataDome.
- Pausa orgánica previa a descarga de ficha individual + desescapado HTML/RSC completo de descripciones.
- Detector de Rebajas de Precio (>=15% y >=10€) + Bloqueo Anti-Repost.
"""

import os
import copy
import json
import re
import html
import time
import random
import urllib.parse
from curl_cffi import requests

from antiban import BrowserProfileRotator, AntiBanTiming, AntiBanHealthMonitor, ParserHealthTracker, default_rate_limiter
from logger import setup_logger, log_metric
from core_engine import (
    WEBHOOK_URL, GEMINI_API_KEY, AI_MODEL, AI_ANALYST_MODEL, PROXY_URL,
    init_db, load_config, run_db_maintenance,
    check_item_seen_or_dropped, is_duplicate_recent_deal, mark_item_seen, count_platform_seen,
    passes_filters, fetch_cex_live, download_item_images,
    analyze_and_generate_kit, evaluate_deal_math_and_score,
    build_kit_folder_name, send_discord_alert,
    send_flash_discord_alert, resolve_flash_discord_alert, send_one_tap_ntfy_alert,
    touch_heartbeat, sleep_with_heartbeat,
    prescreen_item_text, AI_PRESCREEN_SHADOW_MODE,
    evaluate_fraud_risk, log_fraud_event, calculate_dhash, check_and_record_image_hash,
    enrich_seller_profile, evaluate_seller_risk,
    find_semantic_duplicate, save_item_embedding, is_ai_budget_paused,
    CONSOLE_RADAR_IDS, evaluate_bundle_cex_bonus, pre_gate_cex_offer_margin
)

SCAN_INTERVAL = int(os.getenv("VINTED_SCAN_INTERVAL_SECONDS", os.getenv("SCAN_INTERVAL_SECONDS", "45")))

rotator = BrowserProfileRotator(rotate_every_rounds=120)
timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)
health_monitor = AntiBanHealthMonitor("VINTED SNIPER")
parser_tracker = ParserHealthTracker("VINTED SNIPER", default_threshold=3)
logger = setup_logger("VINTED-SNIPER")
default_rate_limiter.register_platform("VINTED", SCAN_INTERVAL)


class VintedClient:
    def __init__(self, proxy_url="", rotator=rotator, health_monitor=health_monitor):
        self.proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        self.rotator = rotator
        self.health_monitor = health_monitor
        self.session = None
        self.session_epoch = -1
        self.consecutive_empty = 0
        self._init_session()

    def _init_session(self):
        profile = self.rotator.get_chrome_profile()
        self.session = requests.Session(impersonate=profile["impersonate"], proxies=self.proxies)
        headers = {
            "User-Agent": profile["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
        }
        if profile.get("sec_ch_ua"):
            headers["sec-ch-ua"] = profile["sec_ch_ua"]
            headers["sec-ch-ua-platform"] = profile["sec_ch_ua_platform"]
            headers["sec-ch-ua-mobile"] = profile["sec_ch_ua_mobile"]
        self.session.headers.update(headers)
        self.session_epoch = self.rotator.rotation_epoch
        self.consecutive_empty = 0
        try:
            self.session.get("https://www.vinted.es/", timeout=10)
        except Exception:
            pass

    def refresh_cookies(self):
        """Fuerza la rotación de perfil y regeneración de cookies de sesión."""
        self.rotator.rotate()
        self._init_session()

    def ensure_fresh_session(self):
        if self.session is None or self.session_epoch != self.rotator.rotation_epoch or self.consecutive_empty >= 4:
            self._init_session()

    def fetch_items(self, search_cfg, min_price=None, max_price=None):
        """
        Consulta Vinted mediante la API interna JSON /api/v2/catalog/items con fallback automático
        al catálogo RSC/HTML. Extrae id, título, precio, fotos HD y país del vendedor.
        Acepta un diccionario search_cfg o un texto de búsqueda directo.
        """
        if self.health_monitor.is_in_cooldown():
            return []

        self.ensure_fresh_session()

        if isinstance(search_cfg, dict):
            kw = search_cfg.get("keywords") or search_cfg.get("search_text") or search_cfg.get("name", "")
            if isinstance(kw, list):
                query_text = " ".join(str(k) for k in kw)
            else:
                query_text = str(kw)
            p_min = search_cfg.get("min_price", min_price if min_price is not None else 0)
            p_max = search_cfg.get("max_price", max_price if max_price is not None else 9999)
        else:
            query_text = str(search_cfg)
            p_min = min_price if min_price is not None else 0
            p_max = max_price if max_price is not None else 9999

        # Enrutar directamente al catálogo web HTML/RSC de Vinted (/catalog)
        return self._fetch_items_catalog_fallback(query_text, p_min, p_max)

    def search_items(self, query_text, min_price, max_price):
        """Compatibilidad hacia atrás con la firma posicional clásica."""
        return self.fetch_items({"keywords": query_text, "min_price": min_price, "max_price": max_price})

    def _fetch_items_catalog_fallback(self, query_text, min_price, max_price):
        params = {
            "search_text": query_text,
            "price_from": str(min_price),
            "price_to": str(max_price),
            "order": "newest_first"
        }
        url = f"https://www.vinted.es/catalog?{urllib.parse.urlencode(params)}"
        t0 = time.time()
        try:
            r = self.session.get(url, timeout=15)
            default_rate_limiter.record_request("VINTED", time.time() - t0, r.status_code)

            if r.status_code in (401, 403, 429):
                should_cd, cd_secs = self.health_monitor.handle_http_response(r.status_code, url=url, query=query_text)
                if should_cd:
                    print(f"[VINTED COOLDOWN] HTTP {r.status_code}. Registrado cooldown de {cd_secs // 60} min (estado no bloqueante)...")
                    self.rotator.rotate()
                    self._init_session()
                    return []
                else:
                    self._init_session()
                    r = self.session.get(url, timeout=15)

            if r.status_code == 200:
                self.health_monitor.handle_http_response(200, query=query_text)
            else:
                print(f"[WARN] Vinted HTTP {r.status_code} buscando '{query_text}'")
                parser_tracker.record_empty("VINTED", query_text, self.health_monitor, reason=f"HTTP {r.status_code}")
                return []

            items_by_id = {}

            # 1. Extractor optimizado de array JSON en payload RSC ("items":[{"id":... "productItem":{...}}])
            marker = r'\"items\":['
            idx = r.text.find(marker)
            if idx != -1:
                try:
                    start_bracket = idx + len(marker) - 1
                    count = 0
                    end_bracket = start_bracket
                    for i in range(start_bracket, len(r.text)):
                        if r.text[i] == '[': count += 1
                        elif r.text[i] == ']':
                            count -= 1
                            if count == 0:
                                end_bracket = i + 1
                                break
                    raw_slice = r.text[start_bracket:end_bracket]
                    cleaned_json = raw_slice.replace(r'\"', '"').replace(r'\\', '\\')
                    raw_items_arr = json.loads(cleaned_json)
                    for raw_entry in raw_items_arr:
                        pi = raw_entry.get("productItem", raw_entry) if isinstance(raw_entry, dict) else {}
                        item_id = str(pi.get("id") or "")
                        if not item_id:
                            continue
                        title = pi.get("title") or ""
                        raw_p = pi.get("price") or {}
                        price = float(raw_p.get("amount", 0) if isinstance(raw_p, dict) else (raw_p or 0))
                        raw_tot = pi.get("totalItemPrice") or {}
                        total_with_fee = float(raw_tot.get("amount", 0) if isinstance(raw_tot, dict) else (raw_tot or round(price * 1.05 + 0.70, 2)))
                        raw_url = pi.get("url") or ""
                        full_url = raw_url if raw_url.startswith("http") else f"https://www.vinted.es{raw_url}"
                        full_url = full_url.split("?")[0]

                        # Fotos HD desde array photos
                        photos = []
                        for ph in pi.get("photos", []):
                            if isinstance(ph, dict) and ph.get("url"):
                                photos.append(ph["url"])
                        if not photos and pi.get("thumbnailUrl"):
                            photos.append(pi["thumbnailUrl"])

                        user_data = pi.get("user") or {}
                        user_id = str(user_data.get("id") or "")
                        login = str(user_data.get("login") or user_data.get("username") or "")
                        country_code = str(user_data.get("country_code") or user_data.get("country_iso_code") or "").upper()

                        item_box = pi.get("itemBox") or {}
                        brand = item_box.get("firstLine", "") or ""
                        status_cond = item_box.get("secondLine", "") or "Muy bueno"

                        items_by_id[item_id] = {
                            "id": item_id,
                            "title": title,
                            "brand_title": brand,
                            "status": status_cond,
                            "price": price,
                            "total_item_price": total_with_fee,
                            "url": full_url,
                            "checkout_url": full_url,
                            "photo": photos[0] if photos else "",
                            "photos": photos,
                            "raw_title": title,
                            "country_code": country_code,
                            "seller": {
                                "name": login,
                                "id": user_id or login,
                                "country_code": country_code,
                                "age_days": None,
                                "listings_count": None,
                                "rating": None,
                                "sales": None
                            }
                        }
                except Exception:
                    pass

            # 2. Fallback: Parser HTML de tarjetas de catálogo
            if not items_by_id:
                pattern = re.compile(
                    r'data-testid="product-item-id-(?P<id>\d+)"[^>]*>'
                    r'.*?<img[^>]+src="(?P<img>https://images\d*\.vinted\.net/[^"]+)"[^>]*data-testid="product-item-id-(?P=id)--image--img"'
                    r'.*?<a\s+href="(?P<link>(?:https://www\.vinted\.es)?/items/(?P=id)-[^"]*)"[^>]*title="(?P<title_attr>[^"]*)"',
                    re.DOTALL
                )
                for m in pattern.finditer(r.text):
                    item_id = m.group("id")
                    img_url = m.group("img")
                    link = m.group("link")
                    title_attr = html.unescape(m.group("title_attr"))

                    prices = re.findall(r"(\d+[\.,]\d{2})\s*€", title_attr)
                    price = float(prices[0].replace(",", ".")) if prices else 0.0
                    total_with_fee = float(prices[1].replace(",", ".")) if len(prices) > 1 else (price * 1.05 + 0.70)

                    title = title_attr
                    brand = ""
                    status_cond = ""
                    if ", Marca:" in title_attr:
                        parts = title_attr.split(", Marca:")
                        title = parts[0].strip()
                        rest = parts[1]
                        if ", Estado:" in rest:
                            brand_p, state_p = rest.split(", Estado:", 1)
                            brand = brand_p.strip()
                            status_cond = state_p.split(",")[0].strip()
                        else:
                            brand = rest.split(",")[0].strip()
                    elif "," in title_attr:
                        title = title_attr.split(",")[0].strip()

                    full_url = link if link.startswith("http") else f"https://www.vinted.es{link}"
                    full_url = full_url.split("?")[0]
                    checkout_url = full_url

                    items_by_id[item_id] = {
                        "id": item_id,
                        "title": title,
                        "brand_title": brand,
                        "status": status_cond,
                        "price": price,
                        "total_item_price": total_with_fee,
                        "url": full_url,
                        "checkout_url": checkout_url,
                        "photo": img_url,
                        "photos": [img_url] if img_url else [],
                        "raw_title": title_attr,
                        "country_code": "",
                        "seller": {
                            "name": "",
                            "id": "",
                            "country_code": "",
                            "age_days": None,
                            "listings_count": None,
                            "rating": None,
                            "sales": None
                        }
                    }

            # 3. Fallback adicional individual por regex RSC
            if not items_by_id:
                rsc_pattern = re.compile(
                    r'\\"productItem\\":\{\\"id\\":(?P<id>\d+),\\"title\\":\\"(?P<title>[^\\"]+)\\",'
                    r'\\"url\\":\\"(?P<url>/items/\d+-[^\\"]*)\\".*?'
                    r'\\"price\\":\{\\"amount\\":\\"(?P<price>[\d\.]+)\\".*?'
                    r'\\"thumbnailUrl\\":\\"(?P<img>https://images\d*\.vinted\.net/[^\\"]+)\\"',
                    re.DOTALL
                )
                for m in rsc_pattern.finditer(r.text):
                    item_id = m.group("id")
                    price = float(m.group("price") or 0)
                    link = m.group("url")
                    full_url = f"https://www.vinted.es{link}"
                    img_u = m.group("img")
                    items_by_id[item_id] = {
                        "id": item_id,
                        "title": html.unescape(m.group("title")),
                        "brand_title": "",
                        "status": "Muy bueno",
                        "price": price,
                        "total_item_price": round(price * 1.05 + 0.70, 2),
                        "url": full_url,
                        "checkout_url": full_url,
                        "photo": img_u,
                        "photos": [img_u] if img_u else [],
                        "raw_title": html.unescape(m.group("title")),
                        "country_code": "",
                        "seller": {
                            "name": "",
                            "id": "",
                            "country_code": "",
                            "age_days": None,
                            "listings_count": None,
                            "rating": None,
                            "sales": None
                        }
                    }

            items = list(items_by_id.values())
            if not items:
                self.consecutive_empty += 1
            else:
                self.consecutive_empty = 0
                parser_tracker.record_success("VINTED", query_text, len(items), self.health_monitor)
            return items
        except Exception as e:
            print(f"[ERROR] Error consultando Vinted '{query_text}': {e}")
            self.consecutive_empty += 1
            parser_tracker.record_parser_error("VINTED", query_text, str(e), self.health_monitor)
            return []

    def fetch_item_details(self, item_url):
        t0 = time.time()
        try:
            r = self.session.get(item_url, timeout=12)
            default_rate_limiter.record_request("VINTED", time.time() - t0, r.status_code)
            if r.status_code in (403, 429):
                self.health_monitor.handle_http_response(r.status_code, url=item_url)
                return "", [], "", None
            if r.status_code != 200:
                return "", [], "", None

            m_desc = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', r.text)
            desc = html.unescape(m_desc.group(1)).strip() if m_desc else ""

            # Intentar extraer descripción completa desde el payload RSC de la ficha si existe
            m_rsc_desc = re.search(r'\\"description\\":\\"((?:\\\\.|[^"\\]){10,2000})\\"', r.text)
            if m_rsc_desc:
                try:
                    rsc_desc = html.unescape(m_rsc_desc.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")).strip()
                    if len(rsc_desc) > len(desc):
                        desc = rsc_desc
                except Exception:
                    pass

            photos = re.findall(r'href="(https://images\d*\.vinted\.net/t/[^"]+/f800/[^"]+)"', r.text)
            if not photos:
                photos = re.findall(r'"(https://images\d*\.vinted\.net/t/[^"]+/f800/[^"]+)"', r.text)
            clean_photos = [html.unescape(p) for p in list(dict.fromkeys(photos))[:4]]

            m_date = re.search(r'itemProp="upload_date"[^>]*>.*?<span[^>]*>([^<]+)</span>', r.text, re.DOTALL)
            upload_date = html.unescape(m_date.group(1)).strip() if m_date else ""

            # Extraer perfil del vendedor en Vinted (P2-4)
            seller_info = {
                "name": "",
                "username": "",
                "id": "",
                "age_days": None,
                "listings_count": None,
                "rating": None,
                "sales": None
            }
            m_uname = re.search(r'data-testid="profile-username"[^>]*>([^<]+)<', r.text)
            if m_uname:
                seller_info["name"] = m_uname.group(1).strip()
                seller_info["username"] = seller_info["name"]

            m_uid = re.search(r'"user":\s*\{\s*"id":\s*(\d+)', r.text) or re.search(r'/member/(\d+)', r.text) or re.search(r'"user_id":\s*(\d+)', r.text)
            if m_uid:
                seller_info["id"] = m_uid.group(1).strip()

            # Extracción de reputación del vendedor en Vinted (JSON SSR y regex multilingüe)
            m_fb = re.search(r'"feedback_count":\s*(\d+)', r.text)
            if m_fb:
                try:
                    seller_info["sales"] = int(m_fb.group(1))
                except Exception:
                    pass

            m_rep = re.search(r'"feedback_reputation":\s*([\d\.]+)', r.text)
            if m_rep:
                try:
                    rep_val = float(m_rep.group(1))
                    if rep_val > 0:
                        seller_info["rating"] = round(rep_val * 5.0, 1)
                    elif seller_info["sales"] == 0:
                        seller_info["rating"] = 0.0
                except Exception:
                    pass

            # Fallback 1: Rating textual en aria-label (ES, FR, IT, EN)
            if seller_info["rating"] is None:
                m_rating = re.search(
                    r'(?:valoraci[oó]n\s+total\s+de|note\s+globale\s+de|valutazione\s+(?:complessiva\s+)?di|overall\s+rating\s+of)\s+([\d\.]+)',
                    r.text, re.IGNORECASE
                )
                if m_rating:
                    try:
                        seller_info["rating"] = float(m_rating.group(1))
                    except Exception:
                        pass

            # Fallback 2: Ventas/reseñas textuales en aria-label (ES, FR, IT, EN)
            if seller_info["sales"] is None:
                m_revs = re.search(
                    r'aria-label="[^"]*(?:valoraci[oó]n\s+total|note\s+globale|valutazione|overall\s+rating)[^"]*"[^>]*>.*?([1-9]\d*)',
                    r.text, re.DOTALL | re.IGNORECASE
                )
                if m_revs:
                    try:
                        seller_info["sales"] = int(m_revs.group(1))
                    except Exception:
                        pass
                elif re.search(
                    r'data-testid="profile-ratings"[^>]*>.*?(?:Sin valoraciones|Pas encore d\'évaluations|0\s+évaluation|Nessuna recensione|0\s+recensioni|No reviews|0\s+reviews)',
                    r.text, re.IGNORECASE
                ):
                    seller_info["sales"] = 0

            # Fallback 3: Detección o extracción de país del vendedor
            if not seller_info.get("country_code"):
                m_loc = re.search(r'data-testid="profile-location"[^>]*>([^<]+)<', r.text)
                if m_loc:
                    loc_str = m_loc.group(1).strip().lower()
                    if any(c in loc_str for c in ("francia", "france", "paris", "lyon", "marseille")):
                        seller_info["country_code"] = "FR"
                    elif any(c in loc_str for c in ("italia", "italy", "roma", "milano", "napoli")):
                        seller_info["country_code"] = "IT"
                    elif any(c in loc_str for c in ("portugal", "lisboa", "porto")):
                        seller_info["country_code"] = "PT"
                    elif any(c in loc_str for c in ("bélgica", "belgique", "belgium", "bruxelles", "brussels")):
                        seller_info["country_code"] = "BE"
                    elif any(c in loc_str for c in ("países bajos", "netherlands", "holanda", "amsterdam")):
                        seller_info["country_code"] = "NL"
                    elif any(c in loc_str for c in ("españa", "spain", "madrid", "barcelona", "sevilla", "valencia")):
                        seller_info["country_code"] = "ES"

            if not seller_info.get("country_code"):
                desc_l = f"{desc} {r.text[:5000]}".lower()
                if any(w in desc_l for w in ("bonjour", "vend", "vends", "état", "envoi", "boîte")):
                    seller_info["country_code"] = "FR"
                elif any(w in desc_l for w in ("vendo", "ottimo", "spedizione", "funzionante", "scatola")):
                    seller_info["country_code"] = "IT"
                elif any(w in desc_l for w in ("olá", "ótimo", "comandos")):
                    seller_info["country_code"] = "PT"
                elif any(w in desc_l for w in ("hallo", "goede staat", "verzending")):
                    seller_info["country_code"] = "NL"

            return desc, clean_photos, upload_date, seller_info
        except Exception:
            return "", [], "", None


VintedScraper = VintedClient


def is_vinted_item_fresh(upload_date):
    """
    Filtra cuentas inactivas / abandonadas en Vinted.
    Un chollo real en Vinted se vende en menos de 15-30 minutos.
    Cualquier anuncio con semanas, meses o años es un vendedor inactivo que no enviará el producto.
    """
    if not upload_date:
        return True
    ud = upload_date.lower().strip()
    stale_tokens = [
        "año", "an ", "ans", "anno", "anni", "year",
        "mes", "mois", "mese", "mesi", "month",
        "semana", "semaine", "settimana", "week"
    ]
    if any(st in ud for st in stale_tokens):
        return False
    day_match = re.search(r'(\d+)\s*(?:días|dias|jours|giorni|days)', ud)
    if day_match:
        try:
            return int(day_match.group(1)) <= 2
        except Exception:
            return False
    return True


def get_vinted_queries_for_target(target):
    if "vinted_keywords" in target:
        vk = target["vinted_keywords"]
        return vk if isinstance(vk, list) else [vk]
    return [target.get("keywords", "").strip()]


def parse_vinted_price(item):
    base_price = float(item.get("price", 0.0))
    buyer_fee = round((base_price * 0.05) + 0.70, 2)
    shipping_est = 3.29
    landed_cost = round(base_price + buyer_fee + shipping_est, 2)
    return base_price, landed_cost, round(buyer_fee + shipping_est, 2)


def send_startup_ping(total_targets, min_profit, min_score):
    if not WEBHOOK_URL or "http" not in WEBHOOK_URL:
        return
    engine = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal 3-Fotos)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    msg = {
        "content": (
            f"💎 **Boti Arbitrage Sniper v4.2 (VINTED ENGINE • MULTIMODAL + RESPALDO CEX) ONLINE**\n"
            f"🧠 Motor IA: **`{engine}`** | Radares activos: **{total_targets}**\n"
            f"🔒 **Regla de Oro 99%:** Inspección visual de hasta 3 fotos reales + Suelo de Amortización CeX España + Rating Persistente."
        )
    }
    try:
        requests.post(WEBHOOK_URL, json=msg, timeout=10)
    except Exception:
        pass


def main():
    conn = init_db()
    vinted = VintedClient(proxy_url=PROXY_URL, rotator=rotator, health_monitor=health_monitor)
    config = load_config()

    targets = [t for t in config.get("targets", []) if t.get("enabled", True) and "vinted" in t.get("platforms", ["vinted"])]
    min_profit = float(config.get("min_net_profit_eur", 35))
    min_score = int(config.get("min_deal_score", 8))

    seen_cnt = count_platform_seen(conn, "VINTED")
    warmup = seen_cnt == 0
    item_eval_fails = {}
    budget_deferred = set()  # Anuncios aplazados por pausa de presupuesto IA (A-001)
    round_num = 0

    engine_name = f"Google Gemini ({AI_ANALYST_MODEL} Multimodal)" if GEMINI_API_KEY else f"OpenRouter ({AI_MODEL})"
    print(f"[STARTUP] Vinted Sniper v4.2 activo | {len(targets)} radares | {seen_cnt} en BD | IA: {engine_name}")
    send_startup_ping(len(targets), min_profit, min_score)
    last_maintenance = time.time()
    touch_heartbeat()

    while True:
        touch_heartbeat()
        if time.time() - last_maintenance > 21600:
            run_db_maintenance(conn)
            item_eval_fails.clear()
            last_maintenance = time.time()
        try:
            config = load_config()
        except Exception as e:
            print(f"[WARN] Error transitorio leyendo config.json ({e}). Reteniendo configuración en memoria.")
        all_targets = config.get("targets", [])
        targets = [t for t in all_targets if t.get("enabled", True) and "vinted" in t.get("platforms", ["vinted"])]
        global_blacklist = config.get("global_blacklist", [])
        min_profit = float(config.get("min_net_profit_eur", 35))
        min_personal_savings = float(config.get("min_personal_savings_eur", 25))
        min_score = int(config.get("min_deal_score", 8))
        drop_pct = float(config.get("price_drop_threshold_pct", 15))
        drop_eur = float(config.get("price_drop_min_eur", 10))

        round_start = time.time()
        round_new = 0
        round_evaluated = 0
        round_total_fetched = 0

        # Cooldown no bloqueante (P3-3): Si Vinted está en pausa, saltar escaneo manteniendo vivo el contenedor
        if vinted.health_monitor.is_in_cooldown():
            rem = vinted.health_monitor.get_remaining_cooldown()
            print(f"[VINTED EN COOLDOWN] Restan {rem // 60}m {rem % 60}s. Saltando ronda sin bloquear proceso...")
            sleep_with_heartbeat(min(SCAN_INTERVAL, max(5, rem)))
            round_num += 1
            continue

        for target in targets:
            touch_heartbeat()
            if not timing.should_scan_target(target, round_num, warmup=warmup):
                continue

            queries = get_vinted_queries_for_target(target)
            target_items_count = 0
            target_new_count = 0
            mode = target.get("mode", "resale")

            raw_items = []
            seen_target_ids = set()
            for q in queries:
                items = vinted.search_items(q, target["min_price"], target["max_price"])
                target_items_count += len(items)
                round_total_fetched += len(items)
                for itm in items:
                    iid = itm.get("id")
                    if iid and iid not in seen_target_ids:
                        seen_target_ids.add(iid)
                        raw_items.append(itm)
                timing.inter_query_sleep(1.0, 2.0)

            tgt_max = max(1.0, float(target.get("max_price", 9999)))
            direct_max = float(target.get("direct_buy_max_price") or tgt_max)

            def _get_vinted_price(it):
                p = it.get("price", 0)
                if isinstance(p, dict):
                    return float(p.get("amount", 0) or 0)
                try:
                    return float(p or 0)
                except (ValueError, TypeError):
                    return 0.0

            fast_lane_key = lambda it: (0 if _get_vinted_price(it) <= direct_max else 1, _get_vinted_price(it) / tgt_max)
            raw_items.sort(key=fast_lane_key)

            for item in raw_items:
                    item_id = item.get("id")
                    if not item_id:
                        continue

                    title = item.get("title", "")
                    brand = item.get("brand_title", "")
                    status_cond = item.get("status", "")
                    base_price, landed_cost, fees = parse_vinted_price(item)

                    already_seen, price_drop_info = check_item_seen_or_dropped(
                        conn, "VINTED", item_id, base_price, min_drop_pct=drop_pct, min_drop_eur=drop_eur
                    )
                    if already_seen:
                        continue

                    # Aplazado por presupuesto: esperar a que se reanude la IA sin repetir la alerta flash
                    was_budget_deferred = item_id in budget_deferred
                    if was_budget_deferred:
                        if is_ai_budget_paused(conn):
                            continue
                        budget_deferred.discard(item_id)

                    target_new_count += 1
                    round_new += 1

                    if warmup:
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    extra_meta_str = f"{brand} {status_cond}"
                    # FASE 1A: Pre-Filtro sobre título/marca antes de abrir la ficha
                    if not passes_filters(title, "", base_price, target, global_blacklist, extra_text=extra_meta_str):
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    if is_duplicate_recent_deal(conn, "VINTED", title, base_price):
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    time.sleep(random.uniform(1.2, 2.3))
                    desc, extra_photos, upload_date, seller_info = vinted.fetch_item_details(item["url"])
                    if seller_info:
                        if not seller_info.get("country_code") and item.get("country_code"):
                            seller_info["country_code"] = item["country_code"]
                        item["seller"] = seller_info
                        if seller_info.get("country_code") and not item.get("country_code"):
                            item["country_code"] = seller_info["country_code"]

                    if not was_budget_deferred and not is_vinted_item_fresh(upload_date):
                        print(f"[VINTED DESCARTADO - ANUNCIO ANTIGUO ({upload_date})] {title} ({base_price:.0f}€)")
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    # FASE 1B: Pre-Filtro sobre descripción completa desescapada
                    if not passes_filters(title, desc, base_price, target, global_blacklist, extra_text=extra_meta_str):
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    item["platform"] = "VINTED"
                    enrich_seller_profile(item, http_session=vinted.session if 'vinted' in locals() else None)

                    # EVALUACIÓN DE FRAUDE DETERMINISTA EN CAPAS (P2)
                    fraud_eval = evaluate_fraud_risk(
                        title, desc, base_price, radar_id=target.get("id", ""),
                        conn=conn, platform="VINTED", item_id=item_id,
                        seller=item.get("seller")
                    )

                    if fraud_eval.get("is_fraud") or fraud_eval.get("should_block") or fraud_eval.get("risk", 0) >= 4:
                        log_fraud_event(conn, "VINTED", item_id, title, base_price, fraud_eval["risk"], fraud_eval["hits"])
                        print(f"[VINTED FRAUDE BLOQUEADO] {title} ({base_price:.0f}€) -> {[h['label'] for h in fraud_eval['hits']]}")
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    # ALERTA FLASH + 1-TAP (Vendedor fiable reviews >= 2 y rating >= 4.0)
                    s_rev = int(item.get("seller_reviews_count", 0) or 0)
                    s_rat = item.get("seller_rating")
                    can_flash = (s_rev >= 2 and (s_rat is not None and s_rat >= 4.0))

                    flash_msg = None
                    if fraud_eval["risk"] < 2 and can_flash and not was_budget_deferred and base_price <= direct_max:
                        print(f"[⚡ FLASH VINTED <80ms] {title} ({base_price:.0f}€) -> Pre-validado por Puerta 0 y Antifraude. Enviando alerta instantánea...")
                        flash_msg = send_flash_discord_alert(
                            platform="VINTED",
                            item_id=item_id,
                            title=title,
                            price=base_price,
                            landed_cost=landed_cost,
                            item_url=item.get("url", ""),
                            image_url=item.get("photo", ""),
                            seller_city=f"Vinted ({brand or item.get('country_code', '')})",
                            target=target
                        )
                        send_one_tap_ntfy_alert(item, {"cex_cash": 0, "p2p_net_profit": 0}, is_flash=True)

                    # Pre-gating matemático CeX antes de IA (TASK-03)
                    ok_pregate, pregate_reason = pre_gate_cex_offer_margin(conn, item, target, config)
                    if not ok_pregate:
                        print(f"[VINTED PRE-GATE CEX DESCARTADO] {title} ({base_price:.0f}€) -> {pregate_reason}")
                        if flash_msg:
                            resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Pre-gate CeX: {pregate_reason}", title=title, price=base_price, item_url=item.get("url", ""))
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    # PUERTA 1: Criba ultra-ligera por texto (0 fotos, 0 thinking tokens)
                    ps_decision, ps_reason, ps_conf, from_cache = prescreen_item_text(
                        "VINTED", title, desc, base_price, landed_cost, target, conn=conn, health_monitor=health_monitor
                    )

                    if ps_decision == "reject":
                        if AI_PRESCREEN_SHADOW_MODE:
                            print(f"[VINTED SHADOW PRESCREEN RECHAZARÍA] {title} ({base_price:.0f}€) -> {ps_reason} | Pasando a visión por shadow mode...")
                        else:
                            print(f"[VINTED DESCARTADO TEXTO PUERTA 1] {title} ({base_price:.0f}€) -> {ps_reason}")
                            if flash_msg:
                                resolve_flash_discord_alert(
                                    flash_msg, approved=False, reason=f"Descartado texto Puerta 1: {ps_reason}",
                                    title=title, price=base_price, item_url=item.get("url", "")
                                )
                            mark_item_seen(conn, "VINTED", item_id, title, base_price)
                            continue

                    # Si hay riesgo moderado (2-3) y Puerta 1 no está segura ('unsure'), descartar y registrar evento
                    if fraud_eval["risk"] in (2, 3) and ps_decision == "unsure":
                        log_fraud_event(conn, "VINTED", item_id, title, base_price, fraud_eval["risk"], fraud_eval["hits"])
                        print(f"[VINTED RIESGO MODERADO DESCARTADO] {title} ({base_price:.0f}€) -> {fraud_eval['warning_banner']}")
                        if flash_msg:
                            resolve_flash_discord_alert(
                                flash_msg, approved=False, reason=f"Riesgo moderado antifraude",
                                title=title, price=base_price, item_url=item.get("url", "")
                            )
                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    # FASE 5: Comprobación de duplicado semántico (P1-6)
                    seller = item.get("seller")
                    current_seller_id = ""
                    if isinstance(seller, dict):
                        current_seller_id = str(seller.get("id") or seller.get("user_id") or "").strip()
                    elif seller:
                        current_seller_id = str(seller).strip()

                    text_for_embed = f"{title}\n{desc}".strip()
                    sem_dup, item_vec = find_semantic_duplicate(
                        conn, text_for_embed, radar_id=target.get("id", ""), threshold=0.92, hours=720, price=base_price,
                        exclude_item_key=f"VINTED:{item_id}"
                    )

                    ai_data = None
                    if sem_dup and sem_dup.get("verdict_json"):
                        try:
                            cached_verdict = json.loads(sem_dup["verdict_json"])
                            if isinstance(cached_verdict, dict):
                                sim_score = float(sem_dup.get("similarity", sem_dup.get("score", 0.0)))
                                cached_seller_id = str(sem_dup.get("seller_id", "")).strip()
                                cached_price = float(sem_dup.get("price", 999999.0))
                                is_cached_reject = (not cached_verdict.get("is_valid_deal", False)) or cached_verdict.get("has_defect_or_missing_parts", False)

                                if is_cached_reject:
                                    if sim_score >= 0.96:
                                        rej = cached_verdict.get("reject_reason") or "Descartado previamente por IA"
                                        print(f"[VINTED DEDUP SEMÁNTICA RECHAZO] '{title}' duplicado de '{sem_dup['title']}' (sim={sim_score:.2f} >= 0.96) -> {rej}")
                                        if flash_msg:
                                            resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Duplicado semántico rechazado: {rej}", title=title, price=base_price, item_url=item.get("url", ""))
                                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                                        continue
                                    else:
                                        print(f"[VINTED DEDUP SEMÁNTICA] Match rechazado previo pero sim={sim_score:.2f} < 0.96 -> Inspección visual obligatoria")
                                        ai_data = None
                                else:
                                    if current_seller_id and current_seller_id == cached_seller_id and base_price <= cached_price:
                                        print(f"[VINTED DEDUP SEMÁNTICA REUTILIZADO] '{title}' duplicado aprobado de '{sem_dup['title']}' (mismo vendedor {current_seller_id}, {base_price:.0f}€ <= {cached_price:.0f}€, sim={sim_score:.2f}) -> Reutilizando kit sin visión")
                                        ai_data = copy.deepcopy(cached_verdict)
                                    else:
                                        print(f"[VINTED DEDUP SEMÁNTICA] Match aprobado pero vendedor ({current_seller_id} vs {cached_seller_id}) o precio ({base_price:.0f}€ vs {cached_price:.0f}€) no apto para bypass -> Forzando visión")
                                        ai_data = None
                        except Exception as e:
                            print(f"[WARN] Error parseando veredicto de dedup: {e}")

                    if ai_data is None:
                        # PUERTA 2: Visión adaptativa (1 foto primero, hasta 3 si la confianza visual es baja)
                        photo_urls = extra_photos if extra_photos else (item.get("photos") or ([item["photo"]] if item.get("photo") else []))
                        cover_url = photo_urls[0] if photo_urls else ""
                        raw_photos = download_item_images(photo_urls, rotator=rotator, max_images=1)

                        # Comprobación de dHash para fotos reutilizadas (P2-5)
                        if raw_photos and len(raw_photos) > 0 and raw_photos[0]:
                            img_h = calculate_dhash(raw_photos[0])
                            if img_h:
                                is_reused, r_info = check_and_record_image_hash(conn, img_h, "VINTED", item_id, target.get("id", ""))
                                if is_reused and r_info:
                                    re_banner = f"⚠️ **FOTO REUTILIZADA:** Ya vista en {r_info['platform']} (anuncio {r_info['item_id']})"
                                    existing_w = fraud_eval.get("warning_banner", "")
                                    fraud_eval["warning_banner"] = f"{re_banner}\n{existing_w}".strip()
                                    fraud_eval["risk"] = min(5, fraud_eval.get("risk", 0) + 3)
                                    fraud_eval.setdefault("hits", []).append({
                                        "code": "REUSED_IMAGE",
                                        "category": "image",
                                        "label": f"Foto reutilizada de {r_info['platform']} ({r_info['item_id']})",
                                        "weight": 3
                                    })
                                    if fraud_eval["risk"] >= 4:
                                        fraud_eval["is_fraud"] = True
                                        log_fraud_event(conn, "VINTED", item_id, title, base_price, fraud_eval["risk"], fraud_eval["hits"])
                                        print(f"[VINTED FRAUDE POR FOTO REUTILIZADA] Bloqueado anuncio {item_id} (riesgo={fraud_eval['risk']})")
                                        if flash_msg:
                                            resolve_flash_discord_alert(flash_msg, approved=False, reason=f"Foto reutilizada de {r_info['platform']} ({r_info['item_id']})", title=title, price=base_price, item_url=item.get("url", ""))
                                        mark_item_seen(conn, "VINTED", item_id, title, base_price)
                                        continue

                        round_evaluated += 1
                        drop_tag = f" [REBAJA -{price_drop_info['drop_eur']:.0f}€]" if price_drop_info else ""
                        print(f"[EVALUANDO VINTED IA + {len(raw_photos)} FOTOS]{drop_tag} {title} ({base_price:.0f}€ | Casa: {landed_cost:.0f}€)...")
                        ai_data = analyze_and_generate_kit(
                            "VINTED", title, desc, base_price, landed_cost, False, target=target,
                            conn=conn, health_monitor=health_monitor,
                            extra_meta=f"(Marca: {brand} | Estado: {status_cond})",
                            raw_images=raw_photos, call_type="vision"
                        )

                        # Reintento con 3 fotos si la IA aprueba pero duda visualmente y hay más fotos disponibles
                        if ai_data and ai_data.get("is_valid_deal", False) and ai_data.get("visual_confidence") == "baja" and len(photo_urls) > 1:
                            print(f"[VINTED REINTENTO VISUAL] Confianza baja con 1 foto para '{title}'. Descargando hasta 3 fotos...")
                            more_photos = download_item_images(photo_urls, rotator=rotator, max_images=3)
                            if len(more_photos) > 1:
                                ai_data_retry = analyze_and_generate_kit(
                                    "VINTED", title, desc, base_price, landed_cost, False, target=target,
                                    conn=conn, health_monitor=health_monitor,
                                    extra_meta=f"(Marca: {brand} | Estado: {status_cond})",
                                    raw_images=more_photos, call_type="vision_retry"
                                )
                                if ai_data_retry:
                                    ai_data = ai_data_retry

                        if ai_data and not ai_data.get("_api_error"):
                            save_item_embedding(
                                conn, f"VINTED:{item_id}", target.get("id", ""),
                                vector_or_text=item_vec or text_for_embed,
                                model="models/gemini-embedding-2",
                                verdict_json=json.dumps(ai_data, ensure_ascii=False),
                                title=title, price=base_price,
                                seller_id=current_seller_id,
                                precomputed_vec=item_vec
                            )

                    if ai_data and ai_data.get("_budget_paused"):
                        budget_deferred.add(item_id)
                        print(f"[VINTED BUDGET] '{title}' ({base_price:.0f}€) aplazado hasta que se reanude el presupuesto IA.")
                        continue

                    if not ai_data or ai_data.get("_api_error"):
                        fail_cnt = item_eval_fails.get(item_id, 0) + 1
                        item_eval_fails[item_id] = fail_cnt
                        if fail_cnt >= 2:
                            mark_item_seen(conn, "VINTED", item_id, title, base_price)
                        continue

                    mark_item_seen(conn, "VINTED", item_id, title, base_price)

                    if not ai_data.get("is_valid_deal", False) or ai_data.get("is_pro_seller", False) or ai_data.get("has_defect_or_missing_parts", False):
                        reason = ai_data.get("reject_reason") or "Descartado en inspección visual IA"
                        print(f"[VINTED DESCARTADO IA] {title} ({base_price:.0f}€) -> {reason}")
                        if flash_msg:
                            resolve_flash_discord_alert(flash_msg, approved=False, reason=reason, title=title, price=base_price, item_url=item.get("url", ""))
                        continue

                    # FASE 3: Verificación CeX España en vivo + Regla de Amortización + Rating Determinista
                    cex_data = None
                    if mode == "resale":
                        cex_query = ai_data.get("cex_search_query") or ai_data.get("identified_model") or q
                        cex_data = fetch_cex_live(
                            conn, cex_query, reference_price=base_price, target=target,
                            title_context=title, desc_context=desc,
                            includes_box=bool(ai_data.get("includes_original_box", False)),
                            includes_lens=bool(ai_data.get("includes_kit_lens", True))
                        )

                        # BUNDLE SUM ENGINE (Task 4.2): Valoración acumulativa de juegos/mandos extra en CeX
                        bundled_q = ai_data.get("bundled_games_queries", [])
                        radar_id = (target or {}).get("id", "")
                        is_console = (radar_id in CONSOLE_RADAR_IDS) or any(c_id in radar_id for c_id in CONSOLE_RADAR_IDS)
                        if is_console and bundled_q and cex_data and isinstance(cex_data, dict):
                            bundle_bonus = evaluate_bundle_cex_bonus(conn, bundled_q)
                            extra_cash = bundle_bonus.get("extra_cash", 0.0)
                            extra_voucher = bundle_bonus.get("extra_voucher", 0.0)
                            matched_extras = bundle_bonus.get("matched_extras", [])
                            if matched_extras:
                                cex_data["cash"] = round(float(cex_data.get("cash", 0.0) or 0.0) + extra_cash, 2)
                                cex_data["voucher"] = round(float(cex_data.get("voucher", 0.0) or 0.0) + extra_voucher, 2)
                                cex_data["box_name"] = f"{cex_data.get('box_name', cex_query)} + {len(matched_extras)} extra(s)"
                                ai_data["bundled_matches"] = matched_extras
                                print(f"[BUNDLE SUM ENGINE] Añadido bonus de {len(matched_extras)} extra(s) (+{extra_cash:.1f}€ cash / +{extra_voucher:.1f}€ vale) a '{cex_query}'")

                    ai_data["fraud_risk_score"] = int(fraud_eval.get("risk", 0) or 0)
                    approved, score, optimal_price, best_net_profit, charger_penalty, reject_msg = evaluate_deal_math_and_score(
                        price=base_price, landed_cost=landed_cost, target=target, ai_data=ai_data, cex_data=cex_data,
                        min_profit=min_profit, min_personal_savings=min_personal_savings, min_score=min_score,
                        filter_pro_seller=True
                    )
                    if not approved:
                        print(f"[VINTED DESCARTADO - {score}/10] {title} ({base_price:.0f}€ | Casa: {landed_cost:.0f}€) -> {reject_msg}")
                        if flash_msg:
                            resolve_flash_discord_alert(
                                flash_msg, approved=False, reason=reject_msg,
                                title=title, price=base_price, item_url=item.get("url", "")
                            )
                        continue

                    kit_folder = build_kit_folder_name("VINTED", item_id, ai_data)
                    print(f"[🔥 CHOLLO VINTED ({score}/10) +{best_net_profit:.0f}€] {title} ({landed_cost:.0f}€ en casa) -> Alertando Discord!")
                    seller_info_obj = item.get("seller") or {}
                    country_tag = (
                        item.get("country_code")
                        or (seller_info_obj.get("country_code") if isinstance(seller_info_obj, dict) else "")
                        or "EU"
                    ).upper()

                    send_discord_alert(
                        conn=conn,
                        platform="VINTED",
                        item_id=item_id,
                        title=title,
                        description=desc or f"Marca: {brand} | Estado: {status_cond}",
                        price=base_price,
                        landed_cost=landed_cost,
                        fees=fees,
                        target=target,
                        ai_data=ai_data,
                        cex_data=cex_data,
                        enhanced_cover_bytes=None,
                        kit_folder=kit_folder,
                        optimal_price=optimal_price,
                        charger_penalty=charger_penalty,
                        net_profit=best_net_profit,
                        item_url=item["url"],
                        checkout_url=item.get("checkout_url", item["url"]),
                        image_url=cover_url,
                        seller_city=f"Vinted ({brand or country_tag})",
                        is_local=False,
                        country_tag=country_tag,
                        shipping_label="📦 Punto Pack + Tasa",
                        price_drop_info=price_drop_info,
                        image_urls=photo_urls,
                        flash_message=flash_msg,
                        fraud_banner=fraud_eval.get("warning_banner", ""),
                        seller=item.get("seller"),
                        item_vec=item_vec
                    )

                    send_one_tap_ntfy_alert(
                        item,
                        {
                            "cex_cash": float((cex_data or {}).get("cash", 0.0) or 0.0),
                            "p2p_net_profit": float(best_net_profit or 0.0),
                            "deal_tier": ai_data.get("deal_tier", 1)
                        },
                        is_flash=False
                    )

                    timing.item_processing_sleep()

            print(f"[RADAR VINTED] {target['name']}: {target_items_count} items ({target_new_count} nuevos)")

        if round_total_fetched > 0:
            parser_tracker.record_platform_success("VINTED", round_total_fetched, health_monitor)
        elif not warmup and targets:
            parser_tracker.record_platform_empty("VINTED", health_monitor, threshold=6, reason="0 anuncios en toda la ronda de Vinted")

        if round_total_fetched == 0:
            print("[VINTED SESSION] Renovando cookies de sesión preventivamente...")
            rotator.rotate()
            vinted._init_session()
        elif rotator.step_round():
            vinted._init_session()

        if warmup:
            print(f"[INFO] Warm-up Vinted completado ({count_platform_seen(conn, 'VINTED')} items). Escaneo activo.")
            warmup = False

        round_num += 1
        duration = round(time.time() - round_start, 1)
        sleep_secs, is_night, transition, reason = timing.calculate_round_sleep(SCAN_INTERVAL, "VINTED")
        if transition:
            health_monitor.notify_transition(transition)

        log_metric(
            event="round_completed",
            service="vinted",
            platform="VINTED",
            round=round_num,
            duration_s=duration,
            items_new=round_new,
            items_evaluated=round_evaluated,
            interval_s=sleep_secs,
            reason=reason
        )

        print(f"[VINTED] Ronda #{round_num} en {duration}s ({round_new} nuevos, {round_evaluated} IA). {reason}. Próximo en {sleep_secs}s.\n")
        sleep_with_heartbeat(sleep_secs)


if __name__ == "__main__":
    main()
