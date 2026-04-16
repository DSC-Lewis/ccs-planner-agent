"""In-memory per-IP rate limiter (sliding-window counter).

Simple enough to reason about for a pilot deploy. When horizontal scaling
arrives, swap the ``_buckets`` dict for Redis + ``SETEX`` + ``INCR``.

Design decisions
----------------
* Only **write** endpoints (POST) are throttled — the review flagged write
  endpoints as the DoS vector.
* Limit format: ``N/SECONDS`` (e.g. ``"30/60"`` means 30 requests per 60 s).
* Sliding window computed via a deque of timestamps; entries older than
  the window drop off on each check. O(1) amortised.
* IP is read from ``X-Forwarded-For`` first (so that a reverse proxy
  doesn't collapse all traffic onto the proxy's IP), else ``request.client.host``.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# Only these paths count toward the limit. GET endpoints are intentionally
# excluded — they're read-only and cheap.
LIMITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _Limiter:
    def __init__(self) -> None:
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, max_requests: int, window_seconds: float) -> Tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= max_requests:
                # Retry-After is the time until the oldest request in the
                # window expires.
                retry = max(1.0, bucket[0] + window_seconds - now)
                return False, retry
            bucket.append(now)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_limiter = _Limiter()


def reset() -> None:
    """Test helper — clear all bucket state."""
    _limiter.reset()


def _parse_limit(spec: str) -> Tuple[int, float]:
    """Parse ``"30/60"`` → (30, 60.0). Falls back to the default on any
    malformed input so a typo doesn't crash the app."""
    try:
        n, w = spec.split("/", 1)
        return max(1, int(n)), max(1.0, float(w))
    except (ValueError, AttributeError):
        return 30, 60.0


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in LIMITED_METHODS:
            from ..config import RATE_LIMIT  # late import for hot-reload
            max_req, window = _parse_limit(RATE_LIMIT)
            allowed, retry = _limiter.check(_client_ip(request), max_req, window)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            f"Rate limit exceeded ({max_req} per "
                            f"{int(window)}s). Retry in {int(retry)}s."
                        )
                    },
                    headers={"Retry-After": str(int(retry))},
                )
        return await call_next(request)
