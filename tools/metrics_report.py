#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reporte de Métricas Operacionales y Rate Limiting (P3-4).
Lee metrics.jsonl y genera estadísticas de:
- Ítems analizados por hora por plataforma.
- Tasa de conversión (ítems -> evaluados -> chollos).
- Latencias de scraping por plataforma.
- Adaptaciones de rate limit y tiempos de ciclo.
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from collections import defaultdict


def get_metrics_path():
    candidates = [
        os.getenv("METRICS_FILE", ""),
        "/app/data/logs/metrics.jsonl",
        "./data/logs/metrics.jsonl",
        "../data/logs/metrics.jsonl",
        os.path.join(os.path.dirname(__file__), "..", "data", "logs", "metrics.jsonl")
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return "./data/logs/metrics.jsonl"


def run_metrics_report(hours=24):
    path = get_metrics_path()
    print("=" * 75)
    print("📈 REPORTE DE MÉTRICAS OPERACIONALES — BOTI SNIPER SUITE")
    print(f"Fichero: {path} | Ventana: últimas {hours}h | Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)

    if not os.path.exists(path):
        print(f"[INFO] No se ha encontrado el fichero de métricas en '{path}'.")
        print("  Las métricas se generarán automáticamente a medida que los snipers completen rondas.")
        return

    cutoff = datetime.now() - timedelta(hours=hours)

    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    ts = datetime.fromisoformat(data.get("timestamp", ""))
                    if ts >= cutoff:
                        records.append(data)
                except Exception:
                    pass
    except Exception as e:
        print(f"[ERROR] Leyendo métricas: {e}")
        return

    if not records:
        print(f"[INFO] 0 registros de métricas encontrados en las últimas {hours} horas.")
        return

    # Agrupaciones
    by_platform = defaultdict(lambda: {"rounds": 0, "new": 0, "evaluated": 0, "duration_s": 0.0, "intervals": []})
    events_count = defaultdict(int)

    for r in records:
        ev = r.get("event", "unknown")
        events_count[ev] += 1
        if ev == "round_completed":
            plat = r.get("platform", "GLOBAL")
            p = by_platform[plat]
            p["rounds"] += 1
            p["new"] += int(r.get("items_new", 0))
            p["evaluated"] += int(r.get("items_evaluated", 0))
            p["duration_s"] += float(r.get("duration_s", 0.0))
            if "interval_s" in r:
                p["intervals"].append(float(r["interval_s"]))

    print(f"\n📦 Resumen General ({len(records)} eventos registrados):")
    for ev, cnt in sorted(events_count.items(), key=lambda x: x[1], reverse=True):
        print(f"  • Evento '{ev}': {cnt} ocurrencias")

    print("\n⏱️ Rendimiento por Plataforma:")
    print(f"{'Plataforma':<15} {'Rondas':<8} {'Nuevos':<10} {'IA Eval':<10} {'Dur. Media':<12} {'Intervalo Medio'}")
    print("-" * 75)
    for plat, p in by_platform.items():
        avg_dur = (p["duration_s"] / max(1, p["rounds"]))
        avg_int = (sum(p["intervals"]) / max(1, len(p["intervals"]))) if p["intervals"] else 0.0
        print(f"{plat:<15} {p['rounds']:<8} {p['new']:<10} {p['evaluated']:<10} {avg_dur:>6.1f}s       {avg_int:>6.1f}s")

    print("=" * 75)


def main():
    parser = argparse.ArgumentParser(description="Reporte de métricas Boti Sniper Suite")
    parser.add_argument("--hours", type=int, default=24, help="Horas a analizar (por defecto 24)")
    args = parser.parse_args()
    run_metrics_report(args.hours)


if __name__ == "__main__":
    main()

