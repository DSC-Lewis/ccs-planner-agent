"""v6 · PR C Issue 1 — Brief.overrides must flow into plan computation.

The pipeline priority (override → calibration → static default) is the
headline promise of FR-31. Before this test suite, the optimizer read
only ``reference.channel_metrics()`` and silently ignored anything a
senior planner typed into ``Brief.overrides``.

Each test hits ``compute_manual_plan`` / ``compute_automatic_plan``
directly (no HTTP) so assertions are pinned to the maths, not any
route wiring.
"""
from __future__ import annotations

from datetime import date

from app.schemas import (
    AutomaticPlanInput,
    Brief,
    ChannelOverride,
    ManualPlanInput,
    PlanningType,
)
from app.services import calibration, optimizer


# default CPM for TV in channel_metrics.json — if this ever changes,
# update the number; the test is about override vs default, not the
# exact number.
_DEFAULT_TV_CPM = optimizer.default_channel_cpm("tv_advertising")


def _brief(client_id: str = "internal_pitch",
           target_ids=None,
           channels=None) -> Brief:
    return Brief(
        client_id=client_id,
        project_name="override-math-fixture",
        start_date=date(2026, 2, 16),
        weeks=4,
        end_date=date(2026, 3, 15),
        target_ids=target_ids or ["all_adults"],
        planning_type=PlanningType.REACH,
        channel_ids=channels or ["tv_advertising", "youtube_video_ads"],
    )


def _tv(allocations):
    for a in allocations:
        if a.channel_id == "tv_advertising":
            return a
    raise AssertionError("TV channel missing from plan allocations")


def test_manual_plan_uses_override_cpm(owner_id):
    """Override CPM 5000 (≈60x default) should roughly 60x compress
    impressions vs the static-default baseline."""
    brief = _brief()
    brief.overrides = {"tv_advertising": ChannelOverride(cpm_twd=5000.0)}

    weekly = {ch: [100_000.0, 100_000.0, 100_000.0, 100_000.0]
              for ch in brief.channel_ids}
    plan = optimizer.compute_manual_plan(
        brief, ManualPlanInput(weekly_budgets=weekly), owner_id=owner_id,
    )
    tv = _tv(plan.allocations)

    # With 400k total budget / 5000 CPM = 80k impressions.
    # Baseline at default ~83 CPM would give ~4.8M impressions.
    assert tv.total_impressions < 200_000, (
        f"Override CPM should compress impressions to <200k, got "
        f"{tv.total_impressions}"
    )
    # Also confirm the baseline is very different, so the test would fail
    # if the override silently fell through to default.
    baseline = optimizer.compute_manual_plan(
        _brief(), ManualPlanInput(weekly_budgets=weekly), owner_id=owner_id,
    )
    baseline_tv = _tv(baseline.allocations)
    assert baseline_tv.total_impressions > tv.total_impressions * 5, (
        "Baseline (default CPM) should have materially more impressions "
        "than the 5000-CPM override — otherwise override isn't wired."
    )


def test_auto_plan_uses_override_cpm(owner_id):
    """Same idea, automatic flow."""
    brief = _brief()
    brief.overrides = {"tv_advertising": ChannelOverride(cpm_twd=5000.0)}

    auto_input = AutomaticPlanInput(
        criterion_id="net_reach", strategy_id="global_plan",
        total_budget_twd=400_000.0,
        mandatory_channel_ids=["tv_advertising", "youtube_video_ads"],
    )
    plan = optimizer.compute_automatic_plan(brief, auto_input, owner_id=owner_id)
    tv = _tv(plan.allocations)

    baseline = optimizer.compute_automatic_plan(
        _brief(), auto_input, owner_id=owner_id,
    )
    baseline_tv = _tv(baseline.allocations)
    assert baseline_tv.total_impressions > tv.total_impressions * 3, (
        "Override CPM 5000 should collapse TV impressions well below the "
        "default-CPM baseline in the automatic flow too."
    )


def test_override_beats_calibration(owner_id):
    """Override value wins even when a calibration profile exists."""
    # Seed a calibration profile for tv_advertising @ CPM 250 — halfway
    # between default (~83) and the override target (5000), so the three
    # values are all distinguishable.
    for _ in range(3):
        calibration.record_observation(
            client_id="internal_pitch", target_id="all_adults",
            channel_id="tv_advertising", metric="cpm_twd",
            value=250.0, owner_id=owner_id,
        )
    prof = calibration.get_profile(
        client_id="internal_pitch", target_id="all_adults",
        channel_id="tv_advertising", metric="cpm_twd", owner_id=owner_id,
    )
    assert prof is not None and prof.n_raw >= 1

    brief = _brief()
    brief.overrides = {"tv_advertising": ChannelOverride(cpm_twd=5000.0)}
    weekly = {ch: [100_000.0] * 4 for ch in brief.channel_ids}
    plan = optimizer.compute_manual_plan(
        brief, ManualPlanInput(weekly_budgets=weekly), owner_id=owner_id,
    )
    tv = _tv(plan.allocations)

    # Expected with override: 400000 / 5000 * 1000 = 80000 impressions.
    # If calibration (250 CPM) had won: 400000 / 250 * 1000 = 1_600_000.
    assert tv.total_impressions < 200_000, (
        f"Override (CPM 5000) must beat calibration (CPM 250); got "
        f"{tv.total_impressions} impressions which looks like calibration won."
    )


def test_calibration_beats_default(owner_id):
    """No override, but observations exist — calibrated CPM is used."""
    # Seed CPM 400 — clearly distinguishable from default ~83.
    for _ in range(3):
        calibration.record_observation(
            client_id="internal_pitch", target_id="all_adults",
            channel_id="tv_advertising", metric="cpm_twd",
            value=400.0, owner_id=owner_id,
        )

    brief = _brief()
    weekly = {ch: [100_000.0] * 4 for ch in brief.channel_ids}
    plan = optimizer.compute_manual_plan(
        brief, ManualPlanInput(weekly_budgets=weekly), owner_id=owner_id,
    )
    tv = _tv(plan.allocations)

    # Expected with calibration CPM 400: 400_000 / 400 * 1000 = 1_000_000.
    # Default CPM ~83 would give ~4_800_000.
    assert 800_000 < tv.total_impressions < 1_200_000, (
        f"Calibrated CPM ~400 should yield ~1M impressions; got "
        f"{tv.total_impressions}. (Default CPM would land ~4.8M.)"
    )
