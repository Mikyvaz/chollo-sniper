#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rastreador de resultados de alertas (Nivel 1: auditoría del cuello de botella).

Vuelve a consultar cada alerta a los 15 min, 1 h, 3 h, 6 h, 24 h y 72 h para saber si sigue disponible,
se reservó, se vendió o desapareció, y con qué demanda (visitas, favoritos, conversaciones en Wallapop).
Así sabemos qué chollos eran reales (los que vuelan) aunque el usuario no los califique.

Se ejecuta como servicio aparte (contenedor outcome-tracker): si falla, los escáneres siguen igual.
Va muy despacio y se retira ante 403/429, porque comparte IP con los escáneres.

Estados: available | reserved | sold | gone (404: borrado o vendido) | unavailable | unknown
"""
import os
import re
import json
import time
import random
import statistics
from datetime import datetime, timedelta

from curl_cffi import requests

import core_engine
from core_engine import get_db, touch_heartbeat, PROXY_URL, _ebay_mgr
from http_client import PlatformClient, NATIVE_PROFILE_HEADERS

SUPPORTED_PLATFORMS = ("WALLAPOP", "VINTED", "EBAY")
CHECKPOINTS_MIN = [15, 60, 180, 360, 1440, 4320]
FINAL_STATUSES = ("sold", "gone")
PACE_SECONDS = {"WALLAPOP": 6.0, "VINTED": 20.0, "EBAY": 60.0}
# eBay es <3% de las alertas y su escáner es sensible: pocas peticiones y retirada larga
BACKOFF_SECONDS = {"WALLAPOP": 15 * 60, "VINTED": 30 * 60, "EBAY": 120 * 60}
BACKFILL_DAYS = int(os.environ.get("OUTCOME_BACKFILL_DAYS", "14"))
LOOP_SLEEP = 30
BATCH = 25

UA = NATIVE_PROFILE_HEADERS["chrome152"]["User-Agent"]
_BS = chr(92)


class Throttled(Exception):
    pass


# ==================== TIEMPO (todo en UTC, mismo formato que CURRENT_TIMESTAMP de SQLite) ====================

def _now():
    return datetime.utcnow().replace(microsecond=0)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _parse(val):
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:19])
    except Exception:
        return None


def _minutes(a, b):
    """Minutos de a hasta b (b - a), o None."""
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 60.0, 1)


# ==================== TABLAS ====================

def ensure_outcome_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_outcomes (
            deal_id TEXT PRIMARY KEY,
            platform TEXT,
            item_id TEXT,
            item_url TEXT,
            alert_at DATETIME,
            published_at DATETIME,
            alert_delay_min REAL,
            initial_price REAL,
            last_price REAL,
            price_changes INTEGER DEFAULT 0,
            last_status TEXT DEFAULT 'pending',
            final_status TEXT,
            last_available_at DATETIME,
            reserved_seen_at DATETIME,
            first_unavailable_at DATETIME,
            views INTEGER,
            favorites INTEGER,
            conversations INTEGER,
            checks_done INTEGER DEFAULT 0,
            unknown_checks INTEGER DEFAULT 0,
            backfill INTEGER DEFAULT 0,
            next_check_at DATETIME,
            last_check_at DATETIME,
            done INTEGER DEFAULT 0,
            discord_state TEXT
        )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(alert_outcomes)").fetchall()]
    for col, ddl in (
        ("seller_id", "TEXT DEFAULT ''"),
        ("seller_status", "TEXT"),
        ("seller_checked_at", "DATETIME"),
        ("discord_state", "TEXT"),
    ):
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE alert_outcomes ADD COLUMN {col} {ddl}")
            except Exception:
                pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_outcomes_next ON alert_outcomes(done, next_check_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_outcome_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT,
            checked_at DATETIME,
            minutes_since_alert REAL,
            status TEXT,
            http_status INTEGER,
            price REAL,
            views INTEGER,
            favorites INTEGER,
            conversations INTEGER,
            detail TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_outcome_checks_deal ON alert_outcome_checks(deal_id, id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fraud_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            item_id TEXT,
            seller_id TEXT DEFAULT '',
            item_url TEXT DEFAULT '',
            risk INTEGER DEFAULT 0,
            codes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            item_status TEXT,
            seller_status TEXT,
            checked_at TIMESTAMP,
            UNIQUE(platform, item_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fraud_samples_pending ON fraud_samples(checked_at, created_at)")
    conn.commit()


def _next_checkpoint(alert_at, now):
    """Siguiente checkpoint posterior a 'now', o None si ya pasaron todos."""
    for cp in CHECKPOINTS_MIN:
        t = alert_at + timedelta(minutes=cp)
        if t > now + timedelta(seconds=30):
            return t
    return None


def register_new_alerts(conn):
    """Da de alta en el seguimiento las alertas nuevas (y las de los últimos BACKFILL_DAYS días la primera vez)."""
    rows = conn.execute(f"""
        SELECT d.deal_id, d.platform, d.item_id, d.item_url, d.created_at, d.price, COALESCE(d.alert_context_json, '')
        FROM deals d LEFT JOIN alert_outcomes o ON o.deal_id = d.deal_id
        WHERE o.deal_id IS NULL
          AND d.platform IN ({', '.join('?' for _ in SUPPORTED_PLATFORMS)})
          AND d.created_at >= datetime('now', ?)
    """, (*SUPPORTED_PLATFORMS, f"-{BACKFILL_DAYS} days")).fetchall()
    now = _now()
    added = 0
    for deal_id, plat, item_id, url, created_at, price, ctx_json in rows:
        alert_at = _parse(created_at)
        if not alert_at or not item_id:
            continue
        try:
            ctx = json.loads(ctx_json) if ctx_json else {}
        except Exception:
            ctx = {}
        published = _parse(ctx.get("published_at_utc"))
        delay = _minutes(published, alert_at)
        if delay is not None and delay < 0:
            delay = 0.0
        elapsed_min = (now - alert_at).total_seconds() / 60.0
        backfill = 1 if elapsed_min > CHECKPOINTS_MIN[0] + 5 else 0
        # Alertas ya pasadas de su primer checkpoint: una comprobación inmediata y luego los que queden
        next_at = now if backfill else alert_at + timedelta(minutes=CHECKPOINTS_MIN[0])
        conn.execute("""
            INSERT OR IGNORE INTO alert_outcomes (deal_id, platform, item_id, item_url, alert_at, published_at,
                alert_delay_min, initial_price, last_price, backfill, next_check_at, seller_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (deal_id, plat, str(item_id), url, _fmt(alert_at), _fmt(published), delay, price, price, backfill,
              _fmt(next_at), str(ctx.get("seller_id") or "")))
        added += 1
    if added:
        conn.commit()
    return added


# ==================== COMPROBACIÓN POR PLATAFORMA ====================

_wallapop_session = None
_vinted_session = None
_vinted_session_at = 0.0


def _wallapop():
    global _wallapop_session
    if _wallapop_session is None:
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        _wallapop_session = PlatformClient(
            "WALLAPOP",
            impersonate="chrome152",
            origin="https://es.wallapop.com",
            referer="https://es.wallapop.com/",
            extra_headers={"X-DeviceOS": "0"},
            proxies=proxies,
        )
    return _wallapop_session


def _vinted(force=False):
    global _vinted_session, _vinted_session_at
    if force or _vinted_session is None or time.time() - _vinted_session_at > 1800:
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        if _vinted_session is None:
            _vinted_session = PlatformClient(
                "VINTED",
                impersonate="chrome152",
                referer="https://www.vinted.es/",
                proxies=proxies,
            )
        else:
            _vinted_session.rotate()
        _vinted_session_at = time.time()
        try:
            _vinted_session.get("https://www.vinted.es/", kind="document", timeout=15)
        except Exception:
            pass
    return _vinted_session


def _result(status, http=None, price=None, views=None, favorites=None, conversations=None, detail=""):
    return {"status": status, "http": http, "price": price, "views": views, "favorites": favorites,
            "conversations": conversations, "detail": str(detail or "")[:200]}


def check_wallapop(item_id):
    r = _wallapop().get(f"https://api.wallapop.com/api/v3/items/{item_id}", timeout=12)
    if r.status_code in (404, 410):
        return _result("gone", r.status_code)
    if r.status_code in (403, 429):
        raise Throttled(f"WALLAPOP HTTP {r.status_code}")
    if r.status_code != 200:
        return _result("unknown", r.status_code, detail=r.text[:120])
    d = r.json()
    counters = d.get("counters") or {}
    price = None
    try:
        price = float(((d.get("price") or {}).get("cash") or {}).get("amount"))
    except Exception:
        pass
    if (d.get("sold") or {}).get("flag"):
        status = "sold"
    elif (d.get("reserved") or {}).get("flag"):
        status = "reserved"
    else:
        status = "available"
    return _result(status, 200, price, counters.get("views"), counters.get("favorites"), counters.get("conversations"))


def parse_vinted_item_status(page_text, item_id):
    """Interpreta la ficha de Vinted (payload RSC). Devuelve (status, favoritos, detalle)."""
    t = page_text.replace(_BS + '"', '"')
    iid = re.escape(str(item_id))
    fav = None
    m_fav = re.search(r'"favourite_count":(\d+),"is_favourite":(?:true|false),"item_id":"' + iid + '"', t)
    if m_fav:
        fav = int(m_fav.group(1))
    m_status = re.search(r'\{"item_id":"' + iid + r'"[^{}]*"title":"([^"]+)"\}[^{}]*"name":"buyer_item_status"', t)
    if m_status:
        title = m_status.group(1)
        low = title.lower()
        if "vend" in low or "sold" in low:
            return "sold", fav, title
        if "reserv" in low:
            return "reserved", fav, title
        return "unavailable", fav, title
    m_ask = re.search(r'\{"can_buy":(true|false)[^{}]*"is_hidden":(true|false)[^{}]*"is_reserved":(true|false)[^{}]*"item_id":"' + iid + '"', t)
    if m_ask:
        can_buy, hidden, reserved = (g == "true" for g in m_ask.groups())
        if reserved:
            return "reserved", fav, "is_reserved"
        if hidden:
            return "unavailable", fav, "is_hidden"
        if can_buy:
            return "available", fav, ""
        return "unavailable", fav, "can_buy=false"
    if '"name":"buy","section"' in t:
        return "available", fav, "buy_section"
    return "unknown", fav, "estructura de ficha no reconocida"


def check_vinted(item_id):
    r = _vinted().get(f"https://www.vinted.es/items/{item_id}", kind="document", timeout=15)
    if r.status_code in (404, 410):
        return _result("gone", r.status_code)
    if r.status_code in (403, 429):
        _vinted(force=True)
        raise Throttled(f"VINTED HTTP {r.status_code}")
    if r.status_code != 200:
        return _result("unknown", r.status_code)
    status, fav, detail = parse_vinted_item_status(r.text, item_id)
    return _result(status, 200, favorites=fav, detail=detail)


def check_ebay(item_id):
    r = _ebay_mgr.get_session().get(f"https://www.ebay.es/itm/{item_id}", timeout=15)
    if r.status_code in (404, 410):
        return _result("gone", r.status_code)
    if r.status_code in (403, 429):
        raise Throttled(f"EBAY HTTP {r.status_code}")
    if r.status_code != 200:
        return _result("unknown", r.status_code)
    t = r.text
    avail = re.findall(r'"availability":"https?://schema\.org/(\w+)"', t)
    if "InStock" in avail or "LimitedAvailability" in avail:
        return _result("available", 200)
    if avail:
        return _result("sold", 200, detail=",".join(sorted(set(avail))))
    if "ha finalizado" in t:
        return _result("sold", 200, detail="ha finalizado")
    if len(t) < 60000:
        raise Throttled("EBAY página de verificación")
    return _result("unknown", 200, detail="sin schema.org availability")


CHECKERS = {"WALLAPOP": check_wallapop, "VINTED": check_vinted, "EBAY": check_ebay}


def check_seller(plat, seller_id):
    """Estado de la cuenta del vendedor un tiempo después: 'active' | 'banned' | 'deleted' | 'unknown'.
    Es la etiqueta de verdad para el antifraude: una cuenta suspendida tras alertar = probable estafa."""
    if plat == "WALLAPOP":
        r = _wallapop().get(f"https://api.wallapop.com/api/v3/users/{seller_id}", timeout=12)
        if r.status_code in (404, 410):
            return "deleted"
        if r.status_code in (403, 429):
            raise Throttled(f"WALLAPOP usuarios HTTP {r.status_code}")
        return "active" if r.status_code == 200 else "unknown"
    if plat == "VINTED":
        r = _vinted().get(f"https://www.vinted.es/api/v2/users/{seller_id}", headers={"Accept": "application/json"}, timeout=12)
        if r.status_code in (404, 410):
            return "deleted"
        if r.status_code in (403, 429):
            raise Throttled(f"VINTED usuarios HTTP {r.status_code}")
        if r.status_code != 200:
            return "unknown"
        u = r.json().get("user") or {}
        if u.get("is_account_banned") or u.get("account_ban_date"):
            return "banned"
        if u.get("is_deleted"):
            return "deleted"
        return "active"
    return "unknown"


def run_seller_checks(conn, limit=5):
    """Revisa vendedores de alertas de hace >= 24 h (una vez por alerta)."""
    rows = conn.execute("""
        SELECT deal_id, platform, seller_id FROM alert_outcomes
        WHERE seller_checked_at IS NULL AND seller_id != '' AND platform IN ('WALLAPOP', 'VINTED')
          AND alert_at <= datetime('now', '-24 hours')
        ORDER BY alert_at LIMIT ?
    """, (limit,)).fetchall()
    n = 0
    for deal_id, plat, seller_id in rows:
        if time.time() < _backoff_until.get(plat, 0):
            continue
        _wait_pace(plat)
        try:
            status = check_seller(plat, seller_id)
        except Throttled as e:
            _backoff_until[plat] = time.time() + BACKOFF_SECONDS.get(plat, 1800)
            print(f"[OUTCOME] {e}: pausa de {BACKOFF_SECONDS.get(plat, 1800) // 60} min para {plat}")
            continue
        except Exception:
            status = "unknown"
        conn.execute("UPDATE alert_outcomes SET seller_status = ?, seller_checked_at = ? WHERE deal_id = ?",
                     (status, _fmt(_now()), deal_id))
        conn.commit()
        n += 1
    return n


def check_pending_fraud_samples(conn, limit=10):
    """Comprueba a las 24 h anuncio y vendedor de la muestra del 10 % de bloqueos (T3.9)."""
    try:
        rows = conn.execute(
            """
            SELECT id, platform, item_id, seller_id
            FROM fraud_samples
            WHERE checked_at IS NULL
              AND created_at <= datetime('now', '-24 hours')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except Exception:
        return 0
    checked = 0
    for sample_id, plat, item_id, seller_id in rows:
        plat_u = str(plat or "").upper()
        if time.time() < _backoff_until.get(plat_u, 0):
            continue
        checker = CHECKERS.get(plat_u)
        if checker:
            _wait_pace(plat_u)
            try:
                res = checker(item_id)
                item_status = res.get("status", "unknown")
            except Throttled as e:
                _backoff_until[plat_u] = time.time() + BACKOFF_SECONDS.get(plat_u, 1800)
                print(f"[OUTCOME] {e}: pausa de {BACKOFF_SECONDS.get(plat_u, 1800) // 60} min para {plat_u}")
                continue
            except Exception:
                item_status = "unknown"
        else:
            item_status = "unknown"

        if seller_id and plat_u in ("WALLAPOP", "VINTED"):
            _wait_pace(plat_u)
            try:
                seller_status = check_seller(plat_u, seller_id)
            except Throttled as e:
                _backoff_until[plat_u] = time.time() + BACKOFF_SECONDS.get(plat_u, 1800)
                print(f"[OUTCOME] {e}: pausa de {BACKOFF_SECONDS.get(plat_u, 1800) // 60} min para {plat_u}")
                continue
            except Exception:
                seller_status = "unknown"
        else:
            seller_status = "unknown"

        conn.execute(
            "UPDATE fraud_samples SET item_status = ?, seller_status = ?, checked_at = ? WHERE id = ?",
            (item_status, seller_status, _fmt(_now()), sample_id),
        )
        conn.commit()
        checked += 1
        touch_heartbeat()
    return checked


# ==================== CICLO ====================

def record_check(conn, row, res, now):
    (deal_id, plat, item_id, alert_at_s, last_price, price_changes, reserved_seen_at,
     first_unavail_at, last_avail_at, checks_done, unknown_checks) = row
    alert_at = _parse(alert_at_s)
    status = res["status"]
    conn.execute("""
        INSERT INTO alert_outcome_checks (deal_id, checked_at, minutes_since_alert, status, http_status, price,
                                          views, favorites, conversations, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (deal_id, _fmt(now), _minutes(alert_at, now), status, res["http"], res["price"], res["views"],
          res["favorites"], res["conversations"], res["detail"]))

    new_price = last_price
    if res["price"] is not None:
        if last_price is not None and abs(float(res["price"]) - float(last_price)) >= 0.5:
            price_changes = (price_changes or 0) + 1
        new_price = res["price"]
    if status == "available":
        last_avail_at = _fmt(now)
    if status == "reserved" and not reserved_seen_at:
        reserved_seen_at = _fmt(now)
    if status in ("sold", "gone", "unavailable") and not first_unavail_at:
        first_unavail_at = _fmt(now)
    unknown_checks = (unknown_checks or 0) + (1 if status == "unknown" else 0)

    final = status if status in FINAL_STATUSES else None
    nxt = None if final else _next_checkpoint(alert_at, now)
    done = 1 if (final or nxt is None) else 0
    conn.execute("""
        UPDATE alert_outcomes SET last_status = ?, final_status = COALESCE(?, final_status), last_price = ?,
            price_changes = ?, reserved_seen_at = ?, first_unavailable_at = ?, last_available_at = ?,
            views = COALESCE(?, views), favorites = COALESCE(?, favorites), conversations = COALESCE(?, conversations),
            checks_done = ?, unknown_checks = ?, last_check_at = ?, next_check_at = ?, done = ?
        WHERE deal_id = ?
    """, (status, final, new_price, price_changes, reserved_seen_at, first_unavail_at, last_avail_at,
          res["views"], res["favorites"], res["conversations"], (checks_done or 0) + 1, unknown_checks,
          _fmt(now), _fmt(nxt), done, deal_id))
    conn.commit()
    if status in ("reserved", "sold", "gone"):
        cur_state_row = conn.execute(
            "SELECT discord_state FROM alert_outcomes WHERE deal_id = ?", (deal_id,)
        ).fetchone()
        cur_discord_state = cur_state_row[0] if cur_state_row else None
        if cur_discord_state != status:
            try:
                core_engine._discord_edit_message(conn, deal_id, status, age_min=_minutes(alert_at, now))
            except Exception as e:
                print(f"[WARN] Error editando alerta Discord {deal_id} ({status}): {e}")


_last_request = {}
_backoff_until = {}


def _wait_pace(plat):
    gap = PACE_SECONDS.get(plat, 10.0) * random.uniform(1.0, 1.4)
    wait = _last_request.get(plat, 0) + gap - time.time()
    while wait > 0:
        touch_heartbeat()
        time.sleep(min(wait, 10))
        wait = _last_request.get(plat, 0) + gap - time.time()
    _last_request[plat] = time.time()


def run_cycle(conn):
    added = register_new_alerts(conn)
    rows = conn.execute("""
        SELECT deal_id, platform, item_id, alert_at, last_price, price_changes, reserved_seen_at,
               first_unavailable_at, last_available_at, checks_done, unknown_checks
        FROM alert_outcomes WHERE done = 0 AND next_check_at <= ? ORDER BY next_check_at LIMIT ?
    """, (_fmt(_now()), BATCH)).fetchall()
    stats = {"added": added, "checked": 0, "throttled": 0}
    for row in rows:
        plat = row[1]
        if time.time() < _backoff_until.get(plat, 0):
            continue
        checker = CHECKERS.get(plat)
        if not checker:
            conn.execute("UPDATE alert_outcomes SET done = 1 WHERE deal_id = ?", (row[0],))
            conn.commit()
            continue
        _wait_pace(plat)
        try:
            res = checker(row[2])
        except Throttled as e:
            _backoff_until[plat] = time.time() + BACKOFF_SECONDS.get(plat, 1800)
            print(f"[OUTCOME] {e}: pausa de {BACKOFF_SECONDS.get(plat, 1800) // 60} min para {plat}")
            stats["throttled"] += 1
            continue
        except Exception as e:
            res = _result("unknown", None, detail=f"error: {e}")
        record_check(conn, row, res, _now())
        stats["checked"] += 1
        touch_heartbeat()
    stats["sellers"] = run_seller_checks(conn)
    stats["fraud_samples"] = check_pending_fraud_samples(conn)
    return stats


def main():
    print("[OUTCOME] Rastreador de resultados de alertas iniciado "
          f"(checkpoints {CHECKPOINTS_MIN} min, plataformas {', '.join(SUPPORTED_PLATFORMS)}).")
    touch_heartbeat()
    conn = get_db()
    ensure_outcome_tables(conn)
    while True:
        try:
            stats = run_cycle(conn)
            if stats["added"] or stats["checked"] or stats["throttled"] or stats.get("sellers"):
                print(f"[OUTCOME] altas {stats['added']} · comprobadas {stats['checked']} · "
                      f"vendedores {stats.get('sellers', 0)} · bloqueos {stats['throttled']}")
        except Exception as e:
            print(f"[OUTCOME ERROR] {e}")
            try:
                conn.close()
            except Exception:
                pass
            time.sleep(10)
            conn = get_db()
        touch_heartbeat()
        time.sleep(LOOP_SLEEP)


# ==================== INFORMES (los usa /resultados en bot.py) ====================

def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def get_outcome_summary(conn, days=None):
    ensure_outcome_tables(conn)
    where = "WHERE o.alert_at >= datetime('now', ?)" if days else ""
    params = [f"-{int(days)} days"] if days else []
    rows = conn.execute(f"""
        SELECT o.platform, COALESCE(d.radar_name, ''), o.alert_at, o.alert_delay_min, o.first_unavailable_at,
               o.last_status, o.final_status, o.checks_done, o.reserved_seen_at, o.last_available_at
        FROM alert_outcomes o LEFT JOIN deals d ON d.deal_id = o.deal_id {where}
    """, params).fetchall()
    windows = (60, 360, 1440)
    by_platform = {}
    by_radar = {}
    for plat, radar, alert_at_s, delay, unavail_s, last_status, final, checks, reserved_s, avail_s in rows:
        if not checks:
            continue
        alert_at = _parse(alert_at_s)
        unavail = _parse(unavail_s)
        mins_to_gone = _minutes(alert_at, unavail) if unavail else None
        mins_last_avail = _minutes(alert_at, _parse(avail_s)) if avail_s else None
        for bucket, key in ((by_platform, plat or "?"), (by_radar, radar or "?")):
            b = bucket.setdefault(key, {"tracked": 0, "delays": [], "gone_total": 0, "reserved": 0,
                                        **{f"eligible_{w}": 0 for w in windows}, **{f"gone_{w}": 0 for w in windows}})
            b["tracked"] += 1
            if delay is not None:
                b["delays"].append(float(delay))
            if mins_to_gone is not None:
                b["gone_total"] += 1
            if reserved_s:
                b["reserved"] += 1
            for w in windows:
                # Solo cuenta si hay evidencia: desapareció antes de w, o se vio disponible después de w.
                # (Una alerta comprobada por primera vez tarde no dice nada de su primera hora.)
                gone_in_w = mins_to_gone is not None and mins_to_gone <= w
                alive_after_w = mins_last_avail is not None and mins_last_avail >= w
                if gone_in_w or alive_after_w:
                    b[f"eligible_{w}"] += 1
                    if gone_in_w:
                        b[f"gone_{w}"] += 1

    def finish(b):
        out = {"tracked": b["tracked"], "gone_total": b["gone_total"], "reserved": b["reserved"],
               "median_delay_min": round(statistics.median(b["delays"]), 1) if b["delays"] else None,
               "with_delay": len(b["delays"])}
        for w in windows:
            out[f"gone_pct_{w}"] = _pct(b[f"gone_{w}"], b[f"eligible_{w}"])
            out[f"eligible_{w}"] = b[f"eligible_{w}"]
        return out

    # Verdad antifraude: ¿cuántos vendedores acabaron suspendidos/eliminados? (sin valoraciones vs con historial)
    seller_truth = {"sin_valoraciones": {"checked": 0, "banned": 0}, "con_historial": {"checked": 0, "banned": 0}}
    for st, ctx_json in conn.execute(f"""
        SELECT o.seller_status, COALESCE(d.alert_context_json, '') FROM alert_outcomes o
        LEFT JOIN deals d ON d.deal_id = o.deal_id
        WHERE o.seller_status IN ('active', 'banned', 'deleted') {('AND ' + where[6:]) if where else ''}
    """, params).fetchall():
        try:
            reviews = (json.loads(ctx_json).get("seller") or {}).get("reviews") if ctx_json else None
        except Exception:
            reviews = None
        if reviews is None:
            continue
        g = seller_truth["sin_valoraciones" if int(reviews) == 0 else "con_historial"]
        g["checked"] += 1
        g["banned"] += 1 if st in ("banned", "deleted") else 0

    pending = conn.execute("SELECT COUNT(*) FROM alert_outcomes WHERE checks_done = 0").fetchone()[0]
    return {"by_platform": {k: finish(v) for k, v in by_platform.items()},
            "by_radar": {k: finish(v) for k, v in by_radar.items()},
            "seller_truth": seller_truth, "pending": pending, "total": len(rows)}


def export_outcomes_csv(conn):
    import csv
    import io
    ensure_outcome_tables(conn)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["deal_id", "plataforma", "radar", "producto", "precio_alerta", "beneficio_estimado", "tipo_alerta",
                "alerta_utc", "publicado_utc", "retraso_publicacion_a_alerta_min", "estado_final", "ultimo_estado",
                "min_hasta_no_disponible", "min_ultima_vez_disponible", "min_hasta_reservado", "comprobaciones",
                "visitas", "favoritos", "conversaciones", "ultimo_precio", "cambios_precio", "seguimiento_tardio",
                "vendedor_valoraciones", "vendedor_antiguedad_dias", "antiguedad_fuente", "vendedor_vendidos",
                "vendedor_comprados", "vendedor_denuncias", "cuenta_vendedor_despues", "aviso_sonoro", "motivo_silencio",
                "mi_veredicto", "mis_motivos", "mi_nota", "url"])
    try:
        fb_rows = conn.execute("""
            SELECT f.deal_id, f.verdict, f.reasons, f.note FROM alert_feedback f
            JOIN (SELECT deal_id, MAX(id) AS mid FROM alert_feedback GROUP BY deal_id) l ON l.mid = f.id
        """).fetchall()
    except Exception:
        fb_rows = []
    fb = {r[0]: r[1:] for r in fb_rows}
    for r in conn.execute("""
        SELECT o.deal_id, o.platform, COALESCE(d.radar_name, ''), COALESCE(NULLIF(d.identified_model, ''), d.title, ''),
               o.initial_price, d.net_profit, d.deal_type, o.alert_at, o.published_at, o.alert_delay_min,
               o.final_status, o.last_status, o.first_unavailable_at, o.last_available_at, o.reserved_seen_at,
               o.checks_done, o.views, o.favorites, o.conversations, o.last_price, o.price_changes, o.backfill, o.item_url,
               o.seller_status, COALESCE(d.alert_context_json, '')
        FROM alert_outcomes o LEFT JOIN deals d ON d.deal_id = o.deal_id ORDER BY o.alert_at
    """).fetchall():
        alert_at = _parse(r[7])
        verdict, reasons, note = fb.get(r[0], ("", "", ""))
        try:
            ctx = json.loads(r[24]) if r[24] else {}
        except Exception:
            ctx = {}
        sel = ctx.get("seller") or {}

        def v(x):
            return "" if x is None else x

        push = ctx.get("push")
        w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], v(r[8]), v(r[9]), v(r[10]), r[11],
                    v(_minutes(alert_at, _parse(r[12]))), v(_minutes(alert_at, _parse(r[13]))),
                    v(_minutes(alert_at, _parse(r[14]))), r[15], v(r[16]), v(r[17]), v(r[18]), v(r[19]), r[20],
                    "sí" if r[21] else "",
                    v(sel.get("reviews")), v(sel.get("age_days")), v(sel.get("seller_age_source")),
                    v(sel.get("seller_sold_count")), v(sel.get("seller_bought_count")), v(sel.get("seller_reports")),
                    v(r[23]), "" if push is None else ("sí" if push else "no"), v(ctx.get("quiet_reason")),
                    verdict, reasons, note, r[22]])
    return buf.getvalue()


if __name__ == "__main__":
    main()
