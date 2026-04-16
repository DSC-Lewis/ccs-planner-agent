"""TS-1 · Input length / magnitude caps (NFR-1.3).

Prevents runaway JSON payloads and numeric overflow downstream in the
optimizer.
"""
from __future__ import annotations

import pytest

from app.schemas import SessionMode, StepPayload
from app.services import agent


def _seed_to_project_step():
    s = agent.create_session(SessionMode.MANUAL)
    return agent.advance(
        s, StepPayload(survey_id="tw_2025", client_id="internal_pitch")
    )


def _seed_to_channels_step():
    s = _seed_to_project_step()
    s = agent.advance(
        s, StepPayload(project_name="cap tests", start_date="2026-02-16", weeks=4)
    )
    s = agent.advance(s, StepPayload(target_ids=["all_adults"]))
    return agent.advance(s, StepPayload(planning_type="Reach"))


def _seed_to_manual_plan():
    s = _seed_to_channels_step()
    s = agent.advance(s, StepPayload(channel_ids=["tv_advertising", "meta_video_ads"]))
    return agent.advance(s, StepPayload())  # calibration


def test_project_name_length_cap_is_enforced():
    """TC-1.2 — 200 chars rejected."""
    s = _seed_to_project_step()
    with pytest.raises(agent.StepError, match="project_name"):
        agent.advance(
            s,
            StepPayload(project_name="A" * 200, start_date="2026-02-16", weeks=4),
        )


def test_channel_ids_list_cap_is_enforced():
    """TC-1.3 — more than 50 channel ids rejected."""
    s = _seed_to_channels_step()
    fake = [f"chan_{i}" for i in range(60)]
    with pytest.raises(agent.StepError, match=r"(channel_ids|too many)"):
        agent.advance(s, StepPayload(channel_ids=fake))


def test_weekly_budget_ceiling_is_enforced():
    """TC-1.4 — per-cell budget above 1e12 rejected."""
    s = _seed_to_manual_plan()
    with pytest.raises(agent.StepError, match=r"(too large|exceeds)"):
        agent.advance(
            s,
            StepPayload(weekly_budgets={
                "tv_advertising":  [1e18, 0, 0, 0],
                "meta_video_ads":  [0, 0, 0, 0],
            }),
        )
