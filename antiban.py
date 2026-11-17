# -*- coding: utf-8 -*-
"""
Módulo de Resiliencia, Antiban y Telemetría de Salud para Boti Sniper v4.0.
Incluye:
- Rotación coherente 1:1 de perfiles de navegador (User-Agents + Client Hints + TLS Impersonation real).
- Perfiles Safari TLS dedicados para evasión limpia de Cloudflare / Akamai (TodoColección, eBay, CeX).
- Priorización Inteligente por Tiers (Hot vs Standard) para reducir latencia a la mitad.
- Jitter orgánico realista y Modo Guardia Nocturna (02:00 - 07:00 Madrid).
- Sistema de Cooldown y Backoff Inteligente ante HTTP 403 / 429 con alertas en #incidencias.
"""

import os
import json
import heapq
import math
import queue
import random
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from curl_cffi import requests
from text_rules import redact_secrets as _redact

HEALTH_WEBHOOK_URL = os.environ.get(
    "DISCORD_HEALTH_WEBHOOK_URL",
    os.environ.get("DISCORD_WEBHOOK_URL", "")
).strip()

# ==================== POOL DE PERFILES DE NAVEGADOR (100% COHERENTES CON JA3/TLS) ====================
BROWSER_PROFILES = [
    {
        "name": "Chrome 152 (Win 11 x64)",
        "impersonate": "chrome152",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not A(Brand";v="8", "Chromium";v="152", "Google Chrome";v="152"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0"
    },
    {
        "name": "Chrome 152 (macOS Sequoia)",
        "impersonate": "chrome152_macos",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not A(Brand";v="8", "Chromium";v="152", "Google Chrome";v="152"',
        "sec_ch_ua_platform": '"macOS"',
        "sec_ch_ua_mobile": "?0"
    },
    {
        "name": "Safari 26.0 (macOS)",
        "impersonate": "safari260",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",
        "sec_ch_ua": None,
        "sec_ch_ua_platform": None,
        "sec_ch_ua_mobile": None
    }
]


class BrowserProfileRotator:
    """Gestiona la rotación de identidades de navegador sin cambiar bruscamente en cada petición."""
    def __init__(self, rotate_every_rounds=10):
        self.rotate_every = rotate_every_rounds
        self.rounds_count = 0
        self.rotation_epoch = 0
        self.current_idx = random.randint(0, len(BROWSER_PROFILES) - 1)
        self.current_profile = BROWSER_PROFILES[self.current_idx]

    def get_profile(self):
        return self.current_profile

    def get_chrome_profile(self):
        if "chrome" in self.current_profile["impersonate"]:
            return self.current_profile
        return BROWSER_PROFILES[0]

    @staticmethod
    def get_safari_impersonation():
        return "safari260"

    def step_round(self):
        self.rounds_count += 1
        if self.rounds_count >= self.rotate_every:
            self.rotate()
            return True
        return False

    def rotate(self):
        self.rounds_count = 0
        self.rotation_epoch += 1
        new_idx = (self.current_idx + random.randint(1, len(BROWSER_PROFILES) - 1)) % len(BROWSER_PROFILES)
        self.current_idx = new_idx
        self.current_profile = BROWSER_PROFILES[new_idx]
        return self.current_profile


# ==================== CONTROL DE JITTER, TIERS Y HORARIO NOCTURNO ====================

class AntiBanTiming:
    """Calcula pausas orgánicas, filtra rondas por prioridad (Hot/Standard) y gestiona el enfriamiento nocturno."""
    def __init__(self, night_start_hour=2, night_end_hour=7):
        self.night_start = night_start_hour
        self.night_end = night_end_hour
        self.last_state_is_night = None

    def is_night_time(self):
        current_hour = datetime.now().hour
        return self.night_start <= current_hour < self.night_end

    @staticmethod
    def should_scan_target(target, round_number, warmup=False):
        if not target.get("enabled", True):
            return False
        if warmup:
            return True
        tier = str(target.get("tier", "hot")).lower()
        if tier == "hot":
            return True
        # Los nichos 'standard' se escanean en rondas pares e impares alternadas según su hash
        target_hash = sum(ord(c) for c in target.get("id", target.get("name", "")))
        return (round_number % 2) == (target_hash % 2)

    def calculate_round_sleep(self, base_interval, engine_name="SNIPER", rate_limiter=None):
        rl = rate_limiter or default_rate_limiter
        if rl:
            return rl.calculate_sleep(engine_name, timing=self, base_interval=base_interval)

        is_night = self.is_night_time()
        transition = None

        if self.last_state_is_night is not None and self.last_state_is_night != is_night:
            transition = "ENTER_NIGHT" if is_night else "ENTER_DAY"
        self.last_state_is_night = is_night

        if is_night:
            base_night = 270  # 4.5 minutos
            jitter = random.randint(-30, 60)
            sleep_secs = max(160, base_night + jitter)
            reason = "Modo Guardia Nocturna (02:00-07:00) | Enfriamiento de IP"
        else:
            jitter_min = -int(base_interval * 0.18)
            jitter_max = int(base_interval * 0.28)
            jitter = random.randint(jitter_min, jitter_max)
            sleep_secs = max(5, base_interval + jitter)
            reason = f"Modo Diurno Turbo (Intervalo {base_interval}s con jitter {jitter:+d}s)"

        return sleep_secs, is_night, transition, reason

    @staticmethod
    def inter_query_sleep(min_s=0.3, max_s=0.6):
        time.sleep(random.uniform(min_s, max_s))

    @staticmethod
    def item_processing_sleep():
        time.sleep(random.uniform(0.7, 1.5))


# ==================== CONTROLADOR DE RATE LIMIT ADAPTATIVO (P3-2) ====================

class AdaptiveRateLimiter:
    """
    Controlador de Tasa Adaptativo por Plataforma (P3-2).
    - Monitorea una ventana deslizante de solicitudes (latencia y códigos de respuesta HTTP).
    - Si latencia media > 5.0s O ratio de respuestas no-200 > 20%: +50% al intervalo de escaneo.
    - Cuando las condiciones se normalizan (latencia <= 5.0s y no-200 <= 20%): vuelta progresiva al intervalo base.
    """
    def __init__(self, window_size=20):
        self._lock = threading.Lock()
        self.window_size = window_size
        self._platforms = {}

    def register_platform(self, platform, base_interval):
        with self._lock:
            self._register_platform_unlocked(platform, base_interval)

    def _register_platform_unlocked(self, platform, base_interval):
        plat = platform.upper()
        if plat not in self._platforms:
            self._platforms[plat] = {
                "base": float(base_interval),
                "current": float(base_interval),
                "latencies": deque(maxlen=self.window_size),
                "statuses": deque(maxlen=self.window_size),
                "healthy_rounds": 0,
                "last_reason": "Intervalo base inicial"
            }
        else:
            self._platforms[plat]["base"] = float(base_interval)

    def reset_window(self, platform):
        """Vacía la ventana de latencias/errores de una plataforma (p. ej. al salir de un cooldown),
        para que errores de antes de la pausa no sigan penalizando su intervalo (A-005)."""
        with self._lock:
            plat = platform.upper()
            pdata = self._platforms.get(plat)
            if pdata:
                pdata["latencies"].clear()
                pdata["statuses"].clear()
                pdata["current"] = pdata["base"]
                pdata["healthy_rounds"] = 0
                pdata["last_reason"] = "Ventana reiniciada tras cooldown"

    def record_request(self, platform, latency_seconds, status_code=200):
        with self._lock:
            plat = platform.upper()
            if plat not in self._platforms:
                self._register_platform_unlocked(plat, 30.0)
            pdata = self._platforms[plat]
            pdata["latencies"].append(float(latency_seconds))
            pdata["statuses"].append(int(status_code))

    def evaluate_adaptation(self, platform):
        with self._lock:
            return self._evaluate_adaptation_unlocked(platform)

    def _evaluate_adaptation_unlocked(self, platform):
        plat = platform.upper()
        if plat not in self._platforms:
            return 30.0, False, "Plataforma no registrada"

        pdata = self._platforms[plat]
        latencies = pdata["latencies"]
        statuses = pdata["statuses"]
        base = pdata["base"]
        current = pdata["current"]
        max_interval = base * 4.0

        if len(latencies) < 3:
            return current, False, pdata["last_reason"]

        avg_lat = sum(latencies) / len(latencies)
        non_200_count = sum(1 for s in statuses if s != 200)
        error_ratio = non_200_count / len(statuses)

        is_degraded = (avg_lat > 5.0) or (error_ratio > 0.20)

        if is_degraded:
            pdata["healthy_rounds"] = 0
            new_interval = min(max_interval, current * 1.5)
            reasons = []
            if avg_lat > 5.0:
                reasons.append(f"latencia media {avg_lat:.2f}s > 5s")
            if error_ratio > 0.20:
                reasons.append(f"errores no-200 {error_ratio*100:.0f}% > 20%")
            reason_str = f"Aumento +50% ({current:.1f}s -> {new_interval:.1f}s) por: {', '.join(reasons)}"
            pdata["current"] = new_interval
            pdata["last_reason"] = reason_str
            return new_interval, True, reason_str
        else:
            pdata["healthy_rounds"] += 1
            if current > base:
                new_interval = max(base, current * 0.85)
                reason_str = f"Recuperación progresiva ({current:.1f}s -> {new_interval:.1f}s | base: {base:.0f}s)"
                pdata["current"] = new_interval
                pdata["last_reason"] = reason_str
                return new_interval, True, reason_str
            else:
                pdata["current"] = base
                pdata["last_reason"] = f"Intervalo base normal ({base:.0f}s)"
                return base, False, pdata["last_reason"]

    def calculate_sleep(self, platform, timing=None, base_interval=None):
        with self._lock:
            plat = platform.upper()
            if base_interval is not None:
                if plat not in self._platforms:
                    self._register_platform_unlocked(plat, base_interval)
                else:
                    self._platforms[plat]["base"] = float(base_interval)
            elif plat not in self._platforms:
                self._register_platform_unlocked(plat, 10.0)

            adapted_interval, was_adapted, adapt_reason = self._evaluate_adaptation_unlocked(plat)
            base_p = self._platforms[plat]["base"]

        is_night = False
        transition = None
        if timing and hasattr(timing, "is_night_time"):
            is_night = timing.is_night_time()
            if hasattr(timing, "last_state_is_night") and timing.last_state_is_night is not None and timing.last_state_is_night != is_night:
                transition = "ENTER_NIGHT" if is_night else "ENTER_DAY"
            if hasattr(timing, "last_state_is_night"):
                timing.last_state_is_night = is_night

        if is_night:
            base_night = max(240, int(adapted_interval * 2.0))
            jitter = random.randint(-20, 40)
            sleep_secs = max(120, base_night + jitter)
            reason = f"Modo Guardia Nocturna ({sleep_secs}s)"
        else:
            jitter_min = -int(adapted_interval * 0.15)
            jitter_max = int(adapted_interval * 0.25)
            jitter = random.randint(jitter_min, jitter_max)
            sleep_secs = max(5, int(adapted_interval + jitter))
            tag = " [ADAPTADO +50%]" if was_adapted and adapted_interval > base_p else ""
            reason = f"Modo Diurno{tag} ({sleep_secs}s | base: {base_p:.0f}s | {adapt_reason})"

        return sleep_secs, is_night, transition, reason

    def get_current_interval(self, platform):
        with self._lock:
            plat = platform.upper()
            if plat in self._platforms:
                return self._platforms[plat]["current"]
            return 10.0

    def reset(self, platform=None):
        with self._lock:
            if platform:
                plat = platform.upper()
                if plat in self._platforms:
                    base = self._platforms[plat]["base"]
                    self._register_platform_unlocked(plat, base)
            else:
                self._platforms.clear()


default_rate_limiter = AdaptiveRateLimiter()


# ==================== GESTOR DE COOLDOWN Y ALERTAS DISCORD ====================

class HealthNotifier(threading.Thread):
    """Despacha avisos fuera del escáner, con cola acotada y deduplicación de diez minutos."""

    def __init__(self, webhook_url, *, start_worker=True):
        super().__init__(name="HealthNotifier", daemon=True)
        self.webhook_url = webhook_url
        self._queue = queue.Queue(maxsize=100)
        self._lock = threading.Lock()
        self._clock = time.monotonic
        self._last_title = {}
        if webhook_url and start_worker:
            self.start()

    def enqueue(self, embed):
        if not self.webhook_url:
            return False
        with self._lock:
            now = self._clock()
            self._last_title = {title: sent for title, sent in self._last_title.items() if now - sent < 600}
            title = embed["title"]
            if title in self._last_title:
                return False
            try:
                self._queue.put_nowait(embed)
            except queue.Full:
                return False
            self._last_title[title] = now
        return True

    def run(self):
        while True:
            embed = self._queue.get()
            try:
                if embed is None:
                    return
                # Import tardío: core_engine también importa antiban.
                from core_engine import _discord_send
                _discord_send("POST", self.webhook_url, json_payload={"embeds": [embed]})
            except Exception as e:
                print(f"[HEALTH MONITOR ERROR] Fallo enviando a Discord: {_redact(e)}")
            finally:
                self._queue.task_done()


class AntiBanHealthMonitor:
    """Monitoriza bloqueos (403, 429), aplica cooldown preventivo y notifica a Discord (#incidencias)."""
    def __init__(self, service_name):
        self._lock = threading.Lock()
        self.service_name = service_name
        self.consecutive_blocks = 0
        self.ok_streak_since_block = 0
        self.last_block_time = 0
        self.in_cooldown = False
        self.cooldown_until = 0
        self.last_ai_alert = 0
        self._notifier = HealthNotifier(HEALTH_WEBHOOK_URL) if HEALTH_WEBHOOK_URL else None

    def send_health_embed(self, title, description, color, fields=None):
        if self._notifier is None:
            return
        footer_text = f"Boti Health Monitor v4.1 • {self.service_name} • {datetime.now().strftime('%H:%M:%S')}".replace("\n", " ").replace("\r", " ").strip()
        embed = {
            "title": str(title).strip(),
            "description": str(description).strip(),
            "color": color,
            "footer": {"text": footer_text}
        }
        if fields:
            embed["fields"] = fields
        self._notifier.enqueue(embed)

    @staticmethod
    def _parse_retry_after(value, cap=6 * 3600):
        """Segundos de una cabecera Retry-After numérica; None si falta, es una fecha o no es válida."""
        try:
            secs = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return min(secs, cap) if secs > 0 else None

    def handle_http_response(self, status_code, url="", query="", retry_after=None):
        notification = None
        result = (False, 0)
        with self._lock:
            if status_code in (429, 403, 401):
                self.consecutive_blocks += 1
                self.ok_streak_since_block = 0
                self.in_cooldown = True
                self.last_block_time = time.time()

                if self.consecutive_blocks == 1:
                    cooldown_secs = 420   # 7 minutos
                elif self.consecutive_blocks == 2:
                    cooldown_secs = 900   # 15 minutos
                else:
                    cooldown_secs = 1800  # 30 minutos

                # Retry-After en segundos: solo puede alargar el cooldown, nunca acortarlo.
                ra = self._parse_retry_after(retry_after)
                if ra:
                    cooldown_secs = max(cooldown_secs, ra)

                self.cooldown_until = max(self.cooldown_until, time.time() + cooldown_secs)
                mins = cooldown_secs // 60
                block_type = "Rate Limit (HTTP 429)" if status_code == 429 else f"Acceso Denegado / WAF (HTTP {status_code})"

                title = f"🚨 [ALERTA ANTIBAN] {self.service_name}: {block_type}"
                desc = (
                    f"Se ha detectado una restricción temporal en la plataforma al consultar `{query}`.\n\n"
                    f"🛡️ **Medida preventiva de protección activada (P3-3 no bloqueante):**\n"
                    f"• **Cooldown registrado:** Se pausan peticiones a este origen durante **{mins} minutos**.\n"
                    f"• **El proceso permanece activo:** Continúa respondiendo al healthcheck y sirviendo otras tiendas/radares.\n"
                    f"• **Rotación automática:** Nueva huella TLS/JA3 y renovación de cookies al reanudar.\n"
                    f"• **Reincidencia acumulada:** `{self.consecutive_blocks}`"
                )
                notification = (title, desc, 0xE74C3C)
                result = (True, cooldown_secs)

            elif status_code == 200:
                recovered_from_cooldown = False
                if self.in_cooldown or (self.cooldown_until > 0 and time.time() >= self.cooldown_until):
                    self.in_cooldown = False
                    self.cooldown_until = 0
                    recovered_from_cooldown = True

                if self.consecutive_blocks > 0:
                    self.ok_streak_since_block += 1
                    if self.ok_streak_since_block >= 10:
                        self.consecutive_blocks = 0
                        self.ok_streak_since_block = 0

                if recovered_from_cooldown:
                    title = f"✅ [RECUPERADO] {self.service_name}: Conexión Normalizada"
                    desc = (
                        "La plataforma vuelve a responder correctamente con código **HTTP 200 OK** tras el enfriamiento.\n"
                        "El escaneo continúa a ritmo normal."
                    )
                    notification = (title, desc, 0x2ECC71)

        if notification:
            self.send_health_embed(*notification)
        return result

    def is_in_cooldown(self):
        """Comprueba si el monitor se encuentra en período de cooldown activo (P3-3)."""
        with self._lock:
            now = time.time()
            if self.cooldown_until > now:
                return True
            if self.in_cooldown and now >= self.cooldown_until:
                self.in_cooldown = False
            return False

    def get_remaining_cooldown(self):
        """Retorna los segundos restantes del cooldown de estado."""
        with self._lock:
            return max(0, int(self.cooldown_until - time.time()))

    def reset_cooldown(self):
        """Fuerza la finalización del cooldown."""
        with self._lock:
            self.cooldown_until = 0
            self.in_cooldown = False

    def notify_ai_error(self, status_code, response_text):
        with self._lock:
            now = time.time()
            if now - self.last_ai_alert < 1800:
                return
            self.last_ai_alert = now
        title = f"⚠️ [ALERTA MOTOR IA] {self.service_name}: HTTP {status_code}"
        desc = f"Respuesta del proveedor IA: `{str(response_text)[:300]}`"
        self.send_health_embed(title, desc, 0xF1C40F)

    def notify_transition(self, transition_type):
        if transition_type == "ENTER_NIGHT":
            title = f"🌙 [MODO NOCTURNO] {self.service_name} en Enfriamiento"
            desc = (
                "Horario nocturno detectado (02:00 a 07:00 Madrid).\n"
                "• **Frecuencia reducida:** Escaneo cada **~4.5 minutos**.\n"
                "• **Beneficio:** Protección residencial de IP sin perder chollos de madrugada."
            )
            self.send_health_embed(title, desc, 0x34495E)
        elif transition_type == "ENTER_DAY":
            title = f"☀️ [MODO DIURNO TURBO] {self.service_name} a Pleno Rendimiento"
            desc = (
                "Horario diurno activado (07:00 Madrid).\n"
                "• **Frecuencia reactivada:** Escaneo rápido con priorización Hot-Tier activa."
            )
            self.send_health_embed(title, desc, 0xF39C12)


# ==================== DETECCIÓN DE FALLOS SILENCIOSOS EN PARSERS (P3-1) ====================

class ParserHealthTracker:
    """
    Monitoriza la salud de los fetchers y parsers (P3-1).
    Rastrea rondas consecutivas con 0 resultados por (plataforma, query).
    A las N rondas consecutivas (por defecto N=3), alerta al webhook de salud
    identificando el parser sospechoso para evitar pérdidas silenciosas de mercado.
    """
    def __init__(self, service_name="", default_threshold=3, alert_cooldown=3600):
        self._lock = threading.Lock()
        self.service_name = service_name
        self.default_threshold = default_threshold
        self.alert_cooldown = alert_cooldown
        self.consecutive_empty = {}    # (plat, query) -> count
        self.last_alert_time = {}      # (plat, query) -> timestamp
        self.alerted = {}              # (plat, query) -> bool

        # Monitorización agregada a nivel de plataforma (Anti-Spam / P3-1 Regulado)
        self.platform_empty_rounds = {}     # plat -> count
        self.platform_last_alert_time = {}  # plat -> timestamp
        self.platform_alerted = {}          # plat -> bool
        self.platform_alert_cooldown = alert_cooldown

    def record_success(self, platform, query, items_count=1, health_monitor=None):
        """Llamado cuando un fetcher obtiene 1 o más ítems correctamente."""
        with self._lock:
            plat = platform.upper()
            key = (plat, str(query).strip().lower())
            was_alerted = self.alerted.get(key, False)
            self.consecutive_empty[key] = 0
            self.alerted[key] = False
            self.platform_empty_rounds[plat] = 0

        if was_alerted and health_monitor:
            title = f"✅ [PARSER RECUPERADO] {platform}: Búsqueda normalizada"
            desc = (
                f"El parser de **{platform}** vuelve a obtener resultados válidos "
                f"(`{items_count}` ítems) para la búsqueda `{query}`."
            )
            health_monitor.send_health_embed(title, desc, 0x2ECC71)

    def record_platform_success(self, platform, total_items=1, health_monitor=None):
        """Llamado cuando la plataforma en su conjunto ha obtenido anuncios válidos en la ronda."""
        with self._lock:
            plat = platform.upper()
            was_alerted = self.platform_alerted.get(plat, False)
            self.platform_empty_rounds[plat] = 0
            self.platform_alerted[plat] = False

        if was_alerted and health_monitor:
            title = f"✅ [PLATAFORMA RECUPERADA] {plat}: Extracción normalizada"
            desc = (
                f"El parser de **{plat}** vuelve a obtener resultados válidos en la ronda "
                f"(`{total_items}` anuncios extraídos en total)."
            )
            health_monitor.send_health_embed(title, desc, 0x2ECC71)

    def record_platform_empty(self, platform, health_monitor=None, threshold=6, reason="0 anuncios en toda la ronda"):
        """Llamado cuando NINGÚN anuncio fue extraído en toda la ronda para esta plataforma (fallo general real)."""
        should_alert = False
        with self._lock:
            plat = platform.upper()
            cnt = self.platform_empty_rounds.get(plat, 0) + 1
            self.platform_empty_rounds[plat] = cnt

            if cnt >= threshold:
                now = time.time()
                last_alert = self.platform_last_alert_time.get(plat, 0)
                if (now - last_alert) >= self.platform_alert_cooldown:
                    self.platform_last_alert_time[plat] = now
                    self.platform_alerted[plat] = True
                    should_alert = True

        if should_alert and health_monitor:
            title = f"🚨 [FALLO GENERAL EN PLATAFORMA] {plat}: 0 anuncios en {cnt} rondas consecutivas"
            desc = (
                f"El parser de **{plat}** no ha podido extraer **ningún anuncio en toda la tienda** "
                f"durante **{cnt} rondas consecutivas**.\n\n"
                f"🔍 **Diagnóstico:**\n"
                f"• Posible cambio estructural completo de selectores o bloqueo general de IP.\n"
                f"• Detalle reportado: `{reason}`"
            )
            health_monitor.send_health_embed(title, desc, 0xE74C3C)
        return cnt

    def record_empty(self, platform, query, health_monitor=None, threshold=None, reason="Cero ítems extraídos del contenido"):
        """Llamado cuando una búsqueda finaliza en 0 resultados."""
        should_alert = False
        with self._lock:
            plat = platform.upper()
            key = (plat, str(query).strip().lower())
            th = threshold or self.default_threshold
            current = self.consecutive_empty.get(key, 0) + 1
            self.consecutive_empty[key] = current

            if current >= th:
                now = time.time()
                last_alert = self.last_alert_time.get(key, 0)
                last_plat_alert = self.platform_last_alert_time.get(plat, 0)
                effective_plat_cd = min(self.alert_cooldown, self.platform_alert_cooldown)

                # Filtro anti-flood: evitar ráfagas masivas de alertas para la misma plataforma
                if (now - last_alert) >= self.alert_cooldown and (now - last_plat_alert) >= effective_plat_cd:
                    self.last_alert_time[key] = now
                    self.platform_last_alert_time[plat] = now
                    self.alerted[key] = True
                    should_alert = True

        if should_alert and health_monitor:
            title = f"⚠️ [FALLO SILENCIOSO SOSPECHOSO] {platform}: Parser sin resultados ({current} rondas)"
            desc = (
                f"El parser de **{platform}** ha devuelto **0 resultados** durante **{current} rondas consecutivas** "
                f"al buscar `{query}`.\n\n"
                f"🔍 **Posible causa:**\n"
                f"• Cambio en la estructura HTML/JSON de la plataforma o selectores desactualizados.\n"
                f"• Detalle reportado: `{reason}`\n"
                f"• Comprueba si la plataforma sigue mostrando anuncios para esta búsqueda."
            )
            health_monitor.send_health_embed(title, desc, 0xE67E22)
        return current

    def record_parser_error(self, platform, query, error_msg, health_monitor=None):
        """Llamado cuando un fetcher lanza una excepción no controlada."""
        should_alert = False
        with self._lock:
            key = (platform.upper(), str(query).strip().lower())
            now = time.time()
            last_alert = self.last_alert_time.get(key, 0)
            if (now - last_alert) >= self.alert_cooldown:
                self.last_alert_time[key] = now
                should_alert = True

        if should_alert and health_monitor:
            title = f"💥 [ERROR EN PARSER] {platform}: Excepción al consultar"
            desc = (
                f"Se ha producido un error inesperado en el fetcher de **{platform}** para `{query}`:\n\n"
                f"```text\n{str(error_msg)[:350]}\n```\n"
                f"Es necesario revisar el código del fetcher."
            )
            health_monitor.send_health_embed(title, desc, 0xE74C3C)

default_parser_tracker = ParserHealthTracker("DEFAULT")


# ==================== TOKEN BUCKET Y PLANIFICADOR POR CONSULTA (T2.3) ====================

class TokenBucket:
    """Limitador de tasa thread-safe con penalización por bloqueo y recuperación gradual.

    `acquire(priority=True)` (fichas y perfiles de un anuncio recién detectado) no espera: toma el
    token a crédito, hasta `max_debt`, y la deuda la pagan las siguientes búsquedas normales. La tasa
    media no cambia; solo se evita que un chollo espere detrás de la cola de sondeos.
    """

    def __init__(self, rate_per_s: float, burst: int = 2, *, clock=time.monotonic, sleeper=time.sleep,
                 max_debt: int = 3):
        self._lock = threading.Lock()
        self.base = max(0.01, float(rate_per_s))
        self.base_rate = self.base
        self.rate = self.base
        self.burst = max(1, int(burst))
        self.max_debt = max(0, int(max_debt))
        self._clock = clock
        self._sleeper = sleeper
        self._tokens = float(self.burst)
        self._last = float(self._clock())
        self._success_streak = 0
        self._recent = deque()
        self.priority_grants = 0
        self.grants = 0  # tokens normales concedidos (búsquedas y páginas extra)

    def set_base_rate(self, rate_per_s: float):
        with self._lock:
            new_base = max(0.01, float(rate_per_s))
            if abs(new_base - self.base) > 1e-9:
                ratio = self.rate / self.base if self.base > 0 else 1.0
                self.base = new_base
                self.base_rate = new_base
                self.rate = max(new_base * 0.25, min(new_base, new_base * ratio))

    def acquire(self, priority: bool = False) -> float:
        """Bloquea hasta disponer de 1 token; devuelve los segundos esperados.

        Con `priority=True` concede el token al instante mientras la deuda no supere `max_debt`
        (el saldo puede quedar negativo); agotado el crédito, se comporta como una petición normal.
        """
        with self._lock:
            now = float(self._clock())
            if now > self._last:
                elapsed = now - self._last
                self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
                self._last = now

            if priority and self.max_debt > 0 and (self._tokens - 1.0) >= -float(self.max_debt):
                self._tokens -= 1.0
                # Cuenta en la ventana de 60 s (retrasa a las normales siguientes) sin desordenarla.
                self._recent.append(max(now, self._recent[-1]) if self._recent else now)
                self.priority_grants += 1
                return 0.0

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                token_ready = self._last
            else:
                needed = 1.0 - self._tokens
                wait_token = needed / self.rate
                self._tokens = 0.0
                token_ready = self._last + wait_token
                self._last = token_ready

            # Garantizar que en ninguna ventana deslizante de 60 s se supere round(rate * 60)
            max_per_min = max(1, int(round(self.rate * 60.0)))
            while self._recent and (token_ready - self._recent[0]) >= 60.0:
                self._recent.popleft()
            if len(self._recent) >= max_per_min:
                min_allowed = self._recent[0] + 60.0
                if min_allowed > token_ready:
                    token_ready = min_allowed
                    self._last = max(self._last, token_ready)
                while self._recent and (token_ready - self._recent[0]) >= 60.0:
                    self._recent.popleft()

            self._recent.append(token_ready)
            self.grants += 1
            wait_s = max(0.0, token_ready - now)

        if wait_s > 0.0:
            self._sleeper(wait_s)
        return wait_s

    def penalize(self, factor: float = 0.5) -> float:
        """Tras un 403/429: rate = max(rate * factor, base * 0.25)."""
        with self._lock:
            self._success_streak = 0
            self.rate = max(self.rate * float(factor), self.base * 0.25)
            return self.rate

    def recover(self, immediate: bool = False) -> float:
        """Cada 50 éxitos seguidos: rate = min(base, rate * 1.1)."""
        with self._lock:
            if immediate:
                self._success_streak = 0
                self.rate = min(self.base, self.rate * 1.1)
                return self.rate
            self._success_streak += 1
            if self._success_streak >= 50:
                self._success_streak = 0
                self.rate = min(self.base, self.rate * 1.1)
            return self.rate


def acquire_item_token(bucket):
    """Token para una petición de ficha o perfil de un anuncio recién detectado.

    Es prioritario: no espera detrás de la cola de búsquedas (la deuda la pagan los sondeos
    siguientes, así que la tasa media hacia la plataforma no cambia). Un cubo sin soporte de
    prioridad recibe la petición normal. Devuelve los segundos esperados.
    """
    if bucket is None:
        return 0.0
    try:
        return bucket.acquire(priority=True)
    except TypeError:
        return bucket.acquire()


@dataclass(order=True)
class QueryTask:
    next_due: float
    radar_id: str = field(default="", compare=False)
    query: str = field(default="", compare=False)
    min_price: float = field(default=0.0, compare=False)
    max_price: float = field(default=9999.0, compare=False)
    interval_s: float = field(default=20.0, compare=False)  # cadencia deseada (config)
    tier: str = field(default="hot", compare=False)
    target: dict = field(default_factory=dict, compare=False, repr=False)
    eff_interval_s: float = field(default=0.0, compare=False)  # cadencia real tras ajustar a la capacidad
    yield_rate: float = field(default=0.0, compare=False)      # candidatos/s (media exponencial)
    last_done: float = field(default=0.0, compare=False)
    polls: int = field(default=0, compare=False)
    cost: float = field(default=1.0, compare=False)            # tokens normales por sondeo (media exponencial)
    grants_mark: int = field(default=-1, compare=False, repr=False)

    @property
    def effective_interval(self) -> float:
        return float(self.eff_interval_s) if self.eff_interval_s and self.eff_interval_s > 0 else float(self.interval_s)


ADAPTIVE_DEFAULTS = {
    "enabled": False,
    "min_factor": 0.5,       # la consulta más productiva baja como mucho a la mitad de su cadencia
    "max_factor": 1.6,       # la menos productiva se alarga como mucho un 60 %
    "tau_s": 10800.0,        # memoria de la media exponencial de candidatos (3 h)
    "prior_per_hour": 0.5,   # suelo: una consulta sin candidatos recientes no se queda sin sondear
    "alert_bonus": 6.0,      # una alerta enviada pesa como 1 + 6 candidatos (acerca el reparto a los chollos reales)
    "recompute_s": 120.0,
}


def parse_adaptive_config(sched_cfg: dict, plat_cfg: dict = None) -> dict:
    """Fusiona ADAPTIVE_DEFAULTS <- scheduling.adaptive <- scheduling.<plataforma>.adaptive."""
    out = dict(ADAPTIVE_DEFAULTS)
    for src in ((sched_cfg or {}).get("adaptive"), (plat_cfg or {}).get("adaptive")):
        if isinstance(src, dict):
            for k, v in src.items():
                if k not in out:
                    continue
                try:
                    out[k] = bool(v) if k == "enabled" else float(v)
                except (TypeError, ValueError):
                    pass
    out["min_factor"] = min(1.0, max(0.1, out["min_factor"]))
    out["max_factor"] = max(1.0, min(10.0, out["max_factor"]))
    out["tau_s"] = max(300.0, out["tau_s"])
    out["prior_per_hour"] = max(1e-6, out["prior_per_hour"])
    out["alert_bonus"] = max(0.0, min(50.0, out["alert_bonus"]))
    out["recompute_s"] = max(10.0, out["recompute_s"])
    return out


def balanced_multipliers(base_freqs, weights, budget, lo, hi):
    """Multiplicadores de frecuencia g_i = clamp(c * w_i, lo, hi) con c tal que sum(f_i * g_i) <= budget.

    Reparte el mismo número total de peticiones (nunca más que `budget`) dando más frecuencia a las
    consultas con más peso. Con pesos iguales devuelve 1.0 para todas.
    """
    n = len(base_freqs)
    if n == 0:
        return []
    if n != len(weights) or min(weights) <= 0 or (max(weights) - min(weights)) < 1e-12:
        return [1.0] * n
    lo, hi = float(lo), float(hi)

    def total(c):
        return sum(f * min(hi, max(lo, c * w)) for f, w in zip(base_freqs, weights))

    c_lo, c_hi = lo / max(weights), hi / min(weights)
    for _ in range(60):
        mid = math.sqrt(c_lo * c_hi)
        if total(mid) > budget:
            c_hi = mid
        else:
            c_lo = mid
    return [min(hi, max(lo, c_lo * w)) for w in weights]


def _extract_queries_for_platform(target: dict, platform: str):
    plat = (platform or "wallapop").lower()
    if plat == "wallapop" and "wallapop_keywords" in target:
        wk = target["wallapop_keywords"]
        qs = wk if isinstance(wk, list) else [wk]
    elif plat == "vinted" and "vinted_keywords" in target:
        vk = target["vinted_keywords"]
        qs = vk if isinstance(vk, list) else [vk]
    else:
        kw = target.get("keywords", "")
        qs = kw if isinstance(kw, list) else [str(kw).strip()]
    return [str(q).strip() for q in qs if str(q).strip()]


def build_query_tasks_from_config(config: dict, platform: str = "wallapop", now: float = 0.0):
    plat = (platform or "wallapop").lower()
    sched_cfg = (config or {}).get("scheduling", {}) or {}
    plat_cfg = sched_cfg.get(plat, {}) or {}
    default_hot = 20.0 if plat == "wallapop" else 60.0
    default_std = 80.0 if plat == "wallapop" else 240.0
    hot_interval = float(plat_cfg.get("hot_interval_s", default_hot))
    std_interval = float(plat_cfg.get("standard_interval_s", default_std))

    tasks = []
    targets = (config or {}).get("targets", (config or {}).get("searches", [])) or []
    for t in targets:
        if not isinstance(t, dict) or not t.get("enabled", True):
            continue
        platforms = [str(p).lower() for p in t.get("platforms", [plat])]
        if plat not in platforms:
            continue
        tier = str(t.get("tier", "hot")).lower()
        interval_s = hot_interval if tier == "hot" else std_interval
        radar_id = str(t.get("id") or t.get("name") or "")
        min_p = float(t.get("min_price", 0) or 0)
        max_p = float(t.get("max_price", 9999) or 9999)
        for q in _extract_queries_for_platform(t, plat):
            tasks.append(
                QueryTask(
                    next_due=float(now),
                    radar_id=radar_id,
                    query=q,
                    min_price=min_p,
                    max_price=max_p,
                    interval_s=interval_s,
                    tier=tier,
                    target=t,
                )
            )
    return tasks


class QueryScheduler:
    """Planificador heapq por consulta con escalonamiento inicial y recarga en caliente.

    - Ajusta la cadencia deseada de cada consulta a la capacidad real del cubo: si la configuración
      pide más peticiones de las que caben, todas se alargan en la misma proporción (ninguna se queda
      sin sondear).
    - Entre las consultas vencidas elige la más atrasada en proporción a su propia cadencia.
    - Opcional (`adaptive`): reparte el mismo número de peticiones dando más frecuencia a las consultas
      que traen más candidatos reales, dentro de [min_factor, max_factor] de su cadencia.
    """

    def __init__(
        self,
        tasks=None,
        bucket: TokenBucket = None,
        *,
        platform: str = "wallapop",
        config: dict = None,
        config_path: str = None,
        night_multiplier: float = 3.0,
        night_start_hour: int = 2,
        night_end_hour: int = 7,
        clock=None,
        sleeper=None,
        now_fn=None,
        heartbeat=None,
        rng=None,
        stagger_initial: bool = True,
        utilization: float = 0.97,
        adaptive: dict = None,
        state_path: str = None,
        wall_clock=None,
    ):
        self._lock = threading.Lock()
        self.platform = (platform or "wallapop").lower()
        self.bucket = bucket
        self._clock = clock or (bucket._clock if bucket is not None else time.monotonic)
        self._sleeper = sleeper or (bucket._sleeper if bucket is not None else time.sleep)
        self._now_fn = now_fn or datetime.now
        self._heartbeat = heartbeat
        self._rng = rng or random.Random()
        self.night_multiplier = float(night_multiplier)
        self.night_start_hour = int(night_start_hour)
        self.night_end_hour = int(night_end_hour)
        self.config_path = config_path
        self.config = config or {}
        self.utilization = min(1.0, max(0.5, float(utilization)))
        self.adaptive = dict(adaptive) if isinstance(adaptive, dict) else dict(ADAPTIVE_DEFAULTS)
        self.state_path = state_path
        self._wall_clock = wall_clock or time.time
        self._last_fit = None
        self._last_save = None
        self._prio_mark = None   # (instante, priority_grants) del último ajuste
        self.prio_rate = 0.0     # tokens/s que se llevan fichas y perfiles (media exponencial)
        self.plan = {}
        self._last_mtime = None
        if self.config_path and os.path.exists(self.config_path):
            try:
                self._last_mtime = os.path.getmtime(self.config_path)
            except Exception:
                self._last_mtime = None

        raw_tasks = list(tasks) if tasks is not None else build_query_tasks_from_config(self.config, self.platform, self._clock())
        if stagger_initial and len(raw_tasks) > 1:
            self._stagger_tasks_in_place(raw_tasks)
        self._all = list(raw_tasks)
        self._heap = list(raw_tasks)
        heapq.heapify(self._heap)
        self._load_state()
        self._fit_intervals()

    @classmethod
    def from_config(
        cls,
        config: dict,
        *,
        platform: str = "wallapop",
        bucket: TokenBucket = None,
        config_path: str = None,
        clock=None,
        sleeper=None,
        now_fn=None,
        heartbeat=None,
        rng=None,
        state_path: str = None,
    ):
        plat = (platform or "wallapop").lower()
        sched_cfg = (config or {}).get("scheduling", {}) or {}
        plat_cfg = sched_cfg.get(plat, {}) or {}
        default_rate = 1.0 if plat == "wallapop" else 0.30
        rate_per_s = float(plat_cfg.get("rate_per_s", default_rate))
        night_mult = float(sched_cfg.get("night_multiplier", 3.0))
        clk = clock or (bucket._clock if bucket is not None else time.monotonic)
        slp = sleeper or (bucket._sleeper if bucket is not None else time.sleep)
        if bucket is None:
            bucket = TokenBucket(rate_per_s=rate_per_s, burst=2, clock=clk, sleeper=slp,
                                 max_debt=cls._cfg_number(plat_cfg, sched_cfg, "priority_burst", 3))
        else:
            bucket.set_base_rate(rate_per_s)
        tasks = build_query_tasks_from_config(config, plat, now=float(clk()))
        return cls(
            tasks,
            bucket=bucket,
            platform=plat,
            config=config,
            config_path=config_path,
            night_multiplier=night_mult,
            clock=clk,
            sleeper=slp,
            now_fn=now_fn,
            heartbeat=heartbeat,
            rng=rng,
            utilization=cls._cfg_number(plat_cfg, sched_cfg, "utilization", 0.97),
            adaptive=parse_adaptive_config(sched_cfg, plat_cfg),
            state_path=state_path,
        )

    @staticmethod
    def _cfg_number(plat_cfg, sched_cfg, key, default):
        for src in (plat_cfg, sched_cfg):
            if isinstance(src, dict) and src.get(key) is not None:
                try:
                    return float(src[key])
                except (TypeError, ValueError):
                    pass
        return float(default)

    # ---------- Ajuste a la capacidad y reparto por rendimiento ----------

    @property
    def all_tasks(self):
        with self._lock:
            return list(self._all)

    def _fit_intervals(self):
        """Calcula eff_interval_s de cada consulta: cabe en el cubo y, si procede, pondera por rendimiento."""
        with self._lock:
            tasks = list(self._all)
        now = float(self._clock())
        self._last_fit = now
        if not tasks:
            self.plan = {}
            return
        # Los tokens a crédito de fichas/perfiles salen de la misma cuota: se descuentan del presupuesto.
        if self.bucket is not None:
            pg = int(getattr(self.bucket, "priority_grants", 0) or 0)
            if self._prio_mark is not None and now - self._prio_mark[0] >= 1.0:
                inst = max(0.0, (pg - self._prio_mark[1]) / (now - self._prio_mark[0]))
                self.prio_rate += 0.5 * (inst - self.prio_rate)
            self._prio_mark = (now, pg)
        # Cada consulta cuesta lo que gasta de verdad (2.ª página incluida), no un token fijo.
        demand = sum(max(1.0, float(t.cost)) / max(0.5, float(t.interval_s)) for t in tasks)
        budget = demand
        if self.bucket is not None:
            cap = float(self.bucket.base) * self.utilization
            budget = min(demand, max(cap * 0.5, cap - self.prio_rate))
        scale = (budget / demand) if demand > 0 else 1.0
        mults = [1.0] * len(tasks)
        ad = self.adaptive or {}
        adaptive_on = bool(ad.get("enabled")) and len(tasks) > 1
        if adaptive_on:
            lo = 1.0 / float(ad["max_factor"])
            hi = 1.0 / float(ad["min_factor"])
            prior = float(ad["prior_per_hour"])
            # Frecuencia óptima ∝ √(rendimiento / coste): una consulta que gasta 2 tokens por sondeo
            # necesita el doble de rendimiento para ganar la misma frecuencia.
            weights = [math.sqrt((max(0.0, float(t.yield_rate)) * 3600.0 + prior) / max(1.0, float(t.cost)))
                       for t in tasks]
            token_rates = [scale * max(1.0, float(t.cost)) / max(0.5, float(t.interval_s)) for t in tasks]
            mults = balanced_multipliers(token_rates, weights, budget, lo, hi)
        for t, g in zip(tasks, mults):
            t.eff_interval_s = max(0.5, float(t.interval_s)) / (scale * g)
        self.plan = {
            "demand_per_min": round(demand * 60.0, 2),
            "budget_per_min": round(budget * 60.0, 2),
            "item_tokens_per_min": round(self.prio_rate * 60.0, 2),
            "scale": round(scale, 4),
            "oversubscribed": bool(scale < 0.999),
            "adaptive": adaptive_on,
            "tasks": len(tasks),
        }

    def describe(self) -> str:
        """Resumen de una línea del plan vigente (para el log de arranque y los informes)."""
        p = self.plan or {}
        if not p:
            return "sin consultas"
        tiers = {}
        for t in self.all_tasks:
            tiers.setdefault(t.tier, []).append(t.effective_interval)
        parts = [f"{tier}: {min(v):.0f}-{max(v):.0f}s (n={len(v)})" for tier, v in sorted(tiers.items())]
        fit = "sobre-suscrito, cadencias alargadas" if p.get("oversubscribed") else "cabe en la cuota"
        return (f"demanda {p['demand_per_min']:.1f}/min vs cuota {p['budget_per_min']:.1f}/min ({fit}, "
                f"x{1.0 / max(1e-9, p['scale']):.2f}; fichas/perfiles {p.get('item_tokens_per_min', 0):.1f}/min) | "
                f"adaptativo={'sí' if p.get('adaptive') else 'no'} | " + " | ".join(parts))

    def yield_score(self, candidates=0, alerts=0) -> float:
        """Rendimiento de un sondeo: candidatos reales más un extra por cada alerta enviada."""
        bonus = float((self.adaptive or {}).get("alert_bonus", ADAPTIVE_DEFAULTS["alert_bonus"]))
        return max(0.0, float(candidates or 0)) + bonus * max(0.0, float(alerts or 0))

    def _observe_yield(self, task: QueryTask, yield_count, now: float):
        """Actualiza la media exponencial de candidatos/s de la consulta con lo que trajo este sondeo."""
        try:
            count = max(0.0, float(yield_count or 0))
        except (TypeError, ValueError):
            count = 0.0
        if task.last_done and task.last_done > 0 and now > task.last_done:
            dt = max(1.0, now - task.last_done)
            tau = float((self.adaptive or {}).get("tau_s") or ADAPTIVE_DEFAULTS["tau_s"])
            alpha = 1.0 - math.exp(-dt / tau)
            task.yield_rate += alpha * ((count / dt) - task.yield_rate)
        task.last_done = now
        task.polls += 1

    def _load_state(self):
        """Recupera el rendimiento aprendido antes del reinicio (si es reciente) para no partir de cero."""
        if not self.state_path:
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved_at = float(data.get("saved_at") or 0)
            if self._wall_clock() - saved_at > 12 * 3600:
                return
            rates = data.get("rates") or {}
            for t in self._all:
                v = rates.get(f"{t.radar_id}|{t.query}")
                if v is not None:
                    t.yield_rate = max(0.0, float(v))
        except Exception:
            return

    def save_state(self):
        if not self.state_path:
            return False
        try:
            payload = {
                "saved_at": float(self._wall_clock()),
                "platform": self.platform,
                "rates": {f"{t.radar_id}|{t.query}": round(float(t.yield_rate), 9) for t in self.all_tasks},
            }
            tmp = f"{self.state_path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
            self._last_save = float(self._clock())
            return True
        except Exception:
            return False

    @staticmethod
    def _stagger_tasks_in_place(tasks):
        by_interval = {}
        for t in tasks:
            by_interval.setdefault(float(t.interval_s), []).append(t)
        for interval_s, group in by_interval.items():
            if len(group) <= 1:
                continue
            dues = {round(float(t.next_due), 6) for t in group}
            if len(dues) == 1:
                base_due = float(group[0].next_due)
                n = len(group)
                for idx, t in enumerate(group):
                    t.next_due = base_due + (idx * interval_s) / n

    @property
    def tasks(self):
        with self._lock:
            return list(self._heap)

    def is_night_time(self) -> bool:
        dt = self._now_fn()
        h = dt.hour if hasattr(dt, "hour") else int(dt)
        return self.night_start_hour <= h < self.night_end_hour

    def _sleep_until(self, target_time: float):
        while True:
            now = float(self._clock())
            rem = target_time - now
            if rem <= 0.0:
                break
            step = min(15.0, rem)
            self._sleeper(step)
            if self._heartbeat is not None:
                try:
                    self._heartbeat()
                except Exception:
                    pass

    def next(self) -> QueryTask:
        """Espera hasta next_due (llamando a touch_heartbeat cada <= 15 s) y adquiere 1 token del bucket."""
        with self._lock:
            if not self._heap:
                raise RuntimeError("QueryScheduler has no tasks")
            earliest_due = self._heap[0].next_due

        now = float(self._clock())
        if earliest_due > now:
            self._sleep_until(earliest_due)
        elif self._heartbeat is not None:
            try:
                self._heartbeat()
            except Exception:
                pass

        if self.bucket is not None:
            self.bucket.acquire()

        now_after = float(self._clock())
        with self._lock:
            due = []
            while self._heap and self._heap[0].next_due <= now_after:
                due.append(heapq.heappop(self._heap))
            if not due:
                task = heapq.heappop(self._heap)
            else:
                # Entre las vencidas, la más atrasada en proporción a su propia cadencia. Con prioridad
                # estricta a 'hot' y el cubo saturado, las 'standard' no se sondeaban nunca de día.
                due.sort(key=lambda t: (
                    -(now_after - float(t.next_due)) / max(0.5, t.effective_interval),
                    t.effective_interval,
                    float(t.next_due),
                ))
                task = due.pop(0)
                for rem_t in due:
                    heapq.heappush(self._heap, rem_t)
            if self.bucket is not None:
                # El token de esta búsqueda ya está concedido: done() medirá lo que gastó el sondeo entero
                task.grants_mark = int(getattr(self.bucket, "grants", 0) or 0) - 1
            return task

    def done(self, task: QueryTask, yield_count=0) -> QueryTask:
        """Reprograma task con next_due = now + cadencia_efectiva * mult * uniform(0.85, 1.15).

        `yield_count`: candidatos reales que trajo este sondeo (anuncios nuevos que superan los filtros
        locales). Alimenta el reparto adaptativo; con 0 o sin `adaptive` el comportamiento no cambia.
        """
        now = float(self._clock())
        if self.bucket is not None and task.grants_mark >= 0:
            used = int(getattr(self.bucket, "grants", 0) or 0) - task.grants_mark
            task.cost += 0.25 * (min(4.0, max(1.0, float(used))) - task.cost)
        task.grants_mark = -1
        self._observe_yield(task, yield_count, now)
        recompute_s = float((self.adaptive or {}).get("recompute_s") or ADAPTIVE_DEFAULTS["recompute_s"])
        if self._last_fit is None or (now - self._last_fit) >= recompute_s:
            self._fit_intervals()
            if self.state_path and (self._last_save is None or (now - self._last_save) >= 600.0):
                self.save_state()
                self._last_save = now
        mult = self.night_multiplier if self.is_night_time() else 1.0
        jitter = self._rng.uniform(0.85, 1.15)
        task.next_due = now + task.effective_interval * mult * jitter
        with self._lock:
            heapq.heappush(self._heap, task)
        return task

    def reload_if_changed(self, config_mtime_or_path=None) -> bool:
        """Reconstruye las tareas si cambia el mtime de config.json, conservando next_due de las existentes."""
        path = self.config_path
        given_mtime = None
        if isinstance(config_mtime_or_path, (int, float)):
            given_mtime = float(config_mtime_or_path)
        elif isinstance(config_mtime_or_path, str):
            path = config_mtime_or_path

        if given_mtime is None:
            if not path or not os.path.exists(path):
                return False
            try:
                given_mtime = float(os.path.getmtime(path))
            except Exception:
                return False

        if self._last_mtime is not None and abs(given_mtime - self._last_mtime) < 1e-6:
            return False

        new_cfg = None
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    new_cfg = json.load(f)
            except Exception:
                return False
        if not isinstance(new_cfg, dict):
            return False

        self._last_mtime = given_mtime
        self.config = new_cfg
        sched_cfg = new_cfg.get("scheduling", {}) or {}
        plat_cfg = sched_cfg.get(self.platform, {}) or {}
        if "night_multiplier" in sched_cfg:
            self.night_multiplier = float(sched_cfg["night_multiplier"])
        if self.bucket is not None and "rate_per_s" in plat_cfg:
            self.bucket.set_base_rate(float(plat_cfg["rate_per_s"]))
        self.utilization = min(1.0, max(0.5, self._cfg_number(plat_cfg, sched_cfg, "utilization", self.utilization)))
        self.adaptive = parse_adaptive_config(sched_cfg, plat_cfg)

        now = float(self._clock())
        fresh_tasks = build_query_tasks_from_config(new_cfg, self.platform, now=now)
        with self._lock:
            existing_by_key = {(t.radar_id, t.query): t for t in self._all}
            merged = []
            new_only = []
            for ft in fresh_tasks:
                prev = existing_by_key.get((ft.radar_id, ft.query))
                if prev is not None:
                    ft.next_due = prev.next_due
                    ft.yield_rate = prev.yield_rate
                    ft.last_done = prev.last_done
                    ft.polls = prev.polls
                    ft.cost = prev.cost
                    merged.append(ft)
                else:
                    new_only.append(ft)
            if len(new_only) > 1:
                self._stagger_tasks_in_place(new_only)
            merged.extend(new_only)
            self._all = list(merged)
            self._heap = merged
            heapq.heapify(self._heap)
        self._fit_intervals()
        return True


_PLATFORM_BUCKETS = {}
_PLATFORM_BUCKETS_LOCK = threading.Lock()


def set_platform_bucket(platform: str, bucket: TokenBucket):
    with _PLATFORM_BUCKETS_LOCK:
        if bucket is None:
            _PLATFORM_BUCKETS.pop(str(platform or "").upper(), None)
        else:
            _PLATFORM_BUCKETS[str(platform or "").upper()] = bucket


def get_platform_bucket(platform: str) -> TokenBucket:
    with _PLATFORM_BUCKETS_LOCK:
        return _PLATFORM_BUCKETS.get(str(platform or "").upper())

