"""Small thread-safe TTL cache used to avoid repeat upstream calls."""
from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    """Minimal in-memory cache with per-entry expiry and a size cap."""

    def __init__(self, ttl: float, maxsize: int = 256) -> None:
        self.ttl, self.maxsize = ttl, maxsize
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
            self._data.pop(key, None)
            return None

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self.maxsize:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.time() + self.ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


AIR_CACHE = TTLCache(ttl=600)   # 10 min: Open-Meteo refreshes hourly
GEO_CACHE = TTLCache(ttl=3600)  # place names rarely change
