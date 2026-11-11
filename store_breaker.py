"""T1.10: circuit breaker exponencial por tienda.

Un bloqueo de WAF (DataDome, PerimeterX...) abre el breaker de la tienda con un cooldown que se
duplica en cada bloqueo consecutivo (BASE, 2*BASE, 4*BASE... hasta CAP). Un exito lo cierra.
El estado se persiste en JSON (escritura tmp + os.replace) para sobrevivir a reinicios.
Solo stdlib: no importa core_engine.
"""
import json
import os
import threading
import time


def store_enabled(config, key):
    """config.json -> "stores": {"milanuncios": {"enabled": false}} desactiva una tienda."""
    try:
        return bool(((config or {}).get("stores") or {}).get(key, {}).get("enabled", True))
    except AttributeError:
        return True


class StoreBreaker:
    BASE, CAP = 900, 12 * 3600

    def __init__(self, path, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass
        return {}

    def _save(self):
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[STORE BREAKER WARN] No se pudo persistir el estado: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass

    def open_until(self, store):
        with self._lock:
            return float(self._state.get(store, {}).get("open_until", 0) or 0)

    def allow(self, store):
        return self._clock() >= self.open_until(store)

    def record_block(self, store):
        """Devuelve (cooldown_s, notify). notify: primer bloqueo o primera vez que se alcanza CAP."""
        with self._lock:
            st = self._state.setdefault(store, {"failures": 0, "open_until": 0})
            st["failures"] = int(st.get("failures", 0)) + 1
            n = st["failures"]
            cd = min(self.BASE * 2 ** (n - 1), self.CAP)
            prev = min(self.BASE * 2 ** (n - 2), self.CAP) if n > 1 else 0
            st["open_until"] = self._clock() + cd
            self._save()
            return cd, (n == 1 or (cd == self.CAP and prev < self.CAP))

    def record_success(self, store):
        """True si la tienda se recupera de un estado abierto (hay que notificar)."""
        with self._lock:
            st = self._state.get(store)
            if not st or not (st.get("failures") or st.get("open_until")):
                return False
            del self._state[store]
            self._save()
            return True
