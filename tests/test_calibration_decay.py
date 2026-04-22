"""TS-29b · Exponential decay + planner weight override (PRD v6 · FR-30).

Weighted mean:  w_i = exp(-ln(2) * age_days_i / half_life_days)
Planner-pinned `weight_override ∈ [0, 1]` replaces the decay weight for
a single observation. A half-life change MUST re-materialise the profile
without changing the underlying observation rows.

These tests manipulate observations directly through the calibration
service so we can assert maths without running a full plan flow end to
end (the plan flow is covered by TS-29a).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from ._v6_helpers import finish_manual_plan


def _calibration():
    from app.services import calibration
    return calibration


def _add_obs(owner_id, *, days_ago: float, value: float, channel="tv_advertising",
             metric="cpm_twd", weight_override=None):
    cal = _calibration()
    when = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return cal.record_observation(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=channel, metric=metric, value=value,
        observed_at=when, owner_id=owner_id,
        weight_override=weight_override,
    )


# ---------- Core decay maths ----------

def test_exponential_weight_formula():
    """w = 2 ** (-age_days / half_life_days). Point-check at half-life."""
    cal = _calibration()
    w = cal.decay_weight(age_days=180, half_life_days=180)
    assert abs(w - 0.5) < 1e-9, f"At one half-life, weight should be 0.5; got {w}"
    w2 = cal.decay_weight(age_days=360, half_life_days=180)
    assert abs(w2 - 0.25) < 1e-9
    w0 = cal.decay_weight(age_days=0, half_life_days=180)
    assert abs(w0 - 1.0) < 1e-9


def test_weighted_mean_shifts_with_age(owner_id):
    """An old observation should contribute less to the mean than a new one."""
    cal = _calibration()
    _add_obs(owner_id, days_ago=365, value=100.0)  # old
    _add_obs(owner_id, days_ago=0,   value=200.0)  # fresh

    prof = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                           channel_id="tv_advertising", metric="cpm_twd",
                           owner_id=owner_id)
    assert prof is not None
    # With default 180-day half-life, fresh observation weighs 1.0 and
    # old one weighs 2**(-365/180) ≈ 0.244. Weighted mean ≈ 180.
    assert 175 < prof.value_mean_weighted < 195, (
        f"Weighted mean should favour the fresh observation; "
        f"got {prof.value_mean_weighted}"
    )


# ---------- Half-life tunable ----------

def test_shorter_half_life_upweights_recent(owner_id):
    cal = _calibration()
    _add_obs(owner_id, days_ago=180, value=100.0)
    _add_obs(owner_id, days_ago=0,   value=200.0)

    # At half-life 180 days, old obs weighs 0.5 → mean ≈ 166.7
    # At half-life 30 days, old obs weighs 2^(-6) ≈ 0.0156 → mean ≈ 198.5
    cal.set_half_life(client_id="internal_pitch", target_id="all_adults",
                      channel_id=None, half_life_days=180, owner_id=owner_id)
    prof_long = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                                channel_id="tv_advertising", metric="cpm_twd",
                                owner_id=owner_id)

    cal.set_half_life(client_id="internal_pitch", target_id="all_adults",
                      channel_id=None, half_life_days=30, owner_id=owner_id)
    prof_short = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                                 channel_id="tv_advertising", metric="cpm_twd",
                                 owner_id=owner_id)
    assert prof_short.value_mean_weighted > prof_long.value_mean_weighted, (
        "Shorter half-life must pull the mean closer to the fresh value"
    )


def test_half_life_scope_precedence(owner_id):
    """Per-channel override must beat per-client, which must beat global."""
    cal = _calibration()
    # Global
    cal.set_half_life(client_id=None, target_id=None, channel_id=None,
                      half_life_days=180, owner_id=owner_id)
    # Per-client
    cal.set_half_life(client_id="internal_pitch", target_id=None,
                      channel_id=None,
                      half_life_days=90, owner_id=owner_id)
    # Per-channel (most specific)
    cal.set_half_life(client_id="internal_pitch", target_id="all_adults",
                      channel_id="tv_advertising",
                      half_life_days=30, owner_id=owner_id)

    hl = cal.effective_half_life(
        client_id="internal_pitch", target_id="all_adults",
        channel_id="tv_advertising", owner_id=owner_id,
    )
    assert hl == 30, f"Per-channel override should win; got {hl}"

    hl_outer = cal.effective_half_life(
        client_id="internal_pitch", target_id="all_adults",
        channel_id="youtube_video_ads", owner_id=owner_id,
    )
    assert hl_outer == 90, (
        f"Fall back to per-client when per-channel is unset; got {hl_outer}"
    )


def test_changing_half_life_rematerialises_profile(owner_id):
    """Changing the knob must re-run the weighted mean; observations stay put."""
    cal = _calibration()
    _add_obs(owner_id, days_ago=200, value=100.0)
    _add_obs(owner_id, days_ago=0,   value=300.0)

    # Capture pre-state
    before = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                             channel_id="tv_advertising", metric="cpm_twd",
                             owner_id=owner_id)

    # Flip to half-life 1 day — old obs weight collapses to ~0.
    cal.set_half_life(client_id="internal_pitch", target_id="all_adults",
                      channel_id=None, half_life_days=1, owner_id=owner_id)

    after = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                            channel_id="tv_advertising", metric="cpm_twd",
                            owner_id=owner_id)

    assert before.n_raw == after.n_raw, "Observations must not be dropped"
    assert after.value_mean_weighted > before.value_mean_weighted, (
        "Weighted mean should shift right after half-life shrinks"
    )
    assert abs(after.value_mean_weighted - 300.0) < 5.0, (
        "With 1-day half-life, old obs weight is effectively 0 → mean ≈ fresh"
    )


# ---------- Weight override ----------

def test_weight_override_zero_excludes_observation(owner_id):
    """weight_override=0 pins an outlier out of the mean entirely."""
    cal = _calibration()
    _add_obs(owner_id, days_ago=0, value=1000.0)  # outlier
    obs_id = _add_obs(owner_id, days_ago=0, value=100.0).id  # keeper
    # Pin the OUTLIER's weight to 0 — need its id first.
    prof_with = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                                channel_id="tv_advertising", metric="cpm_twd",
                                owner_id=owner_id)
    assert prof_with.value_mean_weighted > 400, "Outlier should still pull mean high"

    # Find the outlier's id and mute it.
    obs_list = cal.list_observations(client_id="internal_pitch",
                                     target_id="all_adults",
                                     channel_id="tv_advertising",
                                     metric="cpm_twd", owner_id=owner_id)
    outlier = next(o for o in obs_list if o.value == 1000.0)
    cal.set_observation_weight(owner_id=owner_id, observation_id=outlier.id,
                               weight_override=0.0)

    prof_after = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                                 channel_id="tv_advertising", metric="cpm_twd",
                                 owner_id=owner_id)
    assert abs(prof_after.value_mean_weighted - 100.0) < 1.0, (
        f"Pinned-to-zero outlier should be excluded; got mean={prof_after.value_mean_weighted}"
    )


def test_weight_override_survives_half_life_change(owner_id):
    """A planner-pinned weight is sticky — the next re-materialisation
    must honour it even if decay would say otherwise."""
    cal = _calibration()
    obs = _add_obs(owner_id, days_ago=500, value=50.0,
                   weight_override=1.0)  # force-count this old obs at full weight
    _add_obs(owner_id, days_ago=0, value=150.0)

    cal.set_half_life(client_id=None, target_id=None, channel_id=None,
                      half_life_days=30, owner_id=owner_id)

    prof = cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                           channel_id="tv_advertising", metric="cpm_twd",
                           owner_id=owner_id)
    # Both weights are effectively 1.0 → mean ≈ 100.
    assert abs(prof.value_mean_weighted - 100.0) < 5.0, (
        f"Override=1.0 must keep the old obs fully counted; got {prof.value_mean_weighted}"
    )


# ---------- Performance guard (NFR-7.7) ----------

def test_half_life_change_cheap_for_small_dataset(owner_id):
    """Flipping the half-life on an ivy-scale dataset must complete quickly."""
    import time
    cal = _calibration()
    # Seed 50 observations spread over 18 months.
    for i in range(50):
        _add_obs(owner_id, days_ago=i * 10, value=100.0 + i)

    t0 = time.perf_counter()
    cal.set_half_life(client_id=None, target_id=None, channel_id=None,
                      half_life_days=60, owner_id=owner_id)
    cal.get_profile(client_id="internal_pitch", target_id="all_adults",
                    channel_id="tv_advertising", metric="cpm_twd",
                    owner_id=owner_id)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, (
        f"Half-life rebuild must finish in < 2s for 50 obs; took {elapsed:.3f}s"
    )
