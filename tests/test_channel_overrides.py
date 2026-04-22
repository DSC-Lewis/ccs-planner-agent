"""TS-30 · Planner-override inputs on the Brief (PRD v6 · FR-31).

Senior planners often have better per-client numbers than the CCS survey
defaults. The Brief flow must accept per-channel override values and
persist them in the brief snapshot so the audit trail stays intact.

All imports of the new ChannelOverride symbol happen lazily *inside* each
test so that this module collects cleanly during RED (when the symbol
doesn't exist yet) and fails with a readable assertion rather than a
ModuleNotFoundError at collection time.
"""
from __future__ import annotations

import pytest

from app.schemas import Brief, SessionMode, StepPayload
from app.services import agent, storage


def _import_override():
    try:
        from app.schemas import ChannelOverride  # type: ignore
    except ImportError:
        pytest.fail("ChannelOverride not yet exported from app.schemas (RED expected)")
    return ChannelOverride


def _start_session_up_to_channels(owner_id: str):
    session = agent.create_session(SessionMode.MANUAL, owner_id=owner_id)
    session = agent.advance(session,
                            StepPayload(survey_id="tw_2025",
                                        client_id="internal_pitch"),
                            owner_id=owner_id)
    session = agent.advance(session,
                            StepPayload(project_name="override-fixture",
                                        start_date="2026-02-16", weeks=4),
                            owner_id=owner_id)
    session = agent.advance(session,
                            StepPayload(target_ids=["all_adults"]),
                            owner_id=owner_id)
    session = agent.advance(session,
                            StepPayload(planning_type="Reach"),
                            owner_id=owner_id)
    session = agent.advance(session,
                            StepPayload(channel_ids=[
                                "tv_advertising", "youtube_video_ads",
                            ]),
                            owner_id=owner_id)
    return session


def test_brief_has_overrides_field():
    b = Brief()
    assert hasattr(b, "overrides"), "Brief must expose an 'overrides' attribute"
    assert isinstance(b.overrides, dict)


def test_channel_override_schema_has_expected_metrics():
    ChannelOverride = _import_override()
    ov = ChannelOverride()
    for field in ("cpm_twd", "penetration_pct", "net_reach_pct",
                  "buying_audience_000", "impressions"):
        assert hasattr(ov, field), f"ChannelOverride missing '{field}'"


def test_step_payload_accepts_overrides():
    """StepPayload must grow an `overrides` field so the /advance endpoint
    can pass it through without reshaping."""
    ChannelOverride = _import_override()
    p = StepPayload(overrides={"tv_advertising": ChannelOverride(cpm_twd=222.0)})
    assert "tv_advertising" in (p.overrides or {})


def test_advance_accepts_overrides_on_channels_step(owner_id):
    ChannelOverride = _import_override()
    session = _start_session_up_to_channels(owner_id)
    payload = StepPayload(overrides={
        "tv_advertising": ChannelOverride(cpm_twd=222.0, penetration_pct=55.0),
    })
    try:
        session = agent.advance(session, payload, owner_id=owner_id)
    except agent.StepError:
        pytest.fail("advance() should accept 'overrides' without a StepError")

    assert "tv_advertising" in session.brief.overrides
    assert session.brief.overrides["tv_advertising"].cpm_twd == 222.0


def test_overrides_round_trip_through_storage(owner_id):
    ChannelOverride = _import_override()
    session = _start_session_up_to_channels(owner_id)
    session.brief.overrides = {
        "tv_advertising": ChannelOverride(cpm_twd=250.0, net_reach_pct=30.0),
    }
    storage.save_session(session, owner_id=owner_id)

    reloaded = storage.get_session(session.id, owner_id=owner_id)
    assert "tv_advertising" in reloaded.brief.overrides
    assert reloaded.brief.overrides["tv_advertising"].cpm_twd == 250.0


def test_overrides_recorded_in_conversation_turn(owner_id):
    ChannelOverride = _import_override()
    session = _start_session_up_to_channels(owner_id)
    payload = StepPayload(overrides={
        "tv_advertising": ChannelOverride(cpm_twd=199.0),
    })
    agent.advance(session, payload, owner_id=owner_id)

    turns = storage.get_conversation(session.id, owner_id=owner_id)
    assert turns, "advance() must log at least one conversation turn"
    last = turns[-1]
    snap_overrides = last.brief_snapshot.get("overrides") or {}
    assert snap_overrides.get("tv_advertising", {}).get("cpm_twd") == 199.0, (
        f"brief_snapshot must carry the override; got {snap_overrides}"
    )


def test_clear_override_by_passing_none(owner_id):
    ChannelOverride = _import_override()
    session = _start_session_up_to_channels(owner_id)
    session.brief.overrides = {
        "tv_advertising": ChannelOverride(cpm_twd=250.0),
    }
    storage.save_session(session, owner_id=owner_id)

    payload = StepPayload(overrides={})
    session = agent.advance(session, payload, owner_id=owner_id)
    assert "tv_advertising" not in session.brief.overrides or \
        session.brief.overrides.get("tv_advertising") is None
