# -*- coding: utf-8 -*-
"""
Capa HTTP coherente con sesiones persistentes por hilo y huella TLS/JA3 actual (T2.2).
- Perfiles soportados por curl_cffi 0.17.0: chrome152, chrome152_macos, safari260.
- Cabeceras XHR coherentes (Sec-Fetch-Mode: cors, Sec-Fetch-Dest: empty, Sec-Fetch-Site: same-site,
  sin Sec-Fetch-User ni Upgrade-Insecure-Requests) y cabeceras Document para navegación HTML.
- Una sesión curl_cffi.requests.Session por plataforma e hilo (threading.local).
"""

import os
import time
import random
import threading
from curl_cffi import requests

PROFILE_ORDER = ("chrome152", "chrome152_macos", "safari260")
CHROME_PROFILES = ("chrome152", "chrome152_macos")

# Cabeceras nativas exactas de curl_cffi 0.17.0 por perfil (evita desalinear UA / sec-ch-ua con TLS)
NATIVE_PROFILE_HEADERS = {
    "chrome152": {
        "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="152", "Google Chrome";v="152"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
        ),
        "Accept-Encoding": "gzip, deflate, br, zstd",
    },
    "chrome152_macos": {
        "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="152", "Google Chrome";v="152"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
        ),
        "Accept-Encoding": "gzip, deflate, br, zstd",
    },
    "safari260": {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/26.0 Safari/605.1.15"
        ),
        "Accept-Encoding": "gzip, deflate, br",
    },
}

XHR_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

DOC_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
}


def build_headers(impersonate, kind="xhr", origin=None, referer=None, extra_headers=None, call_headers=None):
    base_prof = NATIVE_PROFILE_HEADERS.get(impersonate, NATIVE_PROFILE_HEADERS["chrome152"])
    out = dict(base_prof)
    if kind == "document":
        out.update(DOC_HEADERS)
        if referer:
            out["Referer"] = referer
            out["Sec-Fetch-Site"] = "same-origin"
    else:
        out.update(XHR_HEADERS)
        if origin:
            out["Origin"] = origin
        if referer:
            out["Referer"] = referer
    if extra_headers:
        out.update(extra_headers)
    if call_headers:
        for k, v in call_headers.items():
            if v is None:
                out.pop(k, None)
            else:
                out[k] = v
    if kind != "document":
        for forbidden in ("Sec-Fetch-User", "sec-fetch-user", "Upgrade-Insecure-Requests", "upgrade-insecure-requests"):
            out.pop(forbidden, None)
    return out


class PlatformClient:
    """Una sesión curl_cffi por plataforma y por hilo (threading.local: Session no es thread-safe)."""

    def __init__(
        self,
        platform,
        *,
        impersonate="chrome152",
        origin=None,
        referer=None,
        extra_headers=None,
        bucket=None,
        health_monitor=None,
        proxies=None,
        rotator=None,
        chrome_only=False,
    ):
        self.platform = str(platform or "GLOBAL").upper()
        self.impersonate = impersonate or "chrome152"
        self.origin = origin
        self.referer = referer
        self.extra_headers = dict(extra_headers or {})
        self.bucket = bucket
        self.health_monitor = health_monitor
        self.proxies = proxies
        self.rotator = rotator
        self.chrome_only = chrome_only
        self._local = threading.local()
        self._lock = threading.Lock()
        self._epoch = 0

    def _current_impersonate(self):
        if self.rotator is not None:
            try:
                prof = self.rotator.get_chrome_profile() if self.chrome_only else self.rotator.get_profile()
                if isinstance(prof, dict) and prof.get("impersonate"):
                    return prof["impersonate"]
            except Exception:
                pass
        return self.impersonate

    def _get_session(self):
        sess = getattr(self._local, "session", None)
        sess_epoch = getattr(self._local, "epoch", -1)
        imp = self._current_impersonate()
        sess_imp = getattr(self._local, "impersonate", None)
        if sess is None or sess_epoch != self._epoch or sess_imp != imp:
            if sess is not None:
                try:
                    sess.close()
                except Exception:
                    pass
            proxies = self.proxies
            if proxies is None:
                proxy_env = os.environ.get("PROXY_URL", "").strip()
                if proxy_env:
                    proxies = {"http": proxy_env, "https": proxy_env}
            sess = requests.Session(impersonate=imp, proxies=proxies, default_headers=False)
            self._local.session = sess
            self._local.epoch = self._epoch
            self._local.impersonate = imp
        return sess, imp

    def rotate(self):
        """Fuerza sesión nueva con otro perfil (tras un cooldown o rotación periódica)."""
        with self._lock:
            if self.rotator is not None:
                try:
                    prof = self.rotator.rotate()
                    if isinstance(prof, dict) and prof.get("impersonate"):
                        self.impersonate = prof["impersonate"]
                except Exception:
                    pass
            else:
                pool = CHROME_PROFILES if self.chrome_only else PROFILE_ORDER
                others = [p for p in pool if p != self.impersonate] or list(pool)
                self.impersonate = random.choice(others)
            self._epoch += 1
        sess = getattr(self._local, "session", None)
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
            self._local.session = None
        return self.impersonate

    def request(self, method, url, *, kind="xhr", query="", record_metrics=True, notify_health=False, **kw):
        if self.bucket is not None:
            self.bucket.acquire()
        call_headers = kw.pop("headers", None)
        sess, imp = self._get_session()
        hdrs = build_headers(
            imp,
            kind=kind,
            origin=self.origin,
            referer=self.referer,
            extra_headers=self.extra_headers,
            call_headers=call_headers,
        )
        kw.setdefault("timeout", 15)
        t0 = time.time()
        resp = getattr(sess, method.lower())(url, headers=hdrs, **kw)
        elapsed = time.time() - t0
        if record_metrics:
            try:
                from antiban import default_rate_limiter
                default_rate_limiter.record_request(self.platform, elapsed, resp.status_code)
            except Exception:
                pass
        if notify_health and self.health_monitor is not None:
            if resp.status_code in (401, 403, 429):
                retry_after = (getattr(resp, "headers", None) or {}).get("Retry-After")
                should_cd, _ = self.health_monitor.handle_http_response(
                    resp.status_code, url=url, query=query, retry_after=retry_after
                )
                if should_cd:
                    self.rotate()
            elif resp.status_code == 200:
                self.health_monitor.handle_http_response(200, query=query)
        return resp

    def get(self, url, *, kind="xhr", **kw):
        return self.request("GET", url, kind=kind, **kw)

    def post(self, url, *, kind="xhr", **kw):
        return self.request("POST", url, kind=kind, **kw)


# Clientes compartidos por plataforma
wallapop_client = PlatformClient(
    "WALLAPOP",
    impersonate="chrome152",
    origin="https://es.wallapop.com",
    referer="https://es.wallapop.com/",
    extra_headers={"X-DeviceOS": "0"},
    chrome_only=True,
)

vinted_doc_client = PlatformClient(
    "VINTED",
    impersonate="chrome152",
    referer="https://www.vinted.es/",
    chrome_only=True,
)

cex_client = PlatformClient(
    "CEX",
    impersonate="safari260",
    origin="https://es.webuy.com",
    referer="https://es.webuy.com/",
    extra_headers={"Content-Type": "application/json", "Accept": "application/json"},
)

image_client = PlatformClient(
    "IMAGES",
    impersonate="safari260",
)
