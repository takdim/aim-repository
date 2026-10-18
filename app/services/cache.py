import time
import threading
from typing import Any, Optional


class TTLCache:
    """Thread-safe in-memory key-value cache with per-key TTL and a size cap."""

    def __init__(self, max_entries: int = 2000) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

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
            now = time.monotonic()
            self._store.pop(key, None)
            if len(self._store) >= self._max_entries:
                for k in [k for k, (_, exp) in self._store.items() if exp <= now]:
                    del self._store[k]
            # Evict entri tertua (urutan insert) bila masih melebihi batas.
            while len(self._store) >= self._max_entries:
                self._store.pop(next(iter(self._store)))
            self._store[key] = (value, now + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


# Shared cache instances
metadata_cache = TTLCache(max_entries=2000)   # search results + eprint detail (10 min TTL)
pdf_bytes_cache = TTLCache(max_entries=24)    # raw/watermarked PDF bytes (5 min TTL)
