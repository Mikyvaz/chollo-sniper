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
import random
import time
import threading
from collections import deque
from datetime import datetime
from curl_cffi import requests

HEALTH_WEBHOOK_URL = os.environ.get(
    "DISCORD_HEALTH_WEBHOOK_URL",
    os.environ.get("DISCORD_WEBHOOK_URL", "")
).strip()

# ==================== POOL DE PERFILES DE NAVEGADOR (100% COHERENTES CON JA3/TLS) ====================
BROWSER_PROFILES = [
    {
        "name": "Chrome 131 (Win 11 x64)",
        "impersonate": "chrome131",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0"
    },
    {
        "name": "Chrome 124 (Win 11 x64)",
        "impersonate": "chrome124",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0"
    },
    {
        "name": "Chrome 131 (macOS Sequoia)",
        "impersonate": "chrome131",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_platform": '"macOS"',
        "sec_ch_ua_mobile": "?0"
    },
    {
        "name": "Chrome 124 (macOS Sonoma M-Series)",
        "impersonate": "chrome124",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec_ch_ua_platform": '"macOS"',
        "sec_ch_ua_mobile": "?0"
    },
    {
        "name": "Safari 17.0 (macOS Sonoma)",
        "impersonate": "safari17_0",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
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
        return "safari17_0"

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

class AntiBanHealthMonitor:
    """Monitoriza bloqueos (403, 429), aplica cooldown preventivo y notifica a Discord (#incidencias)."""
    def __init__(self, service_name):
        self.service_name = service_name
        self.consecutive_blocks = 0
        self.ok_streak_since_block = 0
        self.last_block_time = 0
        self.in_cooldown = False
        self.cooldown_until = 0
        self.last_ai_alert = 0

    def send_health_embed(self, title, description, color, fields=None):
        if not HEALTH_WEBHOOK_URL:
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
        try:
            requests.post(HEALTH_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        except Exception as e:
            print(f"[HEALTH MONITOR ERROR] Fallo enviando a Discord: {e}")

    def handle_http_response(self, status_code, url="", query=""):
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

            self.cooldown_until = time.time() + cooldown_secs
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
            self.send_health_embed(title, desc, 0xE74C3C)
            return True, cooldown_secs

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
                self.send_health_embed(title, desc, 0x2ECC71)

        return False, 0

    def is_in_cooldown(self):
        """Comprueba si el monitor se encuentra en período de cooldown activo (P3-3)."""
        now = time.time()
        if self.cooldown_until > now:
            return True
        if self.in_cooldown and now >= self.cooldown_until:
            self.in_cooldown = False
        return False

    def get_remaining_cooldown(self):
        """Retorna los segundos restantes del cooldown de estado."""
        return max(0, int(self.cooldown_until - time.time()))

    def reset_cooldown(self):
        """Fuerza la finalización del cooldown."""
        self.cooldown_until = 0
        self.in_cooldown = False

    def notify_ai_error(self, status_code, response_text):
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
