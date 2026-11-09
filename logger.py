# -*- coding: utf-8 -*-
"""
Módulo de Logging Estructurado y Métricas (P3-4)
- Formato consistente con timestamps y niveles (INFO, WARN, ERROR, DEBUG).
- RotatingFileHandler para rotación automática de ficheros (10MB, 5 backups).
- Emisión de métricas operacionales estructuradas en formato JSON Lines (metrics.jsonl).
- Interceptor opcional de stdout/print hacia el archivo rotativo para garantizar trazabilidad.
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

LOG_DIR = os.getenv("LOG_DIR", "/app/data/logs" if os.path.exists("/app/data") else "./data/logs")
METRICS_FILE = os.path.join(LOG_DIR, "metrics.jsonl")

_initialized_loggers = {}
_metrics_handler = None


class JSONMetricsLogger:
    """Registra eventos y métricas de rendimiento en un fichero .jsonl rotativo."""
    def __init__(self, filepath, max_bytes=10 * 1024 * 1024, backup_count=3):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
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
            if self.handler and getattr(self.handler, "stream", None):
                if getattr(self.handler, "maxBytes", 0) > 0:
                    try:
                        if self.handler.stream.tell() + len(line.encode("utf-8")) >= self.handler.maxBytes:
                            self.handler.doRollover()
                    except Exception:
                        pass
                self.handler.stream.write(line)
                self.handler.stream.flush()
        except Exception:
            pass

    def close(self):
        try:
            self.handler.close()
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
                if getattr(self.file_handler, "maxBytes", 0) > 0:
                    try:
                        if self.file_handler.stream.tell() + len(line.encode("utf-8")) >= self.file_handler.maxBytes:
                            self.file_handler.doRollover()
                    except Exception:
                        pass
                self.file_handler.stream.write(line)
                self.file_handler.stream.flush()
            except Exception:
                pass

    def flush(self):
        self.original_stdout.flush()
        if self.file_handler and getattr(self.file_handler, "stream", None):
            try:
                self.file_handler.stream.flush()
            except Exception:
                pass


def setup_logger(service_name, log_dir=None, level=logging.INFO, capture_stdout=True):
    global _initialized_loggers
    svc = service_name.upper()
    if svc in _initialized_loggers:
        return _initialized_loggers[svc]

    target_dir = log_dir or LOG_DIR
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
        if _metrics_handler is None:
            os.makedirs(LOG_DIR, exist_ok=True)
            _metrics_handler = JSONMetricsLogger(METRICS_FILE)
        _metrics_handler.emit(event, service=service, platform=platform, **kwargs)
    except Exception as e:
        sys.stderr.write(f"[WARN] Error emitiendo métrica JSON: {e}\n")

