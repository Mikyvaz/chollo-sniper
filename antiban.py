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
    """Limitador de tasa thread-safe con penalización por bloqueo y recuperación gradual."""

    def __init__(self, rate_per_s: float, burst: int = 2, *, clock=time.monotonic, sleeper=time.sleep):
        self._lock = threading.Lock()
        self.base = max(0.01, float(rate_per_s))
        self.base_rate = self.base
        self.rate = self.base
        self.burst = max(1, int(burst))
        self._clock = clock
        self._sleeper = sleeper
        self._tokens = float(self.burst)
        self._last = float(self._clock())
        self._success_streak = 0
        self._recent = deque()

    def set_base_rate(self, rate_per_s: float):
        with self._lock:
            new_base = max(0.01, float(rate_per_s))
            if abs(new_base - self.base) > 1e-9:
                ratio = self.rate / self.base if self.base > 0 else 1.0
                self.base = new_base
                self.base_rate = new_base
                self.rate = max(new_base * 0.25, min(new_base, new_base * ratio))

    def acquire(self) -> float:
        """Bloquea hasta disponer de 1 token; devuelve los segundos esperados."""
        with self._lock:
            now = float(self._clock())
            if now > self._last:
                elapsed = now - self._last
                self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
                self._last = now

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


@dataclass(order=True)
class QueryTask:
    next_due: float
    radar_id: str = field(default="", compare=False)
    query: str = field(default="", compare=False)
    min_price: float = field(default=0.0, compare=False)
    max_price: float = field(default=9999.0, compare=False)
    interval_s: float = field(default=20.0, compare=False)
    tier: str = field(default="hot", compare=False)
    target: dict = field(default_factory=dict, compare=False, repr=False)


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
    """Planificador heapq por consulta con escalonamiento inicial, prioridad por cadencia y recarga en caliente."""

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
        self._last_mtime = None
        if self.config_path and os.path.exists(self.config_path):
            try:
                self._last_mtime = os.path.getmtime(self.config_path)
            except Exception:
                self._last_mtime = None

        raw_tasks = list(tasks) if tasks is not None else build_query_tasks_from_config(self.config, self.platform, self._clock())
        if stagger_initial and len(raw_tasks) > 1:
            self._stagger_tasks_in_place(raw_tasks)
        self._heap = list(raw_tasks)
        heapq.heapify(self._heap)

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
            bucket = TokenBucket(rate_per_s=rate_per_s, burst=2, clock=clk, sleeper=slp)
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
        )

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
                # Entre las tareas ya vencidas, priorizar menor interval_s (hot antes que standard)
                due.sort(key=lambda t: (float(t.interval_s), float(t.next_due)))
                task = due.pop(0)
                for rem_t in due:
                    heapq.heappush(self._heap, rem_t)
            return task

    def done(self, task: QueryTask) -> QueryTask:
        """Reprograma task con next_due = now + interval_s * mult * uniform(0.85, 1.15)."""
        now = float(self._clock())
        mult = self.night_multiplier if self.is_night_time() else 1.0
        jitter = self._rng.uniform(0.85, 1.15)
        task.next_due = now + float(task.interval_s) * mult * jitter
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

        now = float(self._clock())
        fresh_tasks = build_query_tasks_from_config(new_cfg, self.platform, now=now)
        with self._lock:
            existing_by_key = {(t.radar_id, t.query): t for t in self._heap}
            merged = []
            new_only = []
            for ft in fresh_tasks:
                prev = existing_by_key.get((ft.radar_id, ft.query))
                if prev is not None:
                    ft.next_due = prev.next_due
                    merged.append(ft)
                else:
                    new_only.append(ft)
            if len(new_only) > 1:
                self._stagger_tasks_in_place(new_only)
            merged.extend(new_only)
            self._heap = merged
            heapq.heapify(self._heap)
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

