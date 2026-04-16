"""TS-19 · Projects CRUD + session scoping (FR-17 / FR-18)."""
from __future__ import annotations


def test_create_project_returns_201(client):
    r = client.post("/api/projects", json={"name": "Apple Pitch"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Apple Pitch"
    assert body["owner_id"]


def test_list_projects_only_returns_caller_projects(client):
    client.post("/api/projects", json={"name": "Project A"})
    client.post("/api/projects", json={"name": "Project B"})
    r = client.get("/api/projects")
    assert r.status_code == 200
    names = sorted(p["name"] for p in r.json())
    assert names == ["Project A", "Project B"]


def test_cross_user_access_is_blocked(client):
    """Another user's project returns 404 (we choose 404 over 403 to avoid
    leaking project existence)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import storage

    storage.ensure_admin(name="bob", api_key="bob-key")
    alice_proj = client.post("/api/projects", json={"name": "Alice"}).json()

    bob = TestClient(app)
    bob.headers.update({"X-API-Key": "bob-key"})
    r = bob.get(f"/api/projects/{alice_proj['id']}")
    assert r.status_code == 404


def test_archive_soft_deletes(client):
    p = client.post("/api/projects", json={"name": "Temp"}).json()
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    names = [x["name"] for x in client.get("/api/projects").json()]
    assert "Temp" not in names


def test_create_session_defaults_to_default_project(client):
    r = client.post("/api/sessions", json={"mode": "manual"})
    assert r.status_code == 200
    # The default project should now exist for this user.
    projects = client.get("/api/projects").json()
    assert any(p["name"] == "Default" for p in projects)


def test_create_session_with_explicit_project(client):
    proj = client.post("/api/projects", json={"name": "X"}).json()
    r = client.post("/api/sessions",
                    json={"mode": "manual", "project_id": proj["id"]})
    assert r.status_code == 200
    # The session should list under that project.
    sessions = client.get(f"/api/projects/{proj['id']}/sessions").json()
    assert len(sessions) == 1


def test_project_detail_has_session_and_plan_counts(client):
    proj = client.post("/api/projects", json={"name": "Counts"}).json()
    client.post("/api/sessions", json={"mode": "manual", "project_id": proj["id"]})
    detail = client.get(f"/api/projects/{proj['id']}").json()
    assert detail["session_count"] == 1
    assert detail["plan_count"] == 0
