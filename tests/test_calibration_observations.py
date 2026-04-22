"""TS-29a · CalibrationObservation store + optimizer fallback
(PRD v6 · FR-30 — learning loop, observation side).

Every PUT of actuals should append raw observations keyed by
(client_id, target_id, channel_id, metric). On the read path, the
optimizer must prefer the calibrated values over the static
`channel_metrics.json` defaults once at least one observation exists
for a given triple.

Tests are deliberately focused on the *observation* layer; decay maths
and confidence scoring live in TS-29b / TS-29c respectively.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import storage

from ._v6_helpers import any_channel_id, finish_manual_plan


def _final_record(plan, *, spend=400_000, impressions=2_000_000,
                  net_reach=40.0, penetration=38.0, cpm=None):
    ch = any_channel_id(plan)
    return {
        "scope": "FINAL",
        "period_week": None,
        "per_channel": {ch: {
            "spend_twd": spend, "impressions": impressions,
            "cpm_twd": cpm if cpm is not None else
                       ((spend / impressions * 1000) if impressions else 0),
            "net_reach_pct": net_reach, "frequency": 2.5,
            "penetration_pct": penetration, "buying_audience_000": 8500,
        }},
    }


# ---------- Schema ----------

def test_observation_tables_exist():
    """FR-30 / NFR-7.1 — observation store + profile view migrated."""
    tables = storage.list_tables()
    assert "calibration_observations" in tables, (
        "calibration_observations table must be created by init_schema()"
    )
    assert "calibration_profiles" in tables, (
        "calibration_profiles materialised view must be created by init_schema()"
    )


# ---------- Observation append on actuals PUT ----------

def test_final_actuals_append_observations(client: TestClient, owner_id, project_id):
    """One FINAL record → one observation per channel × metric."""
    _, plan = finish_manual_plan(owner_id,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final_record(plan)]})

    from app.services import calibration  # new module
    obs = calibration.list_observations(
        client_id="internal_pitch",
        target_id="all_adults",
        channel_id=ch,
        owner_id=owner_id,
    )
    # At minimum: cpm, penetration, net_reach metrics — one observation each.
    metrics = {o.metric for o in obs}
    assert "cpm_twd" in metrics
    assert "penetration_pct" in metrics
    assert "net_reach_pct" in metrics


def test_weekly_actuals_each_create_own_observation(client, owner_id, project_id):
    """WEEKLY records are independent observations — 4 weeks → 4 obs per metric."""
    _, plan = finish_manual_plan(owner_id, weeks=4,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    for w in range(1, 5):
        client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
            "scope": "WEEKLY", "period_week": w,
            "per_channel": {ch: {
                "spend_twd": 100_000, "impressions": 500_000,
                "cpm_twd": 200.0, "net_reach_pct": 10.0 + w,
                "frequency": 1.8, "penetration_pct": 30.0,
                "buying_audience_000": 8500,
            }},
        }]})

    from app.services import calibration
    reach_obs = calibration.list_observations(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=ch, owner_id=owner_id, metric="net_reach_pct",
    )
    assert len(reach_obs) == 4, (
        f"Expected 4 weekly observations for net_reach_pct, got {len(reach_obs)}"
    )


# ---------- Optimizer read-path prefers profile ----------

def test_optimizer_prefers_calibrated_cpm_over_defaults(client, owner_id, project_id):
    """After recording actuals that imply a very different CPM from the
    survey default, the next plan built for the same (client × target)
    must use the calibrated CPM in its cost math."""
    from app.services import calibration, optimizer

    # First plan — record FINAL with a wildly different CPM (→ triggers
    # profile with non-trivial weighted mean).
    _, first = finish_manual_plan(owner_id,
                                  client_id="internal_pitch",
                                  target_ids=["all_adults"])
    ch = any_channel_id(first)
    client.put(f"/api/plans/{first.id}/actuals",
               json={"records": [_final_record(first, cpm=999.0)]})

    # Read path — optimizer must see a calibrated CPM now.
    resolved = optimizer.resolve_channel_cpm(
        channel_id=ch,
        client_id="internal_pitch",
        target_id="all_adults",
        owner_id=owner_id,
    )
    default = optimizer.default_channel_cpm(ch)
    assert resolved != default, (
        f"Optimizer should NOT fall back to survey default once a "
        f"calibration profile exists; got {resolved}, default {default}"
    )
    assert abs(resolved - 999.0) < 1.0, (
        f"Optimizer must use the calibrated CPM (~999); got {resolved}"
    )


def test_optimizer_falls_back_to_default_without_observations(owner_id):
    """No observations → return the static channel_metrics.json CPM."""
    from app.services import optimizer
    ch = "tv_advertising"
    resolved = optimizer.resolve_channel_cpm(
        channel_id=ch,
        client_id="internal_pitch",
        target_id="all_adults",
        owner_id=owner_id,
    )
    assert resolved == optimizer.default_channel_cpm(ch), (
        "Empty profile → static default must still be returned"
    )


# ---------- Profile materialisation + n_effective ----------

def test_profile_materialises_with_n_effective(client, owner_id, project_id):
    """After 3 FINAL records on the same (client × target × channel), the
    profile row should report n_raw == 3 and n_effective > 0."""
    from app.services import calibration

    # Three plans, each with one FINAL observation.
    for i in range(3):
        _, plan = finish_manual_plan(owner_id,
                                     client_id="internal_pitch",
                                     target_ids=["all_adults"])
        client.put(f"/api/plans/{plan.id}/actuals",
                   json={"records": [_final_record(plan, cpm=200.0 + i)]})

    ch = any_channel_id(plan)
    profile = calibration.get_profile(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=ch, metric="cpm_twd", owner_id=owner_id,
    )
    assert profile is not None, "Profile must materialise after observations"
    assert profile.n_raw == 3
    assert profile.n_effective > 0


def test_cross_tenant_observations_are_isolated(client, owner_id, project_id):
    """One user's observations must not leak into another user's profile."""
    from app.main import app
    from app.services import calibration

    _, plan = finish_manual_plan(owner_id,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final_record(plan, cpm=888.0)]})

    storage.ensure_admin(name="carol", api_key="carol-key")
    carol = TestClient(app)
    carol.headers.update({"X-API-Key": "carol-key"})
    carol_user = storage.get_user_by_name("carol")

    obs = calibration.list_observations(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=ch, owner_id=carol_user.id,
    )
    assert obs == [], (
        "Carol must not see the default user's calibration observations"
    )
