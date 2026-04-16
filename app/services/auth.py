"""Per-user API-key authentication (v4).

Starting with PRD v4 every user has their own API key. The middleware:

1. Reads ``X-API-Key`` from the request.
2. Looks up the user by ``sha256(key)`` in SQLite (constant-time-safe).
3. Attaches the ``User`` to ``request.state.user`` so route handlers can
   depend on it and the service layer can scope every query by
   ``owner_id``.
4. Short-circuits with 401 for protected routes when no valid user matches.

Backward compatibility: if ``CCS_ADMIN_KEY`` (or the legacy ``CCS_API_KEY``)
is set and the DB has an admin user with that key, the existing single-key
workflows keep working unchanged.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import storage


# Routes that never require auth. Health probes, docs, and the static
# frontend all need to bootstrap before the user has typed a key in.
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
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        supplied = request.headers.get("X-API-Key", "")
        if supplied:
            user = storage.get_user_by_api_key(supplied)
            if user:
                request.state.user = user

        if _is_protected_api_path(request.url.path) and request.state.user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required. Set X-API-Key header."},
            )
        return await call_next(request)
