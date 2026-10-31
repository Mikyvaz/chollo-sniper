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

    def calculate_round_sleep(self, base_interval, engine_name="SNIPER"):
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
            sleep_secs = max(18, base_interval + jitter)
            reason = f"Modo Diurno Turbo (Intervalo {base_interval}s con jitter {jitter:+d}s)"

        return sleep_secs, is_night, transition, reason

    @staticmethod
    def inter_query_sleep(min_s=0.65, max_s=1.45):
        time.sleep(random.uniform(min_s, max_s))

    @staticmethod
    def item_processing_sleep():
        time.sleep(random.uniform(0.7, 1.5))


# ==================== GESTOR DE COOLDOWN Y ALERTAS DISCORD ====================

class AntiBanHealthMonitor:
    """Monitoriza bloqueos (403, 429), aplica cooldown preventivo y notifica a Discord (#incidencias)."""
    def __init__(self, service_name):
        self.service_name = service_name
        self.consecutive_blocks = 0
        self.last_block_time = 0
        self.in_cooldown = False
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
        if status_code in (429, 403):
            self.consecutive_blocks += 1
            self.in_cooldown = True
            self.last_block_time = time.time()

            if self.consecutive_blocks == 1:
                cooldown_secs = 420   # 7 minutos
            elif self.consecutive_blocks == 2:
                cooldown_secs = 900   # 15 minutos
            else:
                cooldown_secs = 1800  # 30 minutos

            mins = cooldown_secs // 60
            block_type = "Rate Limit (HTTP 429)" if status_code == 429 else "Acceso Denegado / WAF (HTTP 403)"

            title = f"🚨 [ALERTA ANTIBAN] {self.service_name}: {block_type}"
            desc = (
                f"Se ha detectado una restricción temporal en la plataforma al consultar `{query}`.\n\n"
                f"🛡️ **Medida preventiva de protección activada:**\n"
                f"• **Cooldown obligatorio:** Se pausan peticiones a este origen durante **{mins} minutos**.\n"
                f"• **Rotación automática:** Nueva huella TLS/JA3 y renovación de cookies al reanudar.\n"
                f"• **Reincidencia acumulada:** `{self.consecutive_blocks}`"
            )
            self.send_health_embed(title, desc, 0xE74C3C)
            return True, cooldown_secs

        elif status_code == 200 and self.in_cooldown:
            self.in_cooldown = False
            self.consecutive_blocks = 0
            title = f"✅ [RECUPERADO] {self.service_name}: Conexión Normalizada"
            desc = (
                "La plataforma vuelve a responder correctamente con código **HTTP 200 OK** tras el enfriamiento.\n"
                "El escaneo continúa a ritmo normal."
            )
            self.send_health_embed(title, desc, 0x2ECC71)

        return False, 0

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
