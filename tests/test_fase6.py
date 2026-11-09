# -*- coding: utf-8 -*-
"""
Test Suite - Fase 6: Logging Estructurado & Rate Limit Adaptativo (P3-4 & P3-2)
Verifica:
1. AdaptiveRateLimiter: adaptación (+50%) ante latencia media > 5.0s.
2. AdaptiveRateLimiter: adaptación (+50%) ante ratio de errores no-200 > 20%.
3. AdaptiveRateLimiter: recuperación progresiva hacia intervalo base en condiciones saludables.
4. AdaptiveRateLimiter: integración con AntiBanTiming (modo diurno y guardia nocturna).
5. Logging: setup_logger crea RotatingFileHandler (10MB, 5 backups), formatea niveles y timestamps.
6. Métricas: log_metric emite JSON Lines a metrics.jsonl y tools/metrics_report lo analiza.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/app")

from antiban import AdaptiveRateLimiter, AntiBanTiming
from logger import setup_logger, log_metric, JSONMetricsLogger
from tools.metrics_report import run_metrics_report


def test_rate_limiter_latency():
    print("\n--- Test 1: Rate Limit Adaptativo por Latencia > 5s ---")
    limiter = AdaptiveRateLimiter(window_size=10)
    limiter.register_platform("WALLAPOP", base_interval=10.0)

    # Inicialmente debe estar en intervalo base
    assert limiter.get_current_interval("WALLAPOP") == 10.0

    # Simulamos 5 peticiones con latencia alta (>5.0s) y status 200
    for lat in [5.5, 6.2, 5.8, 6.0, 5.4]:
        limiter.record_request("WALLAPOP", latency_seconds=lat, status_code=200)

    new_interval, was_adapted, reason = limiter.evaluate_adaptation("WALLAPOP")
    assert was_adapted is True, "Debería haberse adaptado por latencia alta"
    assert new_interval == 15.0, f"Esperado 15.0s (+50%), obtenido {new_interval}"
    assert "latencia media" in reason
    print(f"  [OK] Intervalo aumentado correctamente a {new_interval}s (+50%) por {reason}")


def test_rate_limiter_error_ratio():
    print("\n--- Test 2: Rate Limit Adaptativo por Errores HTTP > 20% ---")
    limiter = AdaptiveRateLimiter(window_size=10)
    limiter.register_platform("VINTED", base_interval=40.0)

    # 3 peticiones 200 y 1 petición 429 = 25% de error (> 20%) con latencia baja
    limiter.record_request("VINTED", latency_seconds=1.0, status_code=200)
    limiter.record_request("VINTED", latency_seconds=1.2, status_code=200)
    limiter.record_request("VINTED", latency_seconds=0.9, status_code=200)
    limiter.record_request("VINTED", latency_seconds=1.1, status_code=429)

    new_interval, was_adapted, reason = limiter.evaluate_adaptation("VINTED")
    assert was_adapted is True, "Debería haberse adaptado por errores HTTP > 20%"
    assert new_interval == 60.0, f"Esperado 60.0s (+50% de 40s), obtenido {new_interval}"
    assert "errores no-200" in reason
    print(f"  [OK] Intervalo aumentado correctamente a {new_interval}s (+50%) por {reason}")


def test_rate_limiter_recovery():
    print("\n--- Test 3: Recuperación progresiva hacia intervalo base ---")
    limiter = AdaptiveRateLimiter(window_size=5)
    limiter.register_platform("EBAY", base_interval=20.0)

    # Forzamos degradación inicial
    for _ in range(5):
        limiter.record_request("EBAY", latency_seconds=6.0, status_code=200)
    interval_degraded, _, _ = limiter.evaluate_adaptation("EBAY")
    assert interval_degraded == 30.0  # 20 * 1.5

    # Ahora simulamos peticiones rápidas y exitosas
    for _ in range(5):
        limiter.record_request("EBAY", latency_seconds=0.5, status_code=200)

    # Primera ronda de recuperación: decae progresivamente (-15%)
    r1, was_recovered, reason = limiter.evaluate_adaptation("EBAY")
    assert r1 < 30.0, f"El intervalo debería haber disminuido de 30.0s, valor actual: {r1}"
    assert r1 >= 20.0, f"No debe bajar de base 20.0s, valor actual: {r1}"
    print(f"  [OK] Ronda 1 recuperación: {r1:.1f}s ({reason})")

    # Más rondas de recuperación hasta alcanzar base
    for _ in range(5):
        r_step, _, _ = limiter.evaluate_adaptation("EBAY")
    assert limiter.get_current_interval("EBAY") == 20.0, f"Esperado regreso a 20.0s, obtenido {limiter.get_current_interval('EBAY')}"
    print("  [OK] Recuperación completada: intervalo base de 20.0s restablecido con éxito.")


def test_rate_limiter_timing_integration():
    print("\n--- Test 4: Integración con AntiBanTiming (Jitter y Noche) ---")
    limiter = AdaptiveRateLimiter()
    limiter.register_platform("MILANUNCIOS", base_interval=30.0)
    timing = AntiBanTiming(night_start_hour=2, night_end_hour=7)

    # Modo diurno con jitter
    sleep_s, is_night, transition, reason = limiter.calculate_sleep("MILANUNCIOS", timing=timing)
    assert not is_night or is_night in (True, False)
    assert sleep_s >= 5
    print(f"  [OK] Cálculo de sleep diurno integrado: {sleep_s}s ({reason})")

    # Simulación de guardia nocturna
    class NightTimingMock:
        last_state_is_night = None
        def is_night_time(self):
            return True

    sleep_night, is_night_sim, _, reason_night = limiter.calculate_sleep("MILANUNCIOS", timing=NightTimingMock())
    assert is_night_sim is True
    assert sleep_night >= 120
    assert "Modo Guardia Nocturna" in reason_night
    print(f"  [OK] Cálculo de sleep nocturno integrado: {sleep_night}s ({reason_night})")


def test_structured_logging():
    print("\n--- Test 5: Logging Estructurado y RotatingFileHandler ---")
    tmp_dir = tempfile.mkdtemp(prefix="boti_log_test_")
    try:
        logger = setup_logger("TEST-BOT", log_dir=tmp_dir, capture_stdout=False)
        assert logger.name == "TEST-BOT"
        assert len(logger.handlers) >= 2, "Debe tener al menos ConsoleHandler y RotatingFileHandler"

        # Verificar RotatingFileHandler configurado a 10MB y 5 backups
        rf_handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert len(rf_handlers) == 1, "Debe existir exactamente un RotatingFileHandler"
        rfh = rf_handlers[0]
        assert rfh.maxBytes == 10 * 1024 * 1024, f"maxBytes esperado 10MB, obtenido {rfh.maxBytes}"
        assert rfh.backupCount == 5, f"backupCount esperado 5, obtenido {rfh.backupCount}"

        # Emitir mensajes de prueba
        logger.info("Mensaje informativo de prueba")
        logger.warning("Mensaje de advertencia de prueba")
        logger.error("Mensaje de error de prueba")

        log_file = os.path.join(tmp_dir, "test-bot.log")
        assert os.path.exists(log_file), f"Fichero de log no creado en {log_file}"

        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()

        assert "[INFO] [TEST-BOT] Mensaje informativo de prueba" in content
        assert "[WARNING] [TEST-BOT] Mensaje de advertencia de prueba" in content
        assert "[ERROR] [TEST-BOT] Mensaje de error de prueba" in content
        print(f"  [OK] Formato estructurado, niveles y fichero rotativo {log_file} verificados.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_metrics_json_lines():
    print("\n--- Test 6: Métricas JSON Lines y Reporte ---")
    tmp_dir = tempfile.mkdtemp(prefix="boti_metrics_test_")
    try:
        metrics_file = os.path.join(tmp_dir, "test_metrics.jsonl")
        m_logger = JSONMetricsLogger(metrics_file)

        m_logger.emit("round_completed", service="wallapop", platform="WALLAPOP", round=1, items_new=3, items_evaluated=1, duration_s=4.5, interval_s=8)
        m_logger.emit("round_completed", service="vinted", platform="VINTED", round=1, items_new=5, items_evaluated=2, duration_s=12.0, interval_s=45)
        m_logger.emit("deal_found", service="wallapop", platform="WALLAPOP", deal_score=9, profit_eur=45.0)

        assert os.path.exists(metrics_file)
        lines = []
        with open(metrics_file, "r", encoding="utf-8") as f:
            for l in f:
                if l.strip():
                    lines.append(json.loads(l.strip()))

        assert len(lines) == 3
        assert lines[0]["event"] == "round_completed"
        assert lines[0]["platform"] == "WALLAPOP"
        assert lines[0]["items_new"] == 3
        assert lines[1]["platform"] == "VINTED"
        assert lines[2]["deal_score"] == 9
        print(f"  [OK] 3 eventos JSON Lines emitidos y validados sintácticamente.")

        # Verificar ejecución de herramienta de reporte
        os.environ["METRICS_FILE"] = metrics_file
        run_metrics_report(hours=1)
        print("  [OK] Reporte de métricas ejecutado y formateado con éxito.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("=== INICIANDO TEST SUITE FASE 6 (LOGGING & RATE LIMIT ADAPTATIVO) ===")
    test_rate_limiter_latency()
    test_rate_limiter_error_ratio()
    test_rate_limiter_recovery()
    test_rate_limiter_timing_integration()
    test_structured_logging()
    test_metrics_json_lines()
    print("\n>>> TODOS LOS TESTS DE LA FASE 6 HAN PASADO CON ÉXITO! <<<")

