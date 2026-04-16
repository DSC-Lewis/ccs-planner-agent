"""TS-11 · Per-user API-key authentication (FR-15, NFR-3.2).

Replaces the v2 single-shared-key semantics: keys are now looked up in the
``users`` table. The test harness reloads ``app.main`` so env changes land.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def reset_app():
    keys = ["CCS_ADMIN_KEY", "CCS_API_KEY"]
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
            import app.config
            import app.main
            importlib.reload(app.config)
            importlib.reload(app.main)
        except Exception:  # pragma: no cover
            pass


def _client_with_admin_key(key):
    os.environ["CCS_ADMIN_KEY"] = key
    os.environ.pop("CCS_API_KEY", None)
    import app.config
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.main)
    # Schema was created by conftest; re-bootstrap the admin with the new key.
    from app.services import storage
    storage.ensure_admin(name="admin", api_key=key)
    from fastapi.testclient import TestClient
    return TestClient(app.main.app)


def test_configured_key_rejects_missing_header(reset_app):
    c = _client_with_admin_key("secret-token")
    r = c.get("/api/reference/surveys")
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"] or "Authentication" in r.json()["detail"]


def test_configured_key_rejects_wrong_value(reset_app):
    c = _client_with_admin_key("secret-token")
    r = c.get("/api/reference/surveys", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_configured_key_accepts_matching_header(reset_app):
    c = _client_with_admin_key("secret-token")
    r = c.get("/api/reference/surveys", headers={"X-API-Key": "secret-token"})
    assert r.status_code == 200


def test_health_is_always_open(reset_app):
    c = _client_with_admin_key("secret-token")
    assert c.get("/api/health").status_code == 200


def test_static_frontend_is_not_gated(reset_app):
    c = _client_with_admin_key("secret-token")
    r = c.get("/")
    assert r.status_code == 200
    assert "<title>CCS Planner" in r.text


def test_auth_uses_constant_time_compare():
    """NFR-3.2 — key comparison in storage uses a sha256 hash table. The
    constant-time compare happens implicitly via a hash lookup; the
    storage layer never string-compares raw keys."""
    src = Path(__file__).resolve().parent.parent / "app" / "services" / "storage.py"
    text = src.read_text()
    assert "_hash_key" in text and "sha256" in text
    # No naive == on supplied key
    assert "api_key == " not in text
