"""TS-21 · Users + per-user scoping (FR-15 / NFR-5.2)."""
from __future__ import annotations


def test_me_returns_current_user(client):
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["name"] == "default"


def test_admin_can_create_user_and_key_is_returned_once(client):
    r = client.post("/api/users", json={"name": "alice"})
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["name"] == "alice"
    assert isinstance(body["api_key"], str) and len(body["api_key"]) >= 20


def test_non_admin_cannot_create_users(client):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import storage
    storage.ensure_admin(name="nonadmin", api_key="nk")
    # Downgrade them to non-admin by direct storage (real deployments would
    # use a dedicated demote endpoint; this is a testing shortcut).
    from app.services.storage import _conn
    _conn().execute("UPDATE users SET is_admin = 0 WHERE name = 'nonadmin'")

    c = TestClient(app)
    c.headers.update({"X-API-Key": "nk"})
    r = c.post("/api/users", json={"name": "bob"})
    assert r.status_code == 403


def test_plain_keys_are_never_stored():
    """NFR-5.2 — the `users` table must only ever hold the hash."""
    from app.services import storage
    # Create a user with a known key.
    storage.create_user(name="hashed", api_key="super-secret-plain")
    rows = storage._conn().execute(
        "SELECT api_key_hash FROM users WHERE name = 'hashed'"
    ).fetchall()
    stored = rows[0]["api_key_hash"]
    assert stored != "super-secret-plain"
    assert len(stored) == 64  # sha256 hex


def test_two_users_cannot_see_each_others_data(client):
    """NFR-5.2 — data isolation at the service layer."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import storage

    # Alice (the default admin) creates a project + session.
    alice_proj = client.post("/api/projects", json={"name": "Alice-only"}).json()
    alice_sess = client.post("/api/sessions",
                             json={"mode": "manual",
                                   "project_id": alice_proj["id"]}).json()
    alice_sid = alice_sess["session"]["id"]

    # Bob logs in separately.
    storage.ensure_admin(name="bob", api_key="bob-key")
    bob = TestClient(app)
    bob.headers.update({"X-API-Key": "bob-key"})

    assert bob.get(f"/api/sessions/{alice_sid}").status_code == 404
    assert bob.get(f"/api/projects/{alice_proj['id']}").status_code == 404
    assert bob.get("/api/projects").json() == []  # Bob has his own empty list
