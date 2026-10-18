import functools
import threading
import time
from collections import defaultdict, deque

from flask import Response, request

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


def rate_limit(name: str, limit: int, window: int = 60):
    """Batasi request per IP (sliding window, in-memory). Respons 429 jika terlampaui."""

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            key = f"{name}:{_client_ip()}"
            with _lock:
                _purge(now)
                dq = _hits[key]
                while dq and now - dq[0] > window:
                    dq.popleft()
                if len(dq) >= limit:
                    retry_after = max(1, int(window - (now - dq[0])))
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
