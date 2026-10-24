import functools
import threading
import time
from collections import defaultdict, deque

from flask import Response, request

from .security_logger import log_rate_limit

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()
_last_purge = 0.0


def _client_ip() -> str:
    return request.remote_addr or "unknown"


def _purge(now: float) -> None:
    global _last_purge
    if now - _last_purge < 60:
        return
    _last_purge = now
    for key in [k for k, dq in _hits.items() if not dq or now - dq[-1] > 3600]:
        del _hits[key]


def rate_limit(name: str, limit: int, window: int = 60, burst_limit: int | None = None, burst_window: int = 4):
    """Batasi request per IP (sliding window, in-memory) dengan proteksi lonjakan cepat (burst).
    
    // ASVS 2.4.1: Anti-automation protection against rapid brute-force/scraping.
    // ASVS 16.3.3: Log attempts to bypass anti-automation controls.
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            client_ip = _client_ip()
            key = f"{name}:{client_ip}"
            with _lock:
                _purge(now)
                dq = _hits[key]
                while dq and now - dq[0] > window:
                    dq.popleft()

                # 1. Cek batas burst (mencegah scraping berurutan tanpa jeda)
                if burst_limit is not None and burst_limit > 0:
                    burst_count = sum(1 for t in dq if now - t <= burst_window)
                    if burst_count >= burst_limit:
                        log_rate_limit(client_ip, f"{name}:burst", burst_limit, burst_window)
                        return Response(
                            "Terlalu banyak permintaan dalam waktu singkat. Harap tunggu sebentar.",
                            status=429,
                            headers={"Retry-After": str(burst_window)},
                            mimetype="text/plain",
                        )

                # 2. Cek batas kuota jendela penuh
                if len(dq) >= limit:
                    retry_after = max(1, int(window - (now - dq[0])))
                    log_rate_limit(client_ip, name, limit, window)
                    return Response(
                        "Terlalu banyak permintaan. Coba lagi nanti.",
                        status=429,
                        headers={"Retry-After": str(retry_after)},
                        mimetype="text/plain",
                    )
                dq.append(now)
            return view(*args, **kwargs)

        return wrapper

    return decorator
