"""Smoke-test the HTTP surface through FastAPI's TestClient."""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_reference_endpoints(client):
    for path in [
        "/api/reference/surveys",
        "/api/reference/clients",
        "/api/reference/targets",
        "/api/reference/brand-kpis",
        "/api/reference/channels",
        "/api/reference/optimization",
    ]:
        r = client.get(path)
        assert r.status_code == 200, path


def test_manual_session_round_trip(client):
    r = client.post("/api/sessions", json={"mode": "manual"})
    assert r.status_code == 200
    sid = r.json()["session"]["id"]

    def adv(payload):
        rr = client.post(f"/api/sessions/{sid}/advance", json=payload)
        assert rr.status_code == 200, rr.text
        return rr.json()

    adv({"survey_id": "tw_2025", "client_id": "internal_pitch"})
    adv({"project_name": "api test", "start_date": "2026-02-16", "weeks": 4})
    adv({"target_ids": ["all_adults"]})
    adv({"planning_type": "Reach"})
    adv({"channel_ids": ["tv_advertising", "youtube_video_ads", "meta_video_ads"]})
    adv({})  # calibration
    final = adv({"weekly_budgets": {
        "tv_advertising": [2500, 2500, 2500, 2500],
        "youtube_video_ads": [125000, 125000, 125000, 125000],
        "meta_video_ads": [100000, 100000, 100000, 100000],
    }})
    assert final["completed"] is True
    assert final["plan"]["kind"] == "Manual"
    assert final["plan"]["summary"]["total_budget_twd"] == 910_000


def test_error_messages_are_user_facing(client):
    r = client.post("/api/sessions", json={"mode": "manual"})
    sid = r.json()["session"]["id"]
    # missing survey_id -> 400
    rr = client.post(f"/api/sessions/{sid}/advance", json={"client_id": "internal_pitch"})
    assert rr.status_code == 400
    assert "Survey" in rr.json()["detail"]
