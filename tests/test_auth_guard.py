"""TS-11 · Optional API-key authentication (FR-7 / NFR-3.2)."""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def reset_app():
    """Snapshot env + reload app modules so env-var changes take effect."""
    keys = ["CCS_API_KEY"]
    before = {k: os.environ.get(k) for k in keys}
    try:
        yield
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            import app.config, app.main
            importlib.reload(app.config)
            importlib.reload(app.main)
        except Exception:  # pragma: no cover — best-effort cleanup
            pass


def _client_with_env(env: dict):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import app.config, app.main
    importlib.reload(app.config)
    importlib.reload(app.main)
    from fastapi.testclient import TestClient
    return TestClient(app.main.app)


def test_unset_key_leaves_endpoints_open(reset_app):
    c = _client_with_env({"CCS_API_KEY": None})
    assert c.get("/api/reference/surveys").status_code == 200


def test_configured_key_rejects_missing_header(reset_app):
    c = _client_with_env({"CCS_API_KEY": "secret-token"})
    r = c.get("/api/reference/surveys")
    assert r.status_code == 401
    assert "API key" in r.json()["detail"]


def test_configured_key_rejects_wrong_value(reset_app):
    c = _client_with_env({"CCS_API_KEY": "secret-token"})
    r = c.get("/api/reference/surveys", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_configured_key_accepts_matching_header(reset_app):
    c = _client_with_env({"CCS_API_KEY": "secret-token"})
    r = c.get("/api/reference/surveys", headers={"X-API-Key": "secret-token"})
    assert r.status_code == 200


def test_health_is_always_open(reset_app):
    """Probe-friendly: load balancers / Docker HEALTHCHECK need this."""
    c = _client_with_env({"CCS_API_KEY": "secret-token"})
    assert c.get("/api/health").status_code == 200


def test_static_frontend_is_not_gated(reset_app):
    c = _client_with_env({"CCS_API_KEY": "secret-token"})
    r = c.get("/")
    assert r.status_code == 200
    assert "<title>CCS Planner" in r.text


def test_auth_uses_constant_time_compare():
    """NFR-3.2 — grep the auth module for secrets.compare_digest."""
    p = Path(__file__).resolve().parent.parent / "app" / "services" / "auth.py"
    assert p.exists(), "app/services/auth.py should expose the API-key guard"
    src = p.read_text()
    assert "secrets.compare_digest" in src, (
        "Use secrets.compare_digest to avoid timing attacks on the key."
    )
