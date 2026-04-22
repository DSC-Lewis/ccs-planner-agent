"""v6 gap-audit · Issue 19 — admin guard on global calibration settings.

PRD §FR-34 reads "Global defaults (read-only unless admin)". This test
suite locks down that semantic at the endpoint layer:

* PUT /api/calibration/settings with scope="global" → requires admin (403 otherwise).
* DELETE /api/calibration/settings?scope=global    → requires admin.
* PUT/DELETE with scope="client" or scope="channel" → open to any
  authenticated user (tuning their own tenant is fine).

The data model stays per-owner (each user's own settings row) so there's
no cross-tenant leak via admin writes. The guard is purely "who can
touch the global bucket".
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import storage


def _non_admin_client(name: str, key: str) -> TestClient:
    """Create a fresh non-admin user and return an authenticated client."""
    storage.create_user(name=name, api_key=key, is_admin=False)
    c = TestClient(app)
    c.headers.update({"X-API-Key": key})
    return c


# ---------- PUT guard ----------

def test_put_global_scope_requires_admin():
    nd = _non_admin_client("non_admin_put", "nd-put-key")
    r = nd.put(
        "/api/calibration/settings",
        json={"scope": "global", "half_life_days": 60},
    )
    assert r.status_code == 403, (
        f"Non-admin must be blocked from PUT scope=global; got {r.status_code}"
    )
    detail = r.json().get("detail", "")
    assert "admin" in detail.lower() or "global" in detail.lower()


def test_put_global_scope_allowed_for_admin(client: TestClient):
    # The default test client is admin (see conftest).
    r = client.put(
        "/api/calibration/settings",
        json={"scope": "global", "half_life_days": 60},
    )
    assert r.status_code == 200
    assert client.get("/api/calibration/settings").json()["global"]["half_life_days"] == 60


def test_put_client_scope_allowed_for_non_admin():
    """Non-admins must still be able to tune their own per-client scope."""
    nd = _non_admin_client("non_admin_client_put", "nd-c-key")
    r = nd.put(
        "/api/calibration/settings",
        json={
            "scope": "client",
            "client_id": "internal_pitch",
            "half_life_days": 45,
        },
    )
    assert r.status_code == 200


def test_put_channel_scope_allowed_for_non_admin():
    nd = _non_admin_client("non_admin_ch_put", "nd-ch-key")
    r = nd.put(
        "/api/calibration/settings",
        json={
            "scope": "channel",
            "client_id": "internal_pitch",
            "target_id": "all_adults",
            "channel_id": "tv_advertising",
            "half_life_days": 30,
        },
    )
    assert r.status_code == 200


# ---------- DELETE guard ----------

def test_delete_global_scope_requires_admin():
    nd = _non_admin_client("non_admin_del", "nd-del-key")
    r = nd.delete("/api/calibration/settings?scope=global")
    assert r.status_code == 403


def test_delete_global_scope_allowed_for_admin(client: TestClient):
    # Seed a global override first so there's something to delete.
    client.put(
        "/api/calibration/settings",
        json={"scope": "global", "half_life_days": 90},
    )
    r = client.delete("/api/calibration/settings?scope=global")
    assert r.status_code == 200


def test_delete_client_scope_allowed_for_non_admin():
    nd = _non_admin_client("non_admin_client_del", "nd-c-del-key")
    # Seed our own per-client row first.
    nd.put(
        "/api/calibration/settings",
        json={
            "scope": "client",
            "client_id": "internal_pitch",
            "half_life_days": 45,
        },
    )
    r = nd.delete(
        "/api/calibration/settings?scope=client&client_id=internal_pitch"
    )
    assert r.status_code == 200


# ---------- Thresholds are part of the "global" bucket ----------

def test_put_thresholds_requires_admin():
    """Confidence thresholds travel on the same endpoint as global half_life
    and are explicitly "global" in nature — same admin guard applies."""
    nd = _non_admin_client("non_admin_thresh", "nd-thresh-key")
    r = nd.put(
        "/api/calibration/settings",
        json={"scope": "global", "thresholds": {"high": 80, "mid": 50}},
    )
    assert r.status_code == 403


# ---------- GET is readable by everyone ----------

def test_get_settings_readable_by_non_admin():
    nd = _non_admin_client("non_admin_get", "nd-get-key")
    r = nd.get("/api/calibration/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["global"]["half_life_days"] == 180  # default
