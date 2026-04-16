"""Shared pytest fixtures: wipe storage + rate-limit state between tests."""
import os
import tempfile
from pathlib import Path

import pytest

# Point storage at a throwaway DB BEFORE the app is imported.
_TMP = Path(tempfile.mkdtemp(prefix="ccs_test_"))
os.environ["CCS_STORAGE_PATH"] = str(_TMP / "storage.json")
os.environ["CCS_DATABASE_PATH"] = str(_TMP / "ccs.db")


@pytest.fixture(autouse=True)
def clean_storage():
    from app.services import rate_limit, storage
    storage.reset()
    rate_limit.reset()
    # Every test starts with a default admin user + default project so the
    # pre-v4 tests that don't know about users still work end-to-end.
    store_default_user()
    yield
    storage.reset()
    rate_limit.reset()


def store_default_user():
    from app.services import storage
    if storage.get_user_by_name("default"):
        return
    storage.ensure_admin(name="default", api_key="__test_default_key__")


@pytest.fixture
def default_user():
    from app.services import storage
    return storage.get_user_by_name("default")


@pytest.fixture
def owner_id(default_user):
    """Most service calls just need a user id — this shortcuts it."""
    return default_user.id


@pytest.fixture
def project_id(owner_id):
    from app.services import storage
    return storage.ensure_default_project(owner_id).id


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    # Auto-inject admin key so existing tests that don't know about auth
    # keep working against the v4 auth middleware.
    c.headers.update({"X-API-Key": "__test_default_key__"})
    return c
