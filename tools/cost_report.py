#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reporte de Costes y Telemetría IA (Boti Arbitrage & Homelab Suite).
Muestra el consumo de tokens, gasto en USD y tasas de acierto de caché.
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime

def get_db_path():
    candidates = [
        os.getenv("DB_PATH", ""),
        "/app/data/sniper.db",
        "./data/sniper.db",
        "../data/sniper.db",
        os.path.join(os.path.dirname(__file__), "..", "data", "sniper.db")
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return "./data/sniper.db"


def run_report(days=7):
    db_path = get_db_path()
    if not os.path.exists(db_path):
        print(f"[ERROR] No se encuentra la base de datos en: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    print("=" * 70)
    print("📊 REPORTE DE TELEMETRÍA Y COSTES DE IA — BOTI SUITE")
    print(f"BD: {db_path} | Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. Resumen general (Hoy / N días)
    try:
        c.execute("SELECT COALESCE(SUM(usd), 0.0), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0), COUNT(*) FROM ai_cost_log WHERE created_at >= date('now', 'start of day')")
        today_usd, today_tin, today_tout, today_calls = c.fetchone()

        c.execute("SELECT COALESCE(SUM(usd), 0.0), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0), COUNT(*) FROM ai_cost_log WHERE created_at >= datetime('now', ?)", (f"-{days} days",))
        period_usd, period_tin, period_tout, period_calls = c.fetchone()

        c.execute("SELECT COALESCE(SUM(usd), 0.0), COUNT(*) FROM ai_cost_log")
        all_usd, all_calls = c.fetchone()
    except Exception as e:
        print(f"[WARN] No se pudo leer la tabla ai_cost_log (quizás aún no tiene registros): {e}")
        today_usd = period_usd = all_usd = 0.0
        today_tin = today_tout = today_calls = 0
        period_tin = period_tout = period_calls = 0
        all_calls = 0

    print(f"\n💰 CONSUMO GENERAL:")
    print(f"  • Hoy:              {today_usd:.4f} USD  | {today_calls} llamadas ({today_tin:,} tok in, {today_tout:,} tok out)")
    print(f"  • Últimos {days} días:    {period_usd:.4f} USD  | {period_calls} llamadas ({period_tin:,} tok in, {period_tout:,} tok out)")
    print(f"  • Histórico total:  {all_usd:.4f} USD  | {all_calls} llamadas")

    # 2. Desglose por tipo de llamada (Puerta 1 vs Puerta 2)
    print(f"\n🚪 DESGLOSE POR TIPO DE LLAMADA (Últimos {days} días):")
    try:
        c.execute("""
            SELECT call_type, COUNT(*), COALESCE(SUM(usd), 0.0), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0)
            FROM ai_cost_log
            WHERE created_at >= datetime('now', ?)
            GROUP BY call_type
            ORDER BY SUM(usd) DESC
        """, (f"-{days} days",))
        rows = c.fetchall()
        if rows:
            print(f"  {'Tipo':<16} {'Llamadas':<10} {'USD':<12} {'Tokens In':<14} {'Tokens Out':<14}")
            print("  " + "-" * 66)
            for r in rows:
                print(f"  {r[0]:<16} {r[1]:<10} {r[2]:.4f} USD    {r[3]:<14,} {r[4]:<14,}")
        else:
            print("  (Sin registros aún en este periodo)")
    except Exception as e:
        print(f"  Error: {e}")

    # 3. Desglose por modelo
    print(f"\n🧠 DESGLOSE POR MODELO (Últimos {days} días):")
    try:
        c.execute("""
            SELECT model, COUNT(*), COALESCE(SUM(usd), 0.0), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0)
            FROM ai_cost_log
            WHERE created_at >= datetime('now', ?)
            GROUP BY model
            ORDER BY SUM(usd) DESC
        """, (f"-{days} days",))
        rows = c.fetchall()
        if rows:
            print(f"  {'Modelo':<28} {'Llamadas':<10} {'USD':<12}")
            print("  " + "-" * 52)
            for r in rows:
                print(f"  {r[0]:<28} {r[1]:<10} {r[2]:.4f} USD")
        else:
            print("  (Sin registros aún)")
    except Exception as e:
        print(f"  Error: {e}")

    # 4. Desglose por plataforma
    print(f"\n🛒 DESGLOSE POR PLATAFORMA (Últimos {days} días):")
    try:
        c.execute("""
            SELECT platform, COUNT(*), COALESCE(SUM(usd), 0.0)
            FROM ai_cost_log
            WHERE created_at >= datetime('now', ?)
            GROUP BY platform
            ORDER BY SUM(usd) DESC
        """, (f"-{days} days",))
        rows = c.fetchall()
        if rows:
            print(f"  {'Plataforma':<18} {'Llamadas':<10} {'USD':<12}")
            print("  " + "-" * 42)
            for r in rows:
                print(f"  {r[0]:<18} {r[1]:<10} {r[2]:.4f} USD")
        else:
            print("  (Sin registros aún)")
    except Exception as e:
        print(f"  Error: {e}")

    # 5. Top 5 radares con mayor gasto
    print(f"\n🎯 TOP 5 RADARES POR GASTO (Últimos {days} días):")
    try:
        c.execute("""
            SELECT radar_id, COUNT(*), COALESCE(SUM(usd), 0.0)
            FROM ai_cost_log
            WHERE created_at >= datetime('now', ?)
            GROUP BY radar_id
            ORDER BY SUM(usd) DESC
            LIMIT 5
        """, (f"-{days} days",))
        rows = c.fetchall()
        if rows:
            for r in rows:
                print(f"  • {r[0] or 'Sin radar'}: {r[2]:.4f} USD ({r[1]} llamadas)")
        else:
            print("  (Sin registros aún)")
    except Exception as e:
        print(f"  Error: {e}")

    # 6. Estadísticas de cachés
    print(f"\n📦 ESTADO DE CACHÉS Y ACIERTOS:")
    try:
        c.execute("SELECT COUNT(*) FROM ai_prescreen_cache")
        prescreen_cache_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM ai_eval_cache")
        eval_cache_count = c.fetchone()[0]
        print(f"  • Entradas en caché Puerta 1 (Prescreen Texto):  {prescreen_cache_count:,}")
        print(f"  • Entradas en caché Puerta 2 (Visión Multimodal): {eval_cache_count:,}")
    except Exception as e:
        print(f"  Error consultando cachés: {e}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reporte de costes de IA")
    parser.add_argument("--days", type=int, default=7, help="Días a analizar (por defecto 7)")
    args = parser.parse_args()
    run_report(days=args.days)

