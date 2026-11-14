"""In-memory sliding-window rate limiter (port of `web/lib/rate-limit.ts`)."""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def rate_limit(key: str, limit: int = 30, window_seconds: int = 60) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    now = time.monotonic()
    with _lock:
        arr = [t for t in _hits.get(key, []) if now - t < window_seconds]
        if len(arr) >= limit:
            _hits[key] = arr
            return False, max(1, int(window_seconds - (now - arr[0])))
        arr.append(now)
        _hits[key] = arr
        if len(_hits) > 5000:  # opportunistic cleanup
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window_seconds]:
                _hits.pop(k, None)
    return True, 0


def rate_limit_state(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int, int, int]:
    """Like `rate_limit` but also returns (remaining, reset_epoch_seconds) for X-RateLimit-* headers.

    → (allowed, retry_after_seconds, remaining, reset_epoch)"""
    allowed, retry = rate_limit(key, limit, window_seconds)
    now = time.monotonic()
    with _lock:
        arr = [t for t in _hits.get(key, []) if now - t < window_seconds]
    remaining = max(0, limit - len(arr))
    oldest_age = (now - arr[0]) if arr else 0
    reset = int(time.time() + max(1, window_seconds - oldest_age))
    return allowed, retry, remaining, reset
