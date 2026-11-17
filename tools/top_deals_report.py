#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Precisión de las alertas de compra directa por nota (solo lectura sobre data/sniper.db).

Responde a: ¿cuándo es fiable una nota 10 como para comprar sin mirar?
  - Por nota y plataforma: alertas, % con aviso sonoro, % que desaparece en <=20 min y tus calificaciones.
  - Errores del sistema según tus calificaciones (modelo equivocado, CeX mal, estafa, defecto...).
  - Preparación para una compra automática acotada: clase estricta (nota 10, Vinted, <= --max-price €,
    vendedor con >= --min-reviews valoraciones reales, sin avisos de riesgo ni vendedor profesional)
    y el umbral exigido (>= --min-rated calificadas y >= --min-precision de aciertos).

Las alertas de Vinted anteriores a 2026.11.17 guardaban «12 valoraciones» por un fallo de lectura:
ese 12 se trata como dato desconocido.

Uso: python3 tools/top_deals_report.py [--days 30] [--db data/sniper.db]
"""

import argparse
import json
import os
import sqlite3
from collections import Counter, defaultdict

# Motivos que señalan un error de juicio del sistema (no de rapidez o de interés personal)
SYSTEM_ERRORS = {
    "seller_new", "seller_bad", "scam_signs", "stock_photos", "not_working_proof", "defect", "ambiguous",
    "wrong_model", "cex_wrong", "margin_inflated", "price_not_deal", "shipping_cost",
}
FIXED_VINTED_REVIEWS_VERSION = "2026.11.17"


def default_db():
    for p in (os.getenv("DB_PATH", ""), "/app/data/sniper.db", "data/sniper.db"):
        if p and os.path.exists(p):
            return p
    return "data/sniper.db"


def seller_reviews(platform, ctx):
    seller = ctx.get("seller") or {}
    rv = seller.get("reviews", seller.get("seller_reviews_count"))
    if rv is None:
        return None
    try:
        rv = int(rv)
    except (TypeError, ValueError):
        return None
    version = str(ctx.get("pipeline_version") or "")
    if platform == "VINTED" and rv == 12 and version[:10] < FIXED_VINTED_REVIEWS_VERSION:
        return None
    return rv


def load_rows(conn, days):
    q = """
        SELECT d.deal_id, d.platform, d.deal_score, d.deal_type, d.price, d.net_profit, d.alert_context_json,
               d.status, o.alert_at, o.first_unavailable_at, o.reserved_seen_at, o.backfill,
               (SELECT verdict FROM alert_feedback f WHERE f.deal_id = d.deal_id ORDER BY f.id DESC LIMIT 1),
               (SELECT reasons FROM alert_feedback f WHERE f.deal_id = d.deal_id ORDER BY f.id DESC LIMIT 1)
        FROM deals d LEFT JOIN alert_outcomes o ON o.deal_id = d.deal_id
        WHERE d.created_at >= datetime('now', ?) AND d.deal_type = 'direct_chollo'
    """
    out = []
    for r in conn.execute(q, (f"-{int(days)} days",)):
        try:
            ctx = json.loads(r[6]) if r[6] else {}
            if not isinstance(ctx, dict):
                ctx = {}
        except Exception:
            ctx = {}
        gone_min = None
        end = r[9] or r[10]
        if r[8] and end:
            try:
                gone_min = conn.execute("SELECT (julianday(?) - julianday(?)) * 1440", (end, r[8])).fetchone()[0]
            except Exception:
                gone_min = None
        verdict, reasons_raw = r[12], r[13]
        reasons = {x.strip() for x in str(reasons_raw or "").split(",") if x.strip()}
        out.append({
            "deal_id": r[0], "platform": str(r[1] or "").upper(), "score": int(r[2] or 0), "price": float(r[4] or 0),
            "profit": float(r[5] or 0), "ctx": ctx, "status": r[7], "gone_min": gone_min,
            "tracked": bool(r[8]) and not r[11],   # seguimiento desde la alerta (no incorporado tarde)
            "verdict": verdict, "reasons": reasons,
            # Cualquier motivo de error cuenta aunque el veredicto sea «good» (p. ej. «lo compré, modelo equivocado»)
            "error": bool(verdict) and (bool(reasons & SYSTEM_ERRORS) or verdict == "bad"),
            "reviews": seller_reviews(str(r[1] or "").upper(), ctx),
        })
    return out


def pct(n, d):
    return f"{100.0 * n / d:3.0f}%" if d else "  - "


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=default_db())
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--max-price", type=float, default=150.0)
    ap.add_argument("--min-reviews", type=int, default=20)
    ap.add_argument("--min-rated", type=int, default=30)
    ap.add_argument("--min-precision", type=float, default=0.95)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
    rows = load_rows(conn, args.days)
    print(f"Compras directas alertadas en {args.days} días: {len(rows)}")

    groups = defaultdict(list)
    for r in rows:
        groups[(r["score"], r["platform"])].append(r)
    print("\n(sonora: % de las que guardan decisión de aviso · fuera<=20min: % de las seguidas desde la alerta)")
    print(f"{'nota':>4} {'plataforma':10} {'n':>4} {'sonora':>12} {'fuera<=20min':>14} {'calif.':>6} {'buenas':>6} {'errores':>7} {'margen med':>10}")
    for (score, plat), g in sorted(groups.items(), key=lambda kv: (-kv[0][0], kv[0][1])):
        known = [r for r in g if r["ctx"].get("push") is not None]
        push = sum(1 for r in known if r["ctx"].get("push") is True)
        tracked = [r for r in g if r["tracked"]]
        fast = sum(1 for r in tracked if r["gone_min"] is not None and r["gone_min"] <= 20)
        rated = [r for r in g if r["verdict"]]
        good = sum(1 for r in rated if r["verdict"] == "good" and not r["error"])
        errs = sum(1 for r in rated if r["error"])
        profits = sorted(r["profit"] for r in g)
        print(f"{score:>4} {plat:10} {len(g):>4} {pct(push, len(known)):>6} (n={len(known):>2}) "
              f"{pct(fast, len(tracked)):>6} (n={len(tracked):>2}) {len(rated):>6} "
              f"{good:>6} {errs:>7} {profits[len(profits) // 2]:>9.0f}€")

    top = [r for r in rows if r["score"] >= 10]
    err_reasons = Counter(x for r in top if r["verdict"] for x in (r["reasons"] & SYSTEM_ERRORS))
    quiet = Counter(r["ctx"].get("quiet_reason") for r in top if r["ctx"].get("push") is False)
    print(f"\nNota 10: {len(top)} alertas · calificadas {sum(1 for r in top if r['verdict'])} · "
          f"errores señalados por ti: {dict(err_reasons) or 'ninguno'}")
    print(f"Nota 10 que no sonaron, por motivo: {dict(quiet) or 'ninguna'}")
    unknown_rv = sum(1 for r in top if r["reviews"] is None)
    print(f"Nota 10 con valoraciones del vendedor desconocidas (incluye el «12» erróneo de Vinted): {unknown_rv}")

    strict = [r for r in top
              if r["platform"] == "VINTED" and r["price"] <= args.max_price
              and (r["reviews"] or 0) >= args.min_reviews
              and not str(r["ctx"].get("fraud_banner") or "").strip()
              and not (r["ctx"].get("seller") or {}).get("is_business")]
    rated = [r for r in strict if r["verdict"]]
    correct = [r for r in rated if r["verdict"] == "good" and not r["error"]]
    precision = (len(correct) / len(rated)) if rated else 0.0
    print(f"\nClase candidata a compra automática (nota 10 · Vinted · <= {args.max_price:.0f} € · "
          f">= {args.min_reviews} valoraciones · sin avisos · no profesional): {len(strict)} alertas, "
          f"{len(rated)} calificadas, precisión {precision * 100:.0f}%")
    if len(rated) < args.min_rated:
        print(f"  NO LISTO: faltan {args.min_rated - len(rated)} calificaciones (usa 📝 Calificar en estas alertas).")
    elif precision < args.min_precision:
        print(f"  NO LISTO: precisión {precision * 100:.0f}% < {args.min_precision * 100:.0f}% exigido.")
    else:
        print("  Cumple el umbral de datos. Aún quedan las decisiones de riesgo (condiciones de uso, saldo, topes).")


if __name__ == "__main__":
    main()
