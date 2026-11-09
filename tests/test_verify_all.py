#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Runner Maestro de Verificación - Puntos 2, 3, 4, 5 y 6 de CORRECION_FALLOS.md
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_verify_punto2 import test_model_routing_strict
from tests.test_verify_punto3 import test_punto3_complete
from tests.test_verify_punto4 import test_punto4_pruning_and_sanitization
from tests.test_verify_punto5 import test_punto5_complete
from tests.test_verify_punto6 import test_punto6_complete

def run_all_verifications():
    print("============================================================")
    print("🚀 EJECUTANDO SUITE MAESTRA DE VERIFICACIÓN (PUNTOS 2 AL 6)")
    print("============================================================\n")

    print(">>> [PUNTO 2] Model Routing a Flash-Lite / Mini")
    test_model_routing_strict()
    print("\n------------------------------------------------------------\n")

    print(">>> [PUNTO 3] AI Gateway Centralizado & Hard Limit (0.10$/día)")
    test_punto3_complete()
    print("\n------------------------------------------------------------\n")

    print(">>> [PUNTO 4] Poda de Contexto y Sanitización de Entradas")
    test_punto4_pruning_and_sanitization()
    print("\n------------------------------------------------------------\n")

    print(">>> [PUNTO 5] Caché Local por Hash SHA-256 (Dos Niveles)")
    test_punto5_complete()
    print("\n------------------------------------------------------------\n")

    print(">>> [PUNTO 6] Concurrencia, Abort Timeout y Limpiador JSON")
    test_punto6_complete()
    print("\n============================================================")
    print("🎉 TODAS LAS VERIFICACIONES (P2 - P6) SUPERADAS CON ÉXITO")
    print("============================================================")

if __name__ == "__main__":
    run_all_verifications()
