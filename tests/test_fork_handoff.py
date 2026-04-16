"""Verify that a Manual session's Brief can be forked into an Automatic one
and produce a valid Plan 2 without re-asking any shared questions."""
import pytest

from app.schemas import SessionMode, StepKey, StepPayload
from app.services import agent, storage


def _adv(s, owner_id, **p):
    return agent.advance(s, StepPayload(**p), owner_id=owner_id)


def _finish_manual(owner_id):
    s = agent.create_session(SessionMode.MANUAL, owner_id=owner_id)
    s = _adv(s, owner_id, survey_id="tw_2025", client_id="internal_pitch")
    s = _adv(s, owner_id, project_name="fork demo", start_date="2026-02-16", weeks=4)
    s = _adv(s, owner_id, target_ids=["all_adults", "ta_30_54_a"])
    s = _adv(s, owner_id, planning_type="Comm")
    s = _adv(s, owner_id, comms={
        "brand_strength": 6, "parent_brand": 5, "competitor_clutter": 5,
        "new_creative": 5, "message_complexity": 5,
        "kpi_ids": ["brand_consideration", "attitude_measures"]
    })
    s = _adv(s, owner_id, channel_ids=["tv_advertising", "youtube_video_ads", "meta_video_ads"])
    s = _adv(s, owner_id)  # calibration
    s = _adv(s, owner_id, weekly_budgets={
        "tv_advertising": [2500, 2500, 2500, 2500],
        "youtube_video_ads": [125000, 125000, 125000, 125000],
        "meta_video_ads": [100000, 100000, 100000, 100000],
    })
    return s


def test_fork_carries_brief_into_automatic_agent(owner_id):
    manual = _finish_manual(owner_id)
    assert manual.plan_id, "pre-condition: Plan 1 should exist"

    forked = agent.fork(manual, SessionMode.AUTOMATIC, owner_id=owner_id)

    assert forked.id != manual.id
    assert forked.mode == SessionMode.AUTOMATIC
    assert forked.brief.survey_id == "tw_2025"
    assert forked.brief.client_id == "internal_pitch"
    assert forked.brief.project_name == "fork demo"
    assert str(forked.brief.start_date) == "2026-02-16"
    assert forked.brief.weeks == 4
    assert forked.brief.target_ids == manual.brief.target_ids
    assert forked.brief.planning_type == manual.brief.planning_type
    assert forked.brief.comms.kpi_ids == manual.brief.comms.kpi_ids
    assert forked.brief.channel_ids == manual.brief.channel_ids
    assert forked.automatic_input.mandatory_channel_ids == manual.brief.channel_ids
    assert forked.step == StepKey.CRITERION

    f = _adv(forked, owner_id, criterion_id="net_reach", strategy_id="global_plan")
    f = _adv(f, owner_id, total_budget_twd=6_000_000,
             mandatory_channel_ids=["tv_advertising", "meta_video_ads"],
             optional_channel_ids=["youtube_video_ads"])
    f = _adv(f, owner_id, constraints={})
    f = _adv(f, owner_id)  # optimize
    assert f.plan_id and f.plan_id != manual.plan_id

    plan2 = storage.get_plan(f.plan_id, owner_id=owner_id)
    plan1 = storage.get_plan(manual.plan_id, owner_id=owner_id)
    assert plan2.kind == "Automatic"
    assert plan1.kind == "Manual"
    assert plan2.summary.net_reach_pct > plan1.summary.net_reach_pct


def test_fork_rejects_incomplete_source(owner_id):
    empty = agent.create_session(SessionMode.MANUAL, owner_id=owner_id)
    with pytest.raises(agent.StepError):
        agent.fork(empty, SessionMode.AUTOMATIC, owner_id=owner_id)


def test_fork_rejects_same_mode(owner_id):
    s = _finish_manual(owner_id)
    with pytest.raises(agent.StepError):
        agent.fork(s, SessionMode.MANUAL, owner_id=owner_id)


def test_fork_history_records_provenance(owner_id):
    s = _finish_manual(owner_id)
    f = agent.fork(s, SessionMode.AUTOMATIC, owner_id=owner_id)
    marker = next(h for h in f.history if h["step"] == "__forked_from__")
    assert marker["payload"]["source_session_id"] == s.id
    assert marker["payload"]["source_mode"] == "manual"
    assert marker["payload"]["source_plan_id"] == s.plan_id


def test_fork_over_http(client):
    r = client.post("/api/sessions", json={"mode": "manual"})
    sid = r.json()["session"]["id"]

    def adv(payload):
        return client.post(f"/api/sessions/{sid}/advance", json=payload).json()

    adv({"survey_id": "tw_2025", "client_id": "internal_pitch"})
    adv({"project_name": "http fork", "start_date": "2026-02-16", "weeks": 4})
    adv({"target_ids": ["all_adults"]})
    adv({"planning_type": "Reach"})
    adv({"channel_ids": ["tv_advertising", "meta_video_ads"]})

    fr = client.post(f"/api/sessions/{sid}/fork", json={"target_mode": "automatic"})
    assert fr.status_code == 200
    data = fr.json()
    assert data["session"]["mode"] == "automatic"
    assert data["session"]["step"] == "criterion"
    assert data["session"]["brief"]["channel_ids"] == ["tv_advertising", "meta_video_ads"]
