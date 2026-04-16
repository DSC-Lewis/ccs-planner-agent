"""TS-25 · Admin endpoints for user management (FR-23, FR-24, FR-25)."""
from __future__ import annotations


def _non_admin_client(name: str, key: str):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import storage
    u = storage.create_user(name=name, api_key=key, is_admin=False)
    c = TestClient(app)
    c.headers.update({"X-API-Key": key})
    return c, u


def test_list_users_admin_only(client):
    r = client.get("/api/users")
    assert r.status_code == 200
    body = r.json()
    assert any(u["name"] == "default" for u in body)
    # No secrets leaked
    for u in body:
        assert "api_key" not in u and "api_key_hash" not in u


def test_list_users_denied_to_non_admin(client):
    bob, _ = _non_admin_client("bob", "bobkey")
    r = bob.get("/api/users")
    assert r.status_code == 403


def test_disable_user_revokes_key_immediately(client):
    # Admin mints a user
    mint = client.post("/api/users", json={"name": "charlie"}).json()
    key = mint["api_key"]
    cid = mint["user"]["id"]

    # Charlie can call /api/me
    from fastapi.testclient import TestClient
    from app.main import app
    charlie = TestClient(app)
    charlie.headers.update({"X-API-Key": key})
    assert charlie.get("/api/me").status_code == 200

    # Admin disables Charlie
    r = client.post(f"/api/users/{cid}/disable")
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # Charlie's next call is unauthorized — NO server restart needed.
    assert charlie.get("/api/me").status_code == 401


def test_enable_user_restores_access(client):
    mint = client.post("/api/users", json={"name": "dana"}).json()
    key = mint["api_key"]; did = mint["user"]["id"]

    client.post(f"/api/users/{did}/disable")
    client.post(f"/api/users/{did}/enable")

    from fastapi.testclient import TestClient
    from app.main import app
    dana = TestClient(app)
    dana.headers.update({"X-API-Key": key})
    assert dana.get("/api/me").status_code == 200


def test_rotate_returns_new_key_and_invalidates_old(client):
    mint = client.post("/api/users", json={"name": "evan"}).json()
    old_key = mint["api_key"]; eid = mint["user"]["id"]

    r = client.post(f"/api/users/{eid}/rotate")
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert new_key and new_key != old_key

    from fastapi.testclient import TestClient
    from app.main import app
    evan_old = TestClient(app)
    evan_old.headers.update({"X-API-Key": old_key})
    assert evan_old.get("/api/me").status_code == 401

    evan_new = TestClient(app)
    evan_new.headers.update({"X-API-Key": new_key})
    assert evan_new.get("/api/me").status_code == 200


def test_admin_cannot_disable_self(client):
    """Safety rail — the only admin locking themselves out bricks the system."""
    me = client.get("/api/me").json()
    r = client.post(f"/api/users/{me['id']}/disable")
    assert r.status_code == 422
    assert "self" in r.json()["detail"].lower()


def test_disable_unknown_user_returns_404(client):
    r = client.post("/api/users/usr_doesnotexist/disable")
    assert r.status_code == 404
