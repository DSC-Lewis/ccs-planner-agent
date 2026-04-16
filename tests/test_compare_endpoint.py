"""TS-16 · /api/plans/compare enriched payload (FR-12)."""
from __future__ import annotations

from app.schemas import SessionMode, StepPayload
from app.services import agent, storage


def _build_plan(owner_id: str, mode: SessionMode, weeks: int = 4) -> str:
    """Drive a full session and return the saved plan_id."""
    s = agent.create_session(mode, owner_id=owner_id)

    def adv(payload):
        return agent.advance(s, payload, owner_id=owner_id)

    s = adv(StepPayload(survey_id="tw_2025", client_id="internal_pitch"))
    s = adv(StepPayload(project_name="compare-test", start_date="2026-02-16", weeks=weeks))
    s = adv(StepPayload(target_ids=["all_adults"]))
    s = adv(StepPayload(planning_type="Reach"))
    s = adv(StepPayload(channel_ids=["tv_advertising", "youtube_video_ads", "meta_video_ads"]))
    if mode == SessionMode.MANUAL:
        s = adv(StepPayload())  # calibration
        s = adv(StepPayload(weekly_budgets={
            "tv_advertising": [2500] * weeks,
            "youtube_video_ads": [125000] * weeks,
            "meta_video_ads": [100000] * weeks,
        }))
    else:
        s = adv(StepPayload(criterion_id="net_reach", strategy_id="global_plan"))
        s = adv(StepPayload(
            total_budget_twd=6_000_000,
            mandatory_channel_ids=["tv_advertising", "meta_video_ads"],
            optional_channel_ids=["youtube_video_ads"]))
        s = adv(StepPayload(constraints={}))
        s = adv(StepPayload())  # optimize
    return s.plan_id


def test_compare_includes_frequency_distribution(client, owner_id):
    p1 = _build_plan(owner_id, SessionMode.MANUAL)
    p2 = _build_plan(owner_id, SessionMode.AUTOMATIC)
    r = client.post("/api/plans/compare", json=[p1, p2])
    assert r.status_code == 200
    body = r.json()
    for plan in body["plans"]:
        fd = plan.get("frequency_distribution")
        assert fd, "every plan must carry frequency_distribution"
        assert len(fd) == 10
        assert {"threshold", "reach_pct"} <= set(fd[0].keys())


def test_compare_includes_duplication(client, owner_id):
    p1 = _build_plan(owner_id, SessionMode.MANUAL)
    p2 = _build_plan(owner_id, SessionMode.AUTOMATIC)
    r = client.post("/api/plans/compare", json=[p1, p2])
    body = r.json()
    for plan in body["plans"]:
        dup = plan.get("duplication")
        assert dup, "every plan must carry duplication matrix"
        # all allocation ids should appear as keys
        ids = {a["channel_id"] for a in plan["allocations"]}
        assert ids.issubset(dup.keys())


def test_compare_includes_weekly_grp(client, owner_id):
    p1 = _build_plan(owner_id, SessionMode.MANUAL)
    p2 = _build_plan(owner_id, SessionMode.AUTOMATIC)
    r = client.post("/api/plans/compare", json=[p1, p2])
    body = r.json()
    for plan in body["plans"]:
        wg = plan.get("weekly_grp")
        assert wg and len(wg) == 4
        for row in wg:
            assert set(row.keys()) == {"week", "grp"}


def test_compare_preserves_legacy_fields(client, owner_id):
    p1 = _build_plan(owner_id, SessionMode.MANUAL)
    p2 = _build_plan(owner_id, SessionMode.AUTOMATIC)
    r = client.post("/api/plans/compare", json=[p1, p2])
    body = r.json()
    assert "plans" in body
    assert "delta" in body
    assert set(body["delta"].keys()) >= {
        "total_budget_twd", "net_reach_pct", "frequency", "total_impressions"
    }


def test_compare_requires_two_plans(client, owner_id):
    p1 = _build_plan(owner_id, SessionMode.MANUAL)
    r = client.post("/api/plans/compare", json=[p1])
    assert r.status_code == 400
