"""Shared helpers for v6 (actuals + overrides) test suites.

The goal is to avoid duplicating the Manual-flow walkthrough in every test
file. Callers get a saved Plan ready to hang actuals off.
"""
from __future__ import annotations

from typing import Optional, Tuple

from app.schemas import SessionMode, StepPayload
from app.services import agent, storage


def _advance(session, owner_id: str, **payload):
    return agent.advance(session, StepPayload(**payload), owner_id=owner_id)


def finish_manual_plan(
    owner_id: str,
    *,
    client_id: str = "internal_pitch",
    target_ids: Optional[list] = None,
    channel_ids: Optional[list] = None,
    weeks: int = 4,
):
    """Walk the Manual flow end-to-end and return (session, plan).

    Defaults match the values used throughout the v4 test suite so
    reviewers can map behaviour back to the established baseline.
    """
    target_ids = target_ids or ["all_adults"]
    channel_ids = channel_ids or [
        "tv_advertising", "youtube_video_ads", "meta_video_ads",
    ]

    session = agent.create_session(SessionMode.MANUAL, owner_id=owner_id)
    session = _advance(session, owner_id,
                       survey_id="tw_2025", client_id=client_id)
    session = _advance(session, owner_id,
                       project_name="v6-fixture",
                       start_date="2026-02-16", weeks=weeks)
    session = _advance(session, owner_id, target_ids=target_ids)
    session = _advance(session, owner_id, planning_type="Reach")
    session = _advance(session, owner_id, channel_ids=channel_ids)
    session = _advance(session, owner_id)  # calibration step (no payload)

    weekly = {cid: [100_000] * weeks for cid in channel_ids}
    session = _advance(session, owner_id, weekly_budgets=weekly)

    assert session.plan_id, "Manual flow did not save a plan"
    plan = storage.get_plan(session.plan_id, owner_id=owner_id)
    assert plan is not None
    return session, plan


def any_channel_id(plan) -> str:
    """First channel id on the plan — convenience for asserting per-channel fields."""
    assert plan.allocations, "Plan has no channel allocations"
    return plan.allocations[0].channel_id
