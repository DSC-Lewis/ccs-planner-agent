"""Walk the automatic flow end-to-end and verify the saved Plan 2."""
from app.schemas import SessionMode, StepKey, StepPayload
from app.services import agent, storage


def _adv(s, **p):
    return agent.advance(s, StepPayload(**p))


def test_automatic_flow_distributes_budget_respecting_constraints():
    session = agent.create_session(SessionMode.AUTOMATIC)

    session = _adv(session, survey_id="tw_2025", client_id="internal_pitch")
    session = _adv(session, project_name="auto test", start_date="2026-02-16", weeks=4)
    session = _adv(session, target_ids=["all_adults"])
    session = _adv(session, planning_type="Comm")
    session = _adv(session, comms={
        "brand_strength": 7, "parent_brand": 4, "competitor_clutter": 6,
        "new_creative": 6, "message_complexity": 5,
        "kpi_ids": ["brand_consideration", "attitude_measures", "brand_knowledge_scores"],
    })
    session = _adv(session, channel_ids=[
        "tv_advertising", "youtube_video_ads", "meta_video_ads",
    ])
    assert session.step == StepKey.CRITERION

    session = _adv(session, criterion_id="net_reach", strategy_id="global_plan")
    assert session.step == StepKey.BUDGET_CHANNELS

    session = _adv(
        session,
        total_budget_twd=6_000_000,
        mandatory_channel_ids=["tv_advertising", "meta_video_ads"],
        optional_channel_ids=["youtube_video_ads"],
    )
    assert session.step == StepKey.MIN_MAX

    session = _adv(session, constraints={
        "tv_advertising": {"min_budget": 500_000, "max_budget": 2_000_000},
    })
    assert session.step == StepKey.OPTIMIZE

    session = _adv(session)  # triggers optimizer
    assert session.plan_id
    plan = storage.get_plan(session.plan_id)
    assert plan.kind == "Automatic"

    total = sum(a.total_budget_twd for a in plan.allocations)
    assert abs(total - 6_000_000) / 6_000_000 < 0.01

    tv = next(a for a in plan.allocations if a.channel_id == "tv_advertising")
    assert 500_000 * 0.95 <= tv.total_budget_twd <= 2_000_000 * 1.05

    assert plan.summary.net_reach_pct > 0
    assert plan.summary.total_impressions > 0


def test_budget_sweep_yields_monotonic_reach():
    """As budget increases the net reach should not fall."""
    from app.services import optimizer
    from app.schemas import AutomaticPlanInput, Brief

    brief = Brief(channel_ids=["tv_advertising", "youtube_video_ads", "meta_video_ads"])
    auto = AutomaticPlanInput(
        total_budget_twd=6_000_000,
        mandatory_channel_ids=["tv_advertising", "meta_video_ads"],
        optional_channel_ids=["youtube_video_ads"],
    )
    curve = optimizer.budget_step_curve(brief, auto, step_count=10)
    reaches = [r["net_reach_pct"] for r in curve]
    assert len(reaches) == 10
    for a, b in zip(reaches, reaches[1:]):
        assert b + 0.01 >= a, f"net reach regressed: {a} -> {b}"
