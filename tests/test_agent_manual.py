"""Walk the manual flow end-to-end and verify the saved Plan 1."""
from app.schemas import SessionMode, StepKey
from app.services import agent, storage


def _advance(session, **payload):
    from app.schemas import StepPayload
    return agent.advance(session, StepPayload(**payload))


def test_manual_flow_produces_valid_plan():
    session = agent.create_session(SessionMode.MANUAL)

    session = _advance(session, survey_id="tw_2025", client_id="internal_pitch")
    assert session.step == StepKey.PROJECT_DATES

    session = _advance(session, project_name="test 260128",
                       start_date="2026-02-16", weeks=4)
    assert session.brief.project_name == "test 260128"
    assert str(session.brief.end_date) == "2026-03-15"

    session = _advance(session, target_ids=["all_adults", "ta_30_54_a"])
    assert session.step == StepKey.PLANNING_TYPE

    session = _advance(session, planning_type="Reach")
    # Reach skips comms_setup
    assert session.step == StepKey.CHANNELS

    session = _advance(session, channel_ids=[
        "tv_advertising", "youtube_video_ads", "meta_video_ads",
    ])
    assert session.step == StepKey.CALIBRATION

    session = _advance(session)
    assert session.step == StepKey.MANUAL_PLAN

    weekly = {
        "tv_advertising": [2500, 2500, 2500, 2500],
        "youtube_video_ads": [125000, 125000, 125000, 125000],
        "meta_video_ads": [100000, 100000, 100000, 100000],
    }
    session = _advance(session, weekly_budgets=weekly)
    assert session.step == StepKey.REVIEW
    assert session.plan_id

    plan = storage.get_plan(session.plan_id)
    assert plan is not None
    assert plan.kind == "Manual"
    # Total budget = (2500+125000+100000)*4 = 910,000
    assert plan.summary.total_budget_twd == 910_000
    assert plan.summary.total_impressions > 0
    assert 0 < plan.summary.net_reach_pct <= 100
    assert len(plan.allocations) == 3


def test_manual_flow_rejects_invalid_weeks():
    """Weeks <= 0 must be rejected — either by the schema (Pydantic) or the
    agent (StepError)."""
    import pytest
    from pydantic import ValidationError
    session = agent.create_session(SessionMode.MANUAL)
    session = _advance(session, survey_id="tw_2025", client_id="internal_pitch")
    with pytest.raises((agent.StepError, ValidationError, ValueError)):
        _advance(session, project_name="bad", weeks=0)


def test_manual_flow_rejects_unknown_channel():
    import pytest
    session = agent.create_session(SessionMode.MANUAL)
    session = _advance(session, survey_id="tw_2025", client_id="internal_pitch")
    session = _advance(session, project_name="p", start_date="2026-02-16", weeks=4)
    session = _advance(session, target_ids=["all_adults"])
    session = _advance(session, planning_type="Reach")
    with pytest.raises(agent.StepError):
        _advance(session, channel_ids=["not_a_channel"])
