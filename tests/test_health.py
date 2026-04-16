"""Code-review #4: /api/health should probe the DB, not just return a
static string. Liveness that ignores dependencies is worse than useless."""
from __future__ import annotations


def test_health_reports_db_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body.get("db") == "ok", f"expected db=ok, got: {body}"


def test_health_returns_503_when_db_unreachable(monkeypatch, client):
    """Simulate a broken DB by pointing the connection at an unwritable
    path. The probe must fail cleanly rather than lying about liveness."""
    from app.services import storage

    def _broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage, "_count", _broken)
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["db"] == "error"
