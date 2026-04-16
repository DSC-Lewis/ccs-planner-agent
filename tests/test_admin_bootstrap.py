"""TS-22 · Admin bootstrap + CCS_API_KEY backward compat (FR-16)."""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def reset_env():
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
            import app.config, app.main
            importlib.reload(app.config)
            importlib.reload(app.main)
        except Exception:  # pragma: no cover
            pass


def _fresh(env: dict, tmp_path):
    for k in ["CCS_ADMIN_KEY", "CCS_API_KEY"]:
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v
    os.environ["CCS_DATABASE_PATH"] = str(tmp_path / "ccs.db")
    os.environ["CCS_STORAGE_PATH"] = str(tmp_path / "legacy.json")
    import app.config, app.main, app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    importlib.reload(app.main)
    # FastAPI's TestClient triggers startup events.
    from fastapi.testclient import TestClient
    with TestClient(app.main.app) as c:
        return c, store


def test_ccs_admin_key_creates_admin_on_boot(reset_env, tmp_path):
    c, store = _fresh({"CCS_ADMIN_KEY": "admin-token-abc"}, tmp_path)
    admin = store.get_user_by_name("admin")
    assert admin is not None
    assert admin.is_admin is True
    # That key should work end-to-end.
    r = c.get("/api/me", headers={"X-API-Key": "admin-token-abc"})
    assert r.status_code == 200
    assert r.json()["name"] == "admin"


def test_legacy_ccs_api_key_also_creates_admin(reset_env, tmp_path):
    c, store = _fresh({"CCS_API_KEY": "legacy-token"}, tmp_path)
    admin = store.get_user_by_name("admin")
    assert admin is not None
    r = c.get("/api/me", headers={"X-API-Key": "legacy-token"})
    assert r.status_code == 200


def test_no_env_key_leaves_admin_unset(reset_env, tmp_path):
    c, store = _fresh({}, tmp_path)
    # No admin user was auto-created.
    assert store.get_user_by_name("admin") is None
    # Any /api/* call except health returns 401.
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/health").status_code == 200
