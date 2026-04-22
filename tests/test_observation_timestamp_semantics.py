"""v6 · PR C Issue 12 — WEEKLY / FINAL observations must timestamp to the
event they represent, not to when the planner typed them in.

Before this fix, ``record_from_actuals`` always stamped ``observed_at``
with ``actuals_record.recorded_at`` (wall clock). For a weekly record
that back-fills January numbers in April, decay maths then treats the
observation as fresh instead of old.

Fix: stamp WEEKLY at ``brief.start_date + (period_week-1)*7 + 6 days``
(end of that week), and stamp FINAL at ``brief.end_date``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.schemas import (
    ActualsScope,
    Brief,
    ChannelActual,
    PlanActualsRecord,
    PlanningType,
)
from app.services import calibration


def _brief() -> Brief:
    # 2026-02-16 is the fixture start date used across the suite; weeks=4
    # means end_date = start + 27 days = 2026-03-15.
    return Brief(
        client_id="internal_pitch",
        project_name="ts-fixture",
        start_date=date(2026, 2, 16),
        weeks=4,
        end_date=date(2026, 3, 15),
        target_ids=["all_adults"],
        planning_type=PlanningType.REACH,
        channel_ids=["tv_advertising"],
    )


def _record(scope: ActualsScope, period_week=None) -> PlanActualsRecord:
    # recorded_at = wall clock (April 22, 2026) — deliberately long after
    # the plan's end date so the two timestamps diverge clearly.
    april_22 = datetime(2026, 4, 22, tzinfo=timezone.utc).timestamp()
    return PlanActualsRecord(
        id="act_fixture",
        plan_id="plan_fixture",
        recorded_by="tester",
        recorded_at=april_22,
        scope=scope,
        period_week=period_week,
        per_channel={"tv_advertising": ChannelActual(
            spend_twd=100_000, impressions=500_000, cpm_twd=200.0,
            net_reach_pct=10.0, frequency=2.0, penetration_pct=20.0,
            buying_audience_000=1000,
        )},
    )


def test_weekly_uses_period_end(owner_id):
    """Week 2 of a plan starting 2026-02-16 ends 2026-02-28. observed_at
    must land within a couple days of that — NOT April 22 wall-clock."""
    brief = _brief()
    rec = _record(ActualsScope.WEEKLY, period_week=2)
    calibration.record_from_actuals(plan_brief=brief, actuals_record=rec,
                                    owner_id=owner_id)

    obs = calibration.list_observations(
        client_id="internal_pitch", target_id="all_adults",
        channel_id="tv_advertising", metric="cpm_twd", owner_id=owner_id,
    )
    assert obs, "observation should have been recorded"
    observed_dt = datetime.fromtimestamp(obs[0].observed_at, tz=timezone.utc)

    expected = datetime(2026, 2, 28, tzinfo=timezone.utc)
    delta_days = abs((observed_dt - expected).total_seconds()) / 86400.0
    assert delta_days <= 2, (
        f"WEEKLY observed_at should be within 2 days of 2026-02-28 "
        f"(period-2 end), got {observed_dt.isoformat()} (delta {delta_days}d)."
    )
    # Sanity: it must NOT equal the April wall-clock timestamp.
    april_22 = datetime(2026, 4, 22, tzinfo=timezone.utc)
    assert abs((observed_dt - april_22).total_seconds()) > 30 * 86400, (
        f"WEEKLY observed_at must not be the April wall-clock; got "
        f"{observed_dt.isoformat()}"
    )


def test_final_uses_end_date(owner_id):
    """FINAL observed_at should equal brief.end_date (to the day)."""
    brief = _brief()
    rec = _record(ActualsScope.FINAL)
    calibration.record_from_actuals(plan_brief=brief, actuals_record=rec,
                                    owner_id=owner_id)

    obs = calibration.list_observations(
        client_id="internal_pitch", target_id="all_adults",
        channel_id="tv_advertising", metric="cpm_twd", owner_id=owner_id,
    )
    assert obs, "observation should have been recorded"
    observed_dt = datetime.fromtimestamp(obs[0].observed_at, tz=timezone.utc)

    expected = datetime(2026, 3, 15, tzinfo=timezone.utc)
    assert observed_dt.date() == expected.date(), (
        f"FINAL observed_at must equal brief.end_date 2026-03-15; got "
        f"{observed_dt.isoformat()}"
    )
