# -*- coding: utf-8 -*-
"""
Módulo de Logging Estructurado y Métricas (P3-4)
- Formato consistente con timestamps y niveles (INFO, WARN, ERROR, DEBUG).
- RotatingFileHandler para rotación automática de ficheros (10MB, 5 backups).
- Emisión de métricas operacionales estructuradas en formato JSON Lines (metrics.jsonl).
- Interceptor opcional de stdout/print hacia el archivo rotativo para garantizar trazabilidad.
"""

import os
import re
import sys
import json
import logging
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime

LOG_DIR = os.getenv("LOG_DIR", "/app/data/logs" if os.path.exists("/app/data") else "./data/logs")
METRICS_FILE = os.path.join(LOG_DIR, "metrics.jsonl")

_initialized_loggers = {}
_metrics_handler = None
_metrics_handlers = {}
_metrics_lock = threading.Lock()
_default_service = ""


def _norm_service(name):
    return re.sub(r"[^a-z0-9_-]+", "_", str(name or "").strip().lower()).strip("_")


def get_metrics_filepath(service=None, log_dir=None):
    override = os.getenv("METRICS_FILE", "").strip()
    if override:
        return override
    base_dir = log_dir or os.getenv("LOG_DIR") or LOG_DIR
    svc_raw = os.getenv("SERVICE_NAME") or _default_service or (service if service and service != "system" else "")
    svc = _norm_service(svc_raw)
    fname = f"metrics.{svc}.jsonl" if svc else "metrics.jsonl"
    return os.path.join(base_dir, fname)


def compute_pipeline_ms(t):
    """Calcula duraciones en ms por etapa del pipeline a partir del diccionario `t` de perf_counter."""
    if not isinstance(t, dict):
        return {"ms_fetch_to_p1": 0.0, "ms_p1": 0.0, "ms_vision": 0.0, "ms_cex": 0.0, "ms_alert": 0.0}
    if "ms_fetch_to_p1" in t and "ms_p1" in t:
        return {
            "ms_fetch_to_p1": round(float(t.get("ms_fetch_to_p1") or 0.0), 1),
            "ms_p1": round(float(t.get("ms_p1") or 0.0), 1),
            "ms_vision": round(float(t.get("ms_vision") or 0.0), 1),
            "ms_cex": round(float(t.get("ms_cex") or 0.0), 1),
            "ms_alert": round(float(t.get("ms_alert") or 0.0), 1),
        }

    def _dur(start_k, end_keys):
        s = t.get(start_k)
        if s is None:
            return 0.0
        for ek in end_keys:
            e = t.get(ek)
            if e is not None:
                return round(max(0.0, (float(e) - float(s)) * 1000.0), 1)
        return 0.0

    return {
        "ms_fetch_to_p1": _dur("fetch", ("p1", "filters")),
        "ms_p1": _dur("p1", ("p1_end", "image", "vision", "cex", "alert")),
        "ms_vision": _dur("vision", ("vision_end", "cex", "alert")),
        "ms_cex": _dur("cex", ("cex_end", "alert")),
        "ms_alert": _dur("alert", ("alert_end",)),
    }


class JSONMetricsLogger:
    """Registra eventos y métricas de rendimiento en un fichero .jsonl rotativo."""
    def __init__(self, filepath, max_bytes=10 * 1024 * 1024, backup_count=3):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        self.handler = RotatingFileHandler(
            filepath, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )

    def emit(self, event_name, service="system", platform="GLOBAL", **kwargs):
        record = {
            "timestamp": datetime.now().isoformat(),
            "event": event_name,
            "service": service,
            "platform": platform,
            **kwargs
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            if self.handler:
                self.handler.acquire()
                try:
                    if getattr(self.handler, "stream", None) is None:
                        self.handler.stream = self.handler._open()
                    if getattr(self.handler, "maxBytes", 0) > 0:
                        try:
                            self.handler.stream.seek(0, 2)
                            if self.handler.stream.tell() + len(line.encode("utf-8")) >= self.handler.maxBytes:
                                self.handler.doRollover()
                                if getattr(self.handler, "stream", None) is None:
                                    self.handler.stream = self.handler._open()
                        except Exception:
                            pass
                    self.handler.stream.write(line)
                    self.handler.stream.flush()
                finally:
                    self.handler.release()
        except Exception:
            pass

    def close(self):
        try:
            if self.handler:
                self.handler.acquire()
                try:
                    self.handler.close()
                finally:
                    self.handler.release()
        except Exception:
            pass


class StdoutStreamToLogger:
    """Intermediario que replica stdout hacia el rotating file handler para captura transparente de print()."""
    def __init__(self, original_stdout, file_handler):
        self.original_stdout = original_stdout
        self.file_handler = file_handler

    def write(self, message):
        self.original_stdout.write(message)
        if message and message.strip() and self.file_handler and getattr(self.file_handler, "stream", None):
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"{now_str} [INFO] [PRINT] {message.strip()}\n"
            try:
                self.file_handler.acquire()
                try:
                    if getattr(self.file_handler, "maxBytes", 0) > 0:
                        try:
                            self.file_handler.stream.seek(0, 2)
                            if self.file_handler.stream.tell() + len(line.encode("utf-8")) >= self.file_handler.maxBytes:
                                self.file_handler.doRollover()
                        except Exception:
                            pass
                    if getattr(self.file_handler, "stream", None):
                        self.file_handler.stream.write(line)
                        self.file_handler.stream.flush()
                finally:
                    self.file_handler.release()
            except Exception:
                pass

    def flush(self):
        self.original_stdout.flush()
        if self.file_handler and getattr(self.file_handler, "stream", None):
            try:
                self.file_handler.acquire()
                try:
                    if getattr(self.file_handler, "stream", None):
                        self.file_handler.stream.flush()
                finally:
                    self.file_handler.release()
            except Exception:
                pass


def setup_logger(service_name, log_dir=None, level=logging.INFO, capture_stdout=True):
    global _initialized_loggers, _default_service
    if not _default_service:
        _default_service = _norm_service(os.getenv("SERVICE_NAME") or service_name)
    svc = service_name.upper()
    if svc in _initialized_loggers:
        return _initialized_loggers[svc]

    target_dir = log_dir or os.getenv("LOG_DIR") or LOG_DIR
    os.makedirs(target_dir, exist_ok=True)

    logger = logging.getLogger(svc)
    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 1. Console Handler (stdout)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

        # 2. Rotating File Handler (10MB, 5 backups)
        log_file = os.path.join(target_dir, f"{service_name.lower().replace(' ', '_')}.log")
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)

        # 3. Interceptar stdout para volcar print() al fichero rotativo
        if capture_stdout and not isinstance(sys.stdout, StdoutStreamToLogger):
            sys.stdout = StdoutStreamToLogger(sys.stdout, file_handler)

    _initialized_loggers[svc] = logger
    return logger


def get_logger(service_name="SNIPER"):
    svc = service_name.upper()
    if svc in _initialized_loggers:
        return _initialized_loggers[svc]
    return setup_logger(svc)


def log_metric(event, service="system", platform="GLOBAL", **kwargs):
    global _metrics_handler
    try:
        target_path = get_metrics_filepath(service=service)
        with _metrics_lock:
            handler = _metrics_handlers.get(target_path)
            if handler is None:
                os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
                handler = JSONMetricsLogger(target_path)
                _metrics_handlers[target_path] = handler
            _metrics_handler = handler
        handler.emit(event, service=service, platform=platform, **kwargs)
    except Exception as e:
        sys.stderr.write(f"[WARN] Error emitiendo métrica JSON: {e}\n")

