#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reporte de Latencias por Etapa y Plataforma (T2.1).
Calcula p50 y p90 de cada etapa del pipeline (`ms_fetch_to_p1`, `ms_p1`, `ms_vision`, `ms_cex`, `ms_alert`)
a partir de `metrics*.jsonl`, más el retraso publicación -> alerta (`alert_delay_min`) desde `alert_outcomes`
abriendo la BD en modo solo lectura (`?mode=ro`).
"""

import os
import re
import sys
import glob
import json
import sqlite3
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

STAGES = ("ms_fetch_to_p1", "ms_p1", "ms_vision", "ms_cex", "ms_alert")


def parse_since(since_str):
    s = str(since_str or "24h").strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([hdm]?)$", s)
    if not m:
        return timedelta(hours=24)
    val = float(m.group(1))
    unit = m.group(2) or "h"
    if unit == "d":
        return timedelta(days=val)
    if unit == "m":
        return timedelta(minutes=val)
    return timedelta(hours=val)


def percentile(values, pct):
    if not values:
        return 0.0
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return round(xs[0], 1)
    k = (len(xs) - 1) * (float(pct) / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return round(xs[f], 1)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 1)


def find_metrics_files(log_dir=None):
    env_file = os.getenv("METRICS_FILE", "").strip()
    if env_file and os.path.isfile(env_file) and not log_dir:
        return [os.path.abspath(env_file)]

    candidate_dirs = [
        log_dir or "",
        os.getenv("LOG_DIR", "").strip(),
        "/src/data/logs",
        "/app/data/logs",
        "./data/logs",
        "../data/logs",
        os.path.join(os.path.dirname(__file__), "..", "data", "logs"),
    ]
    seen_real = set()
    found = []
    for d in candidate_dirs:
        if not d or not os.path.isdir(d):
            continue
        matches = sorted(glob.glob(os.path.join(d, "metrics*.jsonl")))
        for m in matches:
            if os.path.isfile(m):
                rp = os.path.realpath(m)
                if rp not in seen_real:
                    seen_real.add(rp)
                    found.append(os.path.abspath(m))
        if found:
            return found
    return []


def find_db_path(db_path=None):
    candidates = [
        db_path or "",
        os.getenv("DB_PATH", "").strip(),
        "/work/test.db",
        "/src/data/sniper.db",
        "/app/data/sniper.db",
        "./data/sniper.db",
        os.path.join(os.path.dirname(__file__), "..", "data", "sniper.db"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def _read_alert_delays_readonly(db_file, delta):
    by_plat = defaultdict(list)
    if not db_file or not os.path.isfile(db_file):
        return {}
    cutoff_utc = (datetime.utcnow() - delta).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(db_file)}?mode=ro", uri=True, timeout=10)
        try:
            rows = conn.execute(
                "SELECT COALESCE(platform, 'GLOBAL'), alert_delay_min FROM alert_outcomes "
                "WHERE alert_delay_min IS NOT NULL AND (alert_at IS NULL OR alert_at >= ?)",
                (cutoff_utc,),
            ).fetchall()
            for plat, delay in rows:
                try:
                    by_plat[str(plat).upper()].append(float(delay))
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        pass
    out = {}
    for plat, vals in by_plat.items():
        if vals:
            out[plat] = {
                "count": len(vals),
                "p50": percentile(vals, 50),
                "p90": percentile(vals, 90),
            }
    return out


def run_latency_report(since="24h", log_dir=None, db_path=None):
    delta = parse_since(since)
    cutoff = datetime.now() - delta
    files = find_metrics_files(log_dir=log_dir)

    stage_vals = defaultdict(lambda: {st: [] for st in STAGES})
    outcomes = defaultdict(lambda: defaultdict(int))
    round_durs = defaultdict(list)
    total_pipeline_events = 0

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts_str = rec.get("timestamp", "")
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < cutoff:
                                continue
                    except Exception:
                        continue
                    ev = rec.get("event")
                    plat = str(rec.get("platform") or "GLOBAL").upper()
                    if ev == "item_pipeline":
                        total_pipeline_events += 1
                        outcomes[plat][str(rec.get("outcome") or "unknown")] += 1
                        for st in STAGES:
                            val = rec.get(st)
                            if val is not None:
                                try:
                                    fval = float(val)
                                    if fval > 0 or st in ("ms_fetch_to_p1", "ms_p1"):
                                        stage_vals[plat][st].append(fval)
                                except Exception:
                                    pass
                    elif ev == "round_completed" and rec.get("duration_s") is not None:
                        try:
                            round_durs[plat].append(float(rec["duration_s"]))
                        except Exception:
                            pass
        except Exception:
            pass

    by_platform = {}
    all_plats = sorted(set(stage_vals.keys()) | set(round_durs.keys()))
    for plat in all_plats:
        st_summary = {}
        for st in STAGES:
            vals = stage_vals[plat][st]
            st_summary[st] = {
                "count": len(vals),
                "p50": percentile(vals, 50),
                "p90": percentile(vals, 90),
            }
        r_vals = round_durs.get(plat, [])
        by_platform[plat] = {
            "stages": st_summary,
            "outcomes": dict(outcomes.get(plat, {})),
            "rounds": {
                "count": len(r_vals),
                "p50_s": percentile(r_vals, 50),
                "p90_s": percentile(r_vals, 90),
            },
        }

    resolved_db = find_db_path(db_path=db_path)
    alert_delay = _read_alert_delays_readonly(resolved_db, delta)

    print("=" * 80)
    print(f"⏱️ REPORTE DE LATENCIA POR ETAPA Y PLATAFORMA (ventana: {since})")
    print(f"Ficheros metrics: {len(files)} ({', '.join(os.path.basename(f) for f in files) or 'ninguno'})")
    print(f"Eventos item_pipeline: {total_pipeline_events} | BD RO: {resolved_db or 'N/A'}")
    print("=" * 80)

    for plat, pdata in by_platform.items():
        r = pdata["rounds"]
        print(f"\n[{plat}] Rondas={r['count']} (p50={r['p50_s']}s, p90={r['p90_s']}s) | Outcomes={pdata['outcomes']}")
        for st in STAGES:
            s = pdata["stages"][st]
            if s["count"] > 0:
                print(f"  • {st:<16} n={s['count']:<5} p50={s['p50']:>7.1f} ms   p90={s['p90']:>7.1f} ms")

    if alert_delay:
        print("\n📡 Retraso Publicación -> Alerta (alert_outcomes, min):")
        for plat, d in sorted(alert_delay.items()):
            print(f"  • {plat:<12} n={d['count']:<5} p50={d['p50']:>5.1f} min   p90={d['p90']:>5.1f} min")
    else:
        print("\n📡 Retraso Publicación -> Alerta: 0 registros en ventana.")
    print("=" * 80)

    return {
        "files": files,
        "total_pipeline_events": total_pipeline_events,
        "by_platform": by_platform,
        "alert_delay": alert_delay,
    }


def main():
    parser = argparse.ArgumentParser(description="Reporte de latencia por etapa y plataforma")
    parser.add_argument("--since", type=str, default="24h", help="Ventana temporal (ej. 24h, 7d, 30m)")
    parser.add_argument("--log-dir", type=str, default=None, help="Directorio de logs")
    parser.add_argument("--db", type=str, default=None, help="Ruta a sniper.db")
    args = parser.parse_args()
    run_latency_report(since=args.since, log_dir=args.log_dir, db_path=args.db)


if __name__ == "__main__":
    main()
