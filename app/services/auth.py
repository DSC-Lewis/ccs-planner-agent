"""Optional API-key authentication.

When ``CCS_API_KEY`` is set in the environment, every ``/api/*`` route
except ``/api/health`` MUST include an ``X-API-Key`` header whose value
matches the configured key. When the variable is empty or missing, the
guard is a no-op — this keeps local development and existing demos
zero-config.

We use :func:`secrets.compare_digest` for the comparison so a remote
attacker can't use response-time differences to learn the key byte-by-byte.
"""
from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import API_KEY


# Paths that remain open even when authentication is enabled.
# Health probes, the static frontend, and the OpenAPI docs must stay
# reachable — without them ops tooling and the UI can't bootstrap.
OPEN_PATH_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _is_protected_api_path(path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    return not any(path.startswith(p) for p in OPEN_PATH_PREFIXES)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces ``X-API-Key`` on API routes."""

    async def dispatch(self, request: Request, call_next):
        # Re-read the configured key each request so tests that reload the
        # config module observe the new value immediately.
        from ..config import API_KEY as CURRENT_KEY
        if CURRENT_KEY and _is_protected_api_path(request.url.path):
            supplied = request.headers.get("X-API-Key", "")
            if not supplied or not secrets.compare_digest(
                supplied.encode("utf-8"), CURRENT_KEY.encode("utf-8")
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API key required or invalid. Set X-API-Key header."},
                )
        return await call_next(request)
