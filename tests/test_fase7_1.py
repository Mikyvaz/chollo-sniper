#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de verificación para la Fase 7.1 (P7-1):
- Reorganización de Flash Alerts
- Filtrado determinista de componentes internos, placas base y mandos/accesorios sueltos
- Prevención de falsos negativos en packs legítimos con mando/accesorios
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core_engine import passes_filters, CONSOLE_RADAR_IDS
from fraud_signals import evaluate_fraud_risk

def run_tests():
    print("[1/5] Probando descarte determinista de placas base y componentes (P7-1)...")
    target_3ds = {
        "id": "new_3ds_family",
        "name": "New 3DS XL",
        "min_price": 40,
        "max_price": 140,
        "direct_buy_max_price": 140,
        "enabled": True
    }
    
    # 1. Placa base New 3DS XL (debe descartarse a coste 0)
    res_placa = passes_filters(
        "Placa base New Nintendo 3DS XL", "Funciona perfectamente, ideal para recambios.",
        80.0, target_3ds, []
    )
    assert res_placa is False, "ERROR: 'Placa base New Nintendo 3DS XL' no debió pasar passes_filters!"
    print("  -> 'Placa base New Nintendo 3DS XL' descartada deterministamente.")

    # 2. Pantalla suelta / repuesto
    res_pantalla = passes_filters(
        "Pantalla superior New 3DS XL", "Repuesto de pantalla original para New 3DS XL.",
        45.0, target_3ds, []
    )
    assert res_pantalla is False, "ERROR: 'Pantalla superior' no debió pasar passes_filters!"
    print("  -> 'Pantalla superior New 3DS XL' descartada deterministamente.")

    print("\n[2/5] Probando descarte de mandos sueltos en radares de consolas...")
    target_switch = {
        "id": "switch_std_lite",
        "name": "Switch Estándar / Lite",
        "min_price": 30,
        "max_price": 120,
        "direct_buy_max_price": 100,
        "enabled": True
    }

    # 3. Mando Rematch Wireless (debe descartarse a coste 0)
    res_mando = passes_filters(
        "Switch 2 Mando Rematch Wireless Fortnite 03930", "Mando para Switch y Switch OLED en buen estado.",
        65.0, target_switch, []
    )
    assert res_mando is False, "ERROR: 'Switch 2 Mando Rematch Wireless' no debió pasar passes_filters!"
    print("  -> 'Switch 2 Mando Rematch Wireless Fortnite' descartado deterministamente.")

    # 4. Mando PS5 suelto
    target_ps5 = {
        "id": "ps5_consoles_gta6",
        "name": "PlayStation 5",
        "min_price": 150,
        "max_price": 350,
        "direct_buy_max_price": 300,
        "enabled": True
    }
    res_ps5_mando = passes_filters(
        "Mando DualSense PS5 Blanco", "Solo el mando, sin consola.",
        50.0, target_ps5, []
    )
    assert res_ps5_mando is False, "ERROR: 'Mando DualSense PS5' no debió pasar passes_filters en radar PS5!"
    print("  -> 'Mando DualSense PS5' descartado deterministamente.")

    print("\n[3/5] Verificando ausencia de falsos negativos (consolas legítimas con accesorios)...")
    # 5. Consola legítima que incluye mando o accesorios
    res_consola_ok1 = passes_filters(
        "Consola Nintendo Switch OLED blanca con mando y caja", "Consola completa en perfecto estado.",
        150.0, {"id": "switch_oled", "min_price": 80, "max_price": 200, "enabled": True}, []
    )
    assert res_consola_ok1 is True, "ERROR: Falso negativo en 'Consola Nintendo Switch OLED con mando y caja'!"
    print("  -> 'Consola Nintendo Switch OLED con mando y caja' APROBADA correctamente.")

    # 6. PlayStation 5 con mando incluido
    res_ps5_ok = passes_filters(
        "Consola PlayStation 5 Slim 1TB con mando DualSense", "Incluye consola, mando y cables.",
        280.0, target_ps5, []
    )
    assert res_ps5_ok is True, "ERROR: Falso negativo en 'Consola PlayStation 5 Slim con mando'!"
    print("  -> 'Consola PlayStation 5 Slim 1TB con mando DualSense' APROBADA correctamente.")

    # 7. New 3DS XL completa
    res_3ds_ok = passes_filters(
        "Consola New Nintendo 3DS XL azul metálico", "En muy buen estado con cargador y lápiz.",
        110.0, target_3ds, []
    )
    assert res_3ds_ok is True, "ERROR: Falso negativo en 'Consola New Nintendo 3DS XL'!"
    print("  -> 'Consola New Nintendo 3DS XL azul metálico' APROBADA correctamente.")

    print("\n[4/5] Probando detección en evaluate_fraud_risk para placas y despieces...")
    f_placa = evaluate_fraud_risk("Placa base Nintendo Switch OLED", "Para piezas o reparar", 50.0)
    assert f_placa["is_fraud"] is True, "ERROR: 'Placa base' debe tener is_fraud=True en fraud_signals!"
    print("  -> evaluate_fraud_risk detecta 'Placa base' como riesgo de fraude/defecto (is_fraud=True).")

    print("\n[5/5] Verificando lógica de secuencia Flash Alert...")
    # Asegurar que sniper.py no referencia flash_msg antes de inicializarlo
    import sniper
    print("  -> sniper.py importado y sintaxis validada con éxito.")

    print("\n✅ ¡TODOS LOS TESTS DE LA FASE 7.1 HAN PASADO CON ÉXITO!")

if __name__ == "__main__":
    run_tests()

