"""Runtime configuration loaded from env vars."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

STORAGE_PATH = Path(os.getenv("CCS_STORAGE_PATH", BASE_DIR / "var" / "storage.json"))
STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CCS_CORS_ORIGINS", "*").split(",")
    if o.strip()
]

CORS_CREDENTIALS = os.getenv("CCS_CORS_CREDENTIALS", "false").lower() in {"1", "true", "yes"}


def validate_cors() -> None:
    """Fail fast on the classic ``*`` + credentials footgun.

    Starlette would echo any origin back when ``allow_origins=["*"]`` and
    ``allow_credentials=True``, which in legacy browsers effectively disables
    same-origin protection. Refusing to start is safer than shipping a silent
    vulnerability.
    """
    if CORS_CREDENTIALS and "*" in CORS_ORIGINS:
        raise RuntimeError(
            "Insecure CORS config: cannot combine CCS_CORS_ORIGINS='*' "
            "with CCS_CORS_CREDENTIALS=true. Provide an explicit allowlist."
        )


HOST = os.getenv("CCS_HOST", "0.0.0.0")
PORT = int(os.getenv("CCS_PORT", "8000"))

# Optional API-key protection. Empty string → disabled.
API_KEY = os.getenv("CCS_API_KEY", "").strip()

# Per-IP rate limit: "N/SECONDS" e.g. "30/60" (30 req per minute).
RATE_LIMIT = os.getenv("CCS_RATE_LIMIT", "30/60")

# Session TTL (seconds). Older sessions are purged on the next write.
SESSION_TTL_SECONDS = int(os.getenv("CCS_SESSION_TTL_SECONDS", "604800"))
