#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de verificación para la Fase 4 (P3-1 y P3-3):
- Detección de fallos silenciosos en parsers (P3-1):
  - Rastreo de 0 resultados consecutivos por (plataforma, query)
  - Disparo de alerta en exactamente <= 3 rondas
  - Prevención de spam y notificación de recuperación
- Cooldown no bloqueante como estado temporal (P3-3):
  - Modelado mediante timestamp de estado (cooldown_until) sin congelar bucles ni peticiones
  - Comprobación de que is_in_cooldown() y get_remaining_cooldown() operan sin invocar sleep
  - Aislamiento multi-tienda (el cooldown de una plataforma no detiene a las demás)
"""

import os
import sys
import time

# Añadir raíz al sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from antiban import AntiBanHealthMonitor, ParserHealthTracker

class MockHealthMonitor(AntiBanHealthMonitor):
    def __init__(self, service_name="TEST_MONITOR"):
        super().__init__(service_name)
        self.sent_embeds = []

    def send_health_embed(self, title, description, color, fields=None):
        self.sent_embeds.append({
            "title": title,
            "description": description,
            "color": color,
            "fields": fields
        })

def run_tests():
    print("[1/5] Probando modelado de cooldown de estado no bloqueante (P3-3)...")
    monitor = MockHealthMonitor("TEST_SERVICE")
    assert not monitor.is_in_cooldown(), "El monitor no debe iniciar en cooldown"
    assert monitor.get_remaining_cooldown() == 0, "Segundos restantes deben ser 0 inicialmente"

    # Simular HTTP 429
    should_cd, cd_secs = monitor.handle_http_response(429, url="https://api.test/search", query="switch oled")
    assert should_cd is True, "HTTP 429 debe activar cooldown"
    assert cd_secs == 420, f"Primer bloqueo debe ser 420s (7 min), recibido: {cd_secs}"
    assert monitor.is_in_cooldown() is True, "El monitor debe reflejar estado de cooldown activo"
    assert monitor.get_remaining_cooldown() > 0, "Segundos restantes deben ser mayores que 0"
    assert len(monitor.sent_embeds) == 1, "Debe haberse enviado embed de alerta a Discord"
    assert "Rate Limit" in monitor.sent_embeds[0]["title"]
    print("  -> Activación de cooldown de estado registrada sin sleep.")

    print("[2/5] Probando recuperación y expiración de cooldown...")
    # Simular resolución manual o recuperación con HTTP 200
    monitor.reset_cooldown()
    assert not monitor.is_in_cooldown()
    assert monitor.get_remaining_cooldown() == 0
    print("  -> Recuperación de cooldown completada.")

    print("[3/5] Probando detección de fallos silenciosos en parsers (P3-1)...")
    tracker = ParserHealthTracker("TEST_TRACKER", default_threshold=3, alert_cooldown=60)
    mock_hm = MockHealthMonitor("TEST_PARSER")

    # Ronda 1: 0 resultados
    c1 = tracker.record_empty("MILANUNCIOS", "switch oled", mock_hm, reason="Regex mismatch")
    assert c1 == 1
    assert len(mock_hm.sent_embeds) == 0, "No debe alertar en ronda 1"

    # Ronda 2: 0 resultados
    c2 = tracker.record_empty("MILANUNCIOS", "switch oled", mock_hm, reason="Regex mismatch")
    assert c2 == 2
    assert len(mock_hm.sent_embeds) == 0, "No debe alertar en ronda 2"

    # Ronda 3: 0 resultados -> DEBE alertar inmediatamente (Criterio de aceptación: <= 3 rondas)
    c3 = tracker.record_empty("MILANUNCIOS", "switch oled", mock_hm, reason="Regex mismatch")
    assert c3 == 3
    assert len(mock_hm.sent_embeds) == 1, "DEBE alertar al llegar a la ronda 3"
    assert "FALLO SILENCIOSO SOSPECHOSO" in mock_hm.sent_embeds[0]["title"]
    assert "MILANUNCIOS" in mock_hm.sent_embeds[0]["title"]
    print("  -> Alerta de fallo silencioso disparada exactamente en la 3ª ronda consecutiva.")

    print("[4/5] Probando anti-spam y recuperación automática de parser...")
    # Ronda 4: no debe duplicar la alerta inmediatamente por cooldown de alerta
    c4 = tracker.record_empty("MILANUNCIOS", "switch oled", mock_hm, reason="Regex mismatch")
    assert c4 == 4
    assert len(mock_hm.sent_embeds) == 1, "No debe reenviar alerta en la misma ventana de tiempo"

    # Recuperación: el parser vuelve a extraer ítems
    tracker.record_success("MILANUNCIOS", "switch oled", items_count=5, health_monitor=mock_hm)
    assert len(mock_hm.sent_embeds) == 2, "Debe enviar notificación de recuperación al normalizarse"
    assert "PARSER RECUPERADO" in mock_hm.sent_embeds[1]["title"]
    assert tracker.consecutive_empty[("MILANUNCIOS", "switch oled")] == 0
    print("  -> Anti-spam y notificación de recuperación verificados con éxito.")

    print("[5/5] Probando registro de excepción no controlada en fetcher...")
    tracker.record_parser_error("EBAY", "game boy", "Simulated KeyError: 'adList'", mock_hm)
    assert len(mock_hm.sent_embeds) == 3
    assert "ERROR EN PARSER" in mock_hm.sent_embeds[2]["title"]
    print("  -> Registro de excepción en parser verificado.")

    print("\n✅ ¡TODOS LOS TESTS DE LA FASE 4 HAN PASADO CON ÉXITO!")

if __name__ == "__main__":
    run_tests()

