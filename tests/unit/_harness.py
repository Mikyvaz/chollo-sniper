"""Arnés de seguridad para tests unitarios. Importar el primero en cada test nuevo:

    import _harness  # noqa: F401

Garantiza que el test corre aislado (tools/run_isolated.sh), sin secretos y sin red externa.
"""
import os
import socket
import sys

assert os.environ.get("SNIPER_ISOLATED") == "1", \
    "Ejecuta los tests con tools/run_isolated.sh (contenedor sin red, BD copiada, sin secretos)"

_SECRET_PREFIXES = ("DISCORD_", "TELEGRAM_")
_SECRET_NAMES = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NTFY_TOPIC_URL")
for _k in list(os.environ):
    if _k in _SECRET_NAMES or _k.startswith(_SECRET_PREFIXES):
        del os.environ[_k]

# Permite importar los módulos del proyecto y este mismo arnés.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_orig_connect = socket.socket.connect


def _guarded_connect(self, address):
    # AF_UNIX u otras familias sin (host, puerto) se dejan pasar.
    if isinstance(address, tuple) and address:
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode("ascii", "ignore")
        if host not in _LOOPBACK:
            raise OSError(f"[harness] conexión externa bloqueada: {host}")
    return _orig_connect(self, address)


socket.socket.connect = _guarded_connect
