#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import os

db_path = "/opt/wallapop-sniper/data/sniper.db"
conn = sqlite3.connect(db_path)
c = conn.cursor()

tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for t in tables:
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()]
    print(f"Table '{t}': {cols}")

print("\n--- DEALS (last 5) ---")
for row in c.execute("SELECT * FROM deals ORDER BY ROWID DESC LIMIT 5").fetchall():
    print(row)

print("\n--- AI_PRESCREEN_CACHE (last 5) ---")
for row in c.execute("SELECT * FROM ai_prescreen_cache ORDER BY ROWID DESC LIMIT 5").fetchall():
    print(row)

print("\n--- FRAUD_LOG (last 5) ---")
for row in c.execute("SELECT * FROM fraud_log ORDER BY ROWID DESC LIMIT 5").fetchall():
    print(row)
