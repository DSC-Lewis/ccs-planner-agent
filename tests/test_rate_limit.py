"""TS-12 · Per-IP rate limit on write endpoints (FR-8)."""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def reset_app():
    keys = ["CCS_RATE_LIMIT"]
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
        except Exception:  # pragma: no cover
            pass


def _fresh_client(env: dict):
    for k, v in env.items():
        os.environ[k] = v
    import app.config, app.main
    importlib.reload(app.config)
    importlib.reload(app.main)
    from fastapi.testclient import TestClient
    from app.services import rate_limit, storage
    rate_limit.reset()
    # Re-seed the default admin (the reload wiped app.main's middleware state
    # but the conftest fixture already provisioned the user once; make sure
    # it still exists).
    if not storage.get_user_by_name("default"):
        storage.ensure_admin(name="default", api_key="__test_default_key__")
    c = TestClient(app.main.app)
    c.headers.update({"X-API-Key": "__test_default_key__"})
    return c


def test_within_limit_all_pass(reset_app):
    c = _fresh_client({"CCS_RATE_LIMIT": "5/10"})
    for _ in range(5):
        r = c.post("/api/sessions", json={"mode": "manual"})
        assert r.status_code == 200, r.text


def test_exceeding_limit_returns_429_with_retry_after(reset_app):
    c = _fresh_client({"CCS_RATE_LIMIT": "5/10"})
    for _ in range(5):
        assert c.post("/api/sessions", json={"mode": "manual"}).status_code == 200
    r = c.post("/api/sessions", json={"mode": "manual"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) > 0


def test_get_endpoints_are_not_rate_limited(reset_app):
    c = _fresh_client({"CCS_RATE_LIMIT": "2/60"})
    for _ in range(20):
        r = c.get("/api/reference/surveys")
        assert r.status_code == 200


def test_default_limit_does_not_break_existing_tests(reset_app):
    c = _fresh_client({"CCS_RATE_LIMIT": "30/60"})
    r = c.post("/api/sessions", json={"mode": "manual"})
    assert r.status_code == 200
    sid = r.json()["session"]["id"]
    for payload in [
        {"survey_id": "tw_2025", "client_id": "internal_pitch"},
        {"project_name": "rate-test", "start_date": "2026-02-16", "weeks": 4},
        {"target_ids": ["all_adults"]},
    ]:
        assert c.post(f"/api/sessions/{sid}/advance", json=payload).status_code == 200
