import time
import threading
from typing import Any, Optional


class TTLCache:
    """Thread-safe in-memory key-value cache with per-key TTL."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if time.monotonic() < expire_at:
                return value
            del self._store[key]
            return None

    def set(self, key: str, value: Any, ttl: int = 600) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


# Shared cache instances
metadata_cache = TTLCache()   # search results + eprint detail (10 min TTL)
pdf_bytes_cache = TTLCache()  # raw PDF bytes (5 min TTL)
