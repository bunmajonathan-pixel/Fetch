from __future__ import annotations
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl: float = 300.0, max_size: int = 1024):
        self.ttl, self.max_size = ttl, max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if not entry:
            return None
        expires, value = entry
        if expires < time.monotonic():
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self.max_size:
            self._store.pop(next(iter(self._store)))
        self._store[key] = (time.monotonic() + self.ttl, value)
