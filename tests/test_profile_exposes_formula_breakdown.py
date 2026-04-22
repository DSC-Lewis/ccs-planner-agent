"""v6 · FR-30b — confidence-formula breakdown surfaced on CalibrationProfile.

The frontend tooltip shows the planner HOW the confidence score was
computed: sample_factor, consistency_factor, cv. Persist these on the
profile row so the UI doesn't re-derive them and potentially drift.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _add_obs(owner_id, *, days_ago: float, value: float,
             channel="tv_advertising", metric="cpm_twd"):
    from app.services import calibration
    when = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return calibration.record_observation(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=channel, metric=metric, value=value,
        observed_at=when, owner_id=owner_id,
    )


def _profile(owner_id, *, channel="tv_advertising", metric="cpm_twd"):
    from app.services import calibration
    return calibration.get_profile(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=channel, metric=metric, owner_id=owner_id,
    )


def test_consistent_observations_yield_zero_cv_and_full_consistency(owner_id):
    """3 identical values → cv=0, consistency_factor=1, sample_factor>0."""
    for days in (10, 20, 30):
        _add_obs(owner_id, days_ago=days, value=200.0)

    prof = _profile(owner_id)
    assert prof is not None
    assert prof.sample_factor > 0, (
        f"sample_factor must be positive for 3 obs; got {prof.sample_factor}"
    )
    assert abs(prof.consistency_factor - 1.0) < 1e-9, (
        f"Identical values → consistency_factor=1; got {prof.consistency_factor}"
    )
    assert abs(prof.cv) < 1e-9, (
        f"Identical values → cv=0; got {prof.cv}"
    )


def test_noisy_observations_yield_positive_cv(owner_id):
    """Spread-out values (100, 200, 300) must yield cv > 0."""
    for days, value in ((5, 100.0), (10, 200.0), (15, 300.0)):
        _add_obs(owner_id, days_ago=days, value=value)

    prof = _profile(owner_id)
    assert prof is not None
    assert prof.cv > 0, (
        f"Spread-out values must produce cv>0; got {prof.cv}"
    )
