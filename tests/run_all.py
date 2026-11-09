#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test runner for all Phase tests (test_fase1 through test_fase7_2)
"""

import os
import subprocess
import sys

def main():
    test_dir = os.path.dirname(__file__)
    test_files = sorted([
        f for f in os.listdir(test_dir)
        if f.startswith("test_fase") and f.endswith(".py")
    ])

    print(f"Encontrados {len(test_files)} archivos de test: {test_files}\n")

    for tf in test_files:
        full_path = os.path.join(test_dir, tf)
        print(f"============================================================")
        print(f"▶️  EJECUTANDO: {tf}")
        print(f"============================================================")
        ret = subprocess.run([sys.executable, full_path])
        if ret.returncode != 0:
            print(f"\n❌ ERROR: {tf} falló con código {ret.returncode}!")
            sys.exit(ret.returncode)
        print(f"✅ {tf} COMPLETADO CON ÉXITO.\n")

    print("============================================================")
    print("🏆 ¡TODAS LAS SUITES DE PRUEBA (FASES 1 A 7.2) HAN PASADO!")
    print("============================================================")

if __name__ == "__main__":
    main()

