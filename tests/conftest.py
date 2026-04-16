"""Shared pytest fixtures: wipe the demo storage between tests."""
import os
import tempfile
from pathlib import Path

import pytest

# Point storage at a throwaway file BEFORE the app is imported.
_TMP = Path(tempfile.mkdtemp(prefix="ccs_test_"))
os.environ["CCS_STORAGE_PATH"] = str(_TMP / "storage.json")


@pytest.fixture(autouse=True)
def clean_storage():
    from app.services import storage
    storage.reset()
    yield
    storage.reset()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
