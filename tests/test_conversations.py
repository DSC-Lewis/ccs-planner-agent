"""TS-20 · Conversation log (FR-19 / NFR-5.4)."""
from __future__ import annotations


def _seed_session(client):
    return client.post("/api/sessions", json={"mode": "manual"}).json()["session"]["id"]


def _adv(client, sid, payload):
    return client.post(f"/api/sessions/{sid}/advance", json=payload)


def test_each_advance_appends_one_turn(client):
    sid = _seed_session(client)
    _adv(client, sid, {"survey_id": "tw_2025", "client_id": "internal_pitch"})
    _adv(client, sid, {"project_name": "t", "start_date": "2026-02-16", "weeks": 4})
    r = client.get(f"/api/sessions/{sid}/conversation")
    assert r.status_code == 200
    turns = r.json()
    assert len(turns) == 2
    assert turns[0]["turn_index"] == 0
    assert turns[1]["turn_index"] == 1


def test_turn_carries_full_brief_snapshot(client):
    sid = _seed_session(client)
    _adv(client, sid, {"survey_id": "tw_2025", "client_id": "internal_pitch"})
    _adv(client, sid, {"project_name": "snap", "start_date": "2026-02-16", "weeks": 4})
    turns = client.get(f"/api/sessions/{sid}/conversation").json()
    snap = turns[-1]["brief_snapshot"]
    # The snapshot must include every Brief field, populated with the
    # latest state after the advance.
    assert snap["survey_id"] == "tw_2025"
    assert snap["client_id"] == "internal_pitch"
    assert snap["project_name"] == "snap"
    assert snap["weeks"] == 4
    assert "target_ids" in snap and "channel_ids" in snap


def test_conversation_is_scoped_to_owner(client):
    """Another user must not see this session's conversation."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import storage

    sid = _seed_session(client)
    _adv(client, sid, {"survey_id": "tw_2025", "client_id": "internal_pitch"})

    storage.ensure_admin(name="mallory", api_key="mallory-key")
    mallory = TestClient(app)
    mallory.headers.update({"X-API-Key": "mallory-key"})
    r = mallory.get(f"/api/sessions/{sid}/conversation")
    assert r.status_code == 404


def test_failed_advance_does_not_log_a_turn(client):
    sid = _seed_session(client)
    # Missing survey_id → 400, nothing should land in the conversation.
    r = client.post(f"/api/sessions/{sid}/advance", json={"client_id": "internal_pitch"})
    assert r.status_code == 400
    turns = client.get(f"/api/sessions/{sid}/conversation").json()
    assert turns == []


def test_conversation_scrubs_sensitive_keys(client):
    """NFR-5.4 — even if someone jams a top-level `api_key` into a payload
    (which our schema doesn't allow but let's prove it wouldn't leak)."""
    from app.services import storage
    sid = _seed_session(client)
    # Log directly (bypassing the HTTP surface) to test the scrub.
    storage.log_turn(session_id=sid, step="survey_client",
                     payload={"client_id": "x", "api_key": "LEAK"},
                     prompt="p", brief_snapshot={})
    turns = storage.get_conversation(
        sid, owner_id=client.headers["X-API-Key"] and
        storage.get_user_by_name("default").id)
    assert all("api_key" not in t.payload for t in turns)


def test_conversation_ordered_by_turn_index(client):
    sid = _seed_session(client)
    _adv(client, sid, {"survey_id": "tw_2025", "client_id": "internal_pitch"})
    _adv(client, sid, {"project_name": "ordered", "start_date": "2026-02-16", "weeks": 4})
    _adv(client, sid, {"target_ids": ["all_adults"]})
    turns = client.get(f"/api/sessions/{sid}/conversation").json()
    indices = [t["turn_index"] for t in turns]
    assert indices == sorted(indices)
