"""TS-1 · Input length / magnitude caps (NFR-1.3)."""
from __future__ import annotations

import pytest

from app.schemas import SessionMode, StepPayload
from app.services import agent


def _seed_to_project_step(owner_id):
    s = agent.create_session(SessionMode.MANUAL, owner_id=owner_id)
    return agent.advance(
        s, StepPayload(survey_id="tw_2025", client_id="internal_pitch"),
        owner_id=owner_id,
    )


def _seed_to_channels_step(owner_id):
    s = _seed_to_project_step(owner_id)
    s = agent.advance(
        s, StepPayload(project_name="cap tests", start_date="2026-02-16", weeks=4),
        owner_id=owner_id,
    )
    s = agent.advance(s, StepPayload(target_ids=["all_adults"]), owner_id=owner_id)
    return agent.advance(s, StepPayload(planning_type="Reach"), owner_id=owner_id)


def _seed_to_manual_plan(owner_id):
    s = _seed_to_channels_step(owner_id)
    s = agent.advance(s, StepPayload(channel_ids=["tv_advertising", "meta_video_ads"]),
                      owner_id=owner_id)
    return agent.advance(s, StepPayload(), owner_id=owner_id)


def test_project_name_length_cap_is_enforced(owner_id):
    s = _seed_to_project_step(owner_id)
    with pytest.raises(agent.StepError, match="project_name"):
        agent.advance(
            s, StepPayload(project_name="A" * 200, start_date="2026-02-16", weeks=4),
            owner_id=owner_id,
        )


def test_channel_ids_list_cap_is_enforced(owner_id):
    s = _seed_to_channels_step(owner_id)
    fake = [f"chan_{i}" for i in range(60)]
    with pytest.raises(agent.StepError, match=r"(channel_ids|too many)"):
        agent.advance(s, StepPayload(channel_ids=fake), owner_id=owner_id)


def test_weekly_budget_ceiling_is_enforced(owner_id):
    s = _seed_to_manual_plan(owner_id)
    with pytest.raises(agent.StepError, match=r"(too large|exceeds)"):
        agent.advance(
            s,
            StepPayload(weekly_budgets={
                "tv_advertising":  [1e18, 0, 0, 0],
                "meta_video_ads":  [0, 0, 0, 0],
            }),
            owner_id=owner_id,
        )
