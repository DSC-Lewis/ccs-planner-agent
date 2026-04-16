"""TS-8 · CORS startup guard (NFR-1.2).

The combination ``allow_origins=["*"]`` + ``allow_credentials=True`` is a
known CORS footgun. The app must refuse to build with that combination.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def reset_app_modules():
    """Snapshot + restore env vars AND module state so teardown is idempotent.

    Why a fixture: every test in this suite mutates ``CCS_CORS_ORIGINS`` /
    ``CCS_CORS_CREDENTIALS`` and calls ``importlib.reload(app.main)`` to make
    the change take effect. Without this fixture a test that errors before
    ``yield`` would leave subsequent test modules with a stale (possibly
    insecure) ``app.main``.
    """
    keys = ["CCS_CORS_ORIGINS", "CCS_CORS_CREDENTIALS"]
    before = {k: os.environ.get(k) for k in keys}
    try:
        yield
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Reload so later test modules observe the default (safe) config.
        # Swallow errors so teardown can't mask the test's own failure.
        try:
            import app.config
            import app.main
            importlib.reload(app.config)
            importlib.reload(app.main)
        except Exception:  # pragma: no cover — best-effort cleanup
            pass


def _reimport_with_env(env: dict) -> None:
    for k, v in env.items():
        os.environ[k] = v
    import app.config
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.main)


def test_wildcard_plus_credentials_refuses_to_start(reset_app_modules):
    """TC-8.1 — this combination is insecure and must fail fast."""
    os.environ["CCS_CORS_ORIGINS"] = "*"
    os.environ["CCS_CORS_CREDENTIALS"] = "true"
    with pytest.raises(RuntimeError, match="CORS"):
        _reimport_with_env({})


def test_explicit_allowlist_with_credentials_is_fine(reset_app_modules):
    """TC-8.2 — named origins may use credentials safely."""
    os.environ["CCS_CORS_ORIGINS"] = "https://a.example,https://b.example"
    os.environ["CCS_CORS_CREDENTIALS"] = "true"
    _reimport_with_env({})
    from app.main import app
    from fastapi.testclient import TestClient
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200


def test_wildcard_without_credentials_is_fine(reset_app_modules):
    """TC-8.3 — public APIs without cookies are OK with *."""
    os.environ["CCS_CORS_ORIGINS"] = "*"
    os.environ.pop("CCS_CORS_CREDENTIALS", None)
    _reimport_with_env({})
    from app.main import app
    from fastapi.testclient import TestClient
    assert TestClient(app).get("/api/health").status_code == 200
