#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Informe de cadencia y frescura (solo lectura) a partir de metrics.<servicio>.jsonl.

Responde, por plataforma y tier:
  - cada cuánto se sondea de verdad cada consulta (mediana/p90 entre sondeos consecutivos, de día);
  - qué cadencia planificó el QueryScheduler (`interval_eff_s`) y cuántos candidatos/h aprende (`yield_per_h`);
  - cuánto llevaba publicado un anuncio nuevo cuando lo vimos por primera vez (`fresh_age_*`):
    en Wallapop es, sobre todo, el retraso de indexación de su buscador, que no depende de nosotros.

Uso:
  python3 tools/cadence_report.py --since 24h [--log-dir data/logs] [--by-query]
"""

import argparse
import glob
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta


def parse_since(text):
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([hdm]?)$", str(text or "24h").strip().lower())
    if not m:
        return timedelta(hours=24)
    val, unit = float(m.group(1)), (m.group(2) or "h")
    return timedelta(days=val) if unit == "d" else timedelta(minutes=val) if unit == "m" else timedelta(hours=val)


def pct(values, p):
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def iter_events(log_dir, service, since):
    for path in sorted(glob.glob(os.path.join(log_dir, f"metrics.{service}.jsonl*"))):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip().lstrip("\x00")
                    if '"query_completed"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                        dt = datetime.fromisoformat(ev["timestamp"])
                    except Exception:
                        continue
                    if dt >= since:
                        ev["_dt"] = dt
                        yield ev
        except OSError:
            continue


def is_night(dt):
    return 2 <= dt.hour < 7


def report(log_dir, service, since, by_query=False):
    events = sorted(iter_events(log_dir, service, since), key=lambda e: e["_dt"])
    print(f"\n=== {service.upper()} · {len(events)} sondeos desde {since:%Y-%m-%d %H:%M} ===")
    if not events:
        return
    by_q = defaultdict(list)
    for ev in events:
        by_q[(ev.get("tier") or "?", ev.get("radar_id") or "", ev.get("query") or "")].append(ev)

    tier_gaps, tier_plan, tier_fresh, tier_cand = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(int)
    rows = []
    for key, evs in by_q.items():
        tier = key[0]
        gaps = [(b["_dt"] - a["_dt"]).total_seconds() for a, b in zip(evs, evs[1:])
                if not is_night(b["_dt"]) and (b["_dt"] - a["_dt"]).total_seconds() <= 3600]
        tier_gaps[tier].extend(gaps)
        plan = [float(e["interval_eff_s"]) for e in evs if e.get("interval_eff_s") is not None]
        tier_plan[tier].extend(plan)
        fresh_min = [float(e["fresh_age_min_s"]) for e in evs if e.get("fresh_age_min_s") is not None]
        tier_fresh[tier].extend(fresh_min)
        cand = sum(int(e.get("candidates") or 0) for e in evs)
        tier_cand[tier] += cand
        yph = next((float(e["yield_per_h"]) for e in reversed(evs) if e.get("yield_per_h") is not None), None)
        rows.append((tier, key[1], key[2], len(evs), pct(gaps, 50), pct(gaps, 90), pct(plan, 50), cand, yph,
                     pct(fresh_min, 50)))

    print(f"  {'tier':9} {'sondeo real med/p90 (s, día)':>30} {'plan med (s)':>13} {'candidatos':>11} {'1.ª vista: edad mín. med (s)':>30}")
    for tier in sorted(tier_gaps):
        g = tier_gaps[tier]
        print(f"  {tier:9} {fmt(pct(g, 50)):>14} / {fmt(pct(g, 90)):<14} {fmt(pct(tier_plan[tier], 50)):>13} "
              f"{tier_cand[tier]:>11} {fmt(pct(tier_fresh[tier], 50)):>30}")
    fresh_all = [v for vals in tier_fresh.values() for v in vals]
    if fresh_all:
        print(f"  Anuncio más reciente de cada sondeo con novedades: mediana {fmt(pct(fresh_all, 50))} s · "
              f"p10 {fmt(pct(fresh_all, 10))} s · p90 {fmt(pct(fresh_all, 90))} s (n={len(fresh_all)})")
    if by_query:
        print(f"  {'tier':9} {'radar':22} {'consulta':24} {'n':>6} {'real med':>8} {'p90':>7} {'plan':>7} {'cand':>5} {'cand/h':>7} {'edad med':>8}")
        for r in sorted(rows, key=lambda r: (r[0], -(r[8] or 0))):
            print(f"  {r[0]:9} {r[1][:22]:22} {r[2][:24]:24} {r[3]:6d} {fmt(r[4]):>8} {fmt(r[5]):>7} "
                  f"{fmt(r[6]):>7} {r[7]:5d} {fmt(r[8], 2):>7} {fmt(r[9]):>8}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="24h")
    ap.add_argument("--log-dir", default=os.getenv("LOG_DIR") or ("/app/data/logs" if os.path.isdir("/app/data/logs") else "data/logs"))
    ap.add_argument("--by-query", action="store_true")
    args = ap.parse_args()
    since = datetime.now() - parse_since(args.since)
    for service in ("wallapop-sniper", "vinted-sniper"):
        report(args.log_dir, service, since, by_query=args.by_query)


if __name__ == "__main__":
    main()
