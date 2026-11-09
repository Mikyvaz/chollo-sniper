#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditoría Exhaustiva de Costes y Frecuencia de Llamadas IA.
Responde al Punto 1 de CORRECION_FALLOS.md:
¿El gasto proviene de un modelo caro o de un bucle de llamadas frecuentes?
"""

import sqlite3
import os
import json
from collections import defaultdict
from datetime import datetime

DB_PATH = "data/sniper.db"

def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] No se encuentra {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=" * 80)
    print("AUDITORÍA DE COSTES Y PATRONES DE LLAMADAS IA - PUNTO 1 CORRECION_FALLOS.md")
    print("=" * 80)

    # Esquema de ai_cost_log
    c.execute("PRAGMA table_info(ai_cost_log)")
    cols = [r[1] for r in c.fetchall()]
    print(f"Columnas en ai_cost_log: {cols}\n")

    # 2. Resumen por día (Últimos 14 días)
    print("--- 1. CONSUMO DIARIO (ÚLTIMOS 14 DÍAS) ---")
    c.execute("""
        SELECT date(created_at) as dia,
               count(*) as total_calls,
               sum(tokens_in) as sum_in,
               sum(tokens_out) as sum_out,
               sum(usd) as total_usd
        FROM ai_cost_log
        GROUP BY date(created_at)
        ORDER BY dia DESC
        LIMIT 14
    """)
    rows = c.fetchall()
    print(f"{'Día':<12} {'Llamadas':<10} {'Tok In':<12} {'Tok Out':<12} {'USD Total':<12} {'Coste/Llamada (USD)':<20}")
    print("-" * 80)
    for r in rows:
        avg_cost = (r['total_usd'] / r['total_calls']) if r['total_calls'] else 0
        print(f"{r['dia']:<12} {r['total_calls']:<10} {r['sum_in'] or 0:<12,} {r['sum_out'] or 0:<12,} {r['total_usd'] or 0.0:<12.4f} {avg_cost:<20.6f}")

    # 3. Modelos utilizados y su impacto económico
    print("\n--- 2. IMPACTO POR MODELO (HISTÓRICO COMPLETO) ---")
    c.execute("""
        SELECT model,
               count(*) as calls,
               sum(tokens_in) as sum_in,
               sum(tokens_out) as sum_out,
               sum(usd) as total_usd,
               avg(usd) as avg_usd
        FROM ai_cost_log
        GROUP BY model
        ORDER BY total_usd DESC
    """)
    rows = c.fetchall()
    print(f"{'Modelo':<30} {'Llamadas':<10} {'Tok In':<12} {'Tok Out':<10} {'USD Total':<12} {'USD/Llamada':<12} {'% del Coste':<10}")
    print("-" * 96)
    total_usd_all = sum(r['total_usd'] for r in rows) or 1.0
    for r in rows:
        pct = (r['total_usd'] / total_usd_all) * 100
        print(f"{r['model']:<30} {r['calls']:<10} {r['sum_in'] or 0:<12,} {r['sum_out'] or 0:<10,} {r['total_usd'] or 0.0:<12.4f} {r['avg_usd'] or 0.0:<12.6f} {pct:>8.2f}%")

    # 4. Impacto por Tipo de Llamada (Vision vs Prescreen vs Embedding)
    print("\n--- 3. IMPACTO POR TIPO DE LLAMADA ---")
    c.execute("""
        SELECT call_type,
               count(*) as calls,
               sum(tokens_in) as sum_in,
               sum(tokens_out) as sum_out,
               sum(usd) as total_usd,
               avg(tokens_in) as avg_in,
               avg(tokens_out) as avg_out,
               avg(usd) as avg_usd
        FROM ai_cost_log
        GROUP BY call_type
        ORDER BY total_usd DESC
    """)
    rows = c.fetchall()
    print(f"{'Tipo':<15} {'Llamadas':<10} {'Tok In Med':<12} {'Tok Out Med':<12} {'USD Total':<12} {'USD/Llamada':<12} {'% Gasto':<10}")
    print("-" * 85)
    for r in rows:
        pct = (r['total_usd'] / total_usd_all) * 100
        print(f"{r['call_type']:<15} {r['calls']:<10} {int(r['avg_in'] or 0):<12} {int(r['avg_out'] or 0):<12} {r['total_usd'] or 0.0:<12.4f} {r['avg_usd'] or 0.0:<12.6f} {pct:>8.2f}%")

    # 5. Detección de bucles o llamadas repetitivas frecuentes
    print("\n--- 4. ANÁLISIS DE FRECUENCIA Y LLAMADAS REPETIDAS (BUCLES) ---")
    # Comprobar si hay ítems que han recibido múltiples llamadas de visión o prescreen
    c.execute("""
        SELECT radar_id, count(*) as call_count, count(DISTINCT date(created_at, 'unixepoch')) as distinct_days
        FROM ai_cost_log
        GROUP BY radar_id
        ORDER BY call_count DESC
        LIMIT 10
    """)
    rows = c.fetchall()
    print("Top 10 Radares con más llamadas:")
    for r in rows:
        print(f"  • {r['radar_id'] or 'sin_radar'}: {r['call_count']} llamadas")

    # Frecuencia temporal: número de llamadas por hora hoy
    print("\nLlamadas por hora hoy (para detectar ráfagas o bucles descontrolados):")
    c.execute("""
        SELECT strftime('%Y-%m-%d %H:00', created_at) as hora,
               count(*) as calls,
               sum(usd) as usd
        FROM ai_cost_log
        WHERE created_at >= date('now', 'start of day')
        GROUP BY hora
        ORDER BY hora ASC
    """)
    rows = c.fetchall()
    for r in rows:
        print(f"  {r['hora']}: {r['calls']:>3} llamadas ({r['usd']:.4f} USD)")

    # 6. Comprobación de estado de cachés
    print("\n--- 5. EFECTIVIDAD DE CACHÉS ---")
    c.execute("SELECT count(*) FROM ai_prescreen_cache")
    prescreen_entries = c.fetchone()[0]
    c.execute("SELECT count(*) FROM ai_eval_cache")
    eval_entries = c.fetchone()[0]
    c.execute("SELECT count(*) FROM seen_items")
    seen_items_count = c.fetchone()[0]
    print(f"  • seen_items (filtro deduplicación rápida): {seen_items_count:,}")
    print(f"  • ai_prescreen_cache (Puerta 1 texto):       {prescreen_entries:,}")
    print(f"  • ai_eval_cache (Puerta 2 visión):           {eval_entries:,}")

    # 7. Fallos o reintentos registrados en logs
    print("\n--- 6. REVISIÓN DE FALLOS O REINTENTOS EN ai_cost_log ---")
    c.execute("""
        SELECT model, count(*) as count
        FROM ai_cost_log
        WHERE tokens_out = 0 OR usd = 0
        GROUP BY model
    """)
    zero_out = c.fetchall()
    print(f"Llamadas con 0 tokens out o 0 usd: {[dict(r) for r in zero_out]}")

    print("=" * 80)
    conn.close()

if __name__ == "__main__":
    main()
