"""Issue 7 · v6 · FR-30 — GET /api/calibration/observations must surface the
currently-contributing ``effective_weight`` and ``age_days`` so the
observation drawer can render them without re-doing the decay maths.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def _add_obs(owner_id, *, days_ago: float, value: float,
             channel="tv_advertising", metric="cpm_twd",
             weight_override=None):
    from app.services import calibration
    when = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return calibration.record_observation(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=channel, metric=metric, value=value,
        observed_at=when, owner_id=owner_id,
        weight_override=weight_override,
    )


def _get_rows(client, *, channel="tv_advertising", metric="cpm_twd"):
    resp = client.get(
        "/api/calibration/observations",
        params={
            "client_id": "internal_pitch",
            "target_id": "all_adults",
            "channel_id": channel,
            "metric": metric,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_effective_weight_with_override(client, owner_id):
    """weight_override is sticky — endpoint echoes it back as effective_weight."""
    _add_obs(owner_id, days_ago=7, value=200.0, weight_override=0.3)
    rows = _get_rows(client)
    assert len(rows) == 1
    row = rows[0]
    assert row["weight_override"] == 0.3
    assert abs(row["effective_weight"] - 0.3) < 1e-9, (
        f"Override should short-circuit decay; got {row['effective_weight']}"
    )


def test_effective_weight_without_override_decays(client, owner_id):
    """180-day-old observation at default 180-day half-life → weight ≈ 0.5."""
    _add_obs(owner_id, days_ago=180, value=123.0)
    rows = _get_rows(client)
    assert len(rows) == 1
    row = rows[0]
    assert row["weight_override"] is None
    assert abs(row["effective_weight"] - 0.5) < 0.05, (
        f"One-half-life-old obs should weigh ~0.5; got {row['effective_weight']}"
    )
    assert abs(row["age_days"] - 180.0) < 1.0, (
        f"age_days should reflect the observation's age in days; got {row['age_days']}"
    )


def test_effective_weight_respects_per_channel_half_life(client, owner_id):
    """Per-channel half-life override must feed the effective_weight calc."""
    from app.services import calibration
    _add_obs(owner_id, days_ago=180, value=123.0)
    # Pin this (client × target × channel) to a 30-day half-life.
    calibration.set_half_life(
        owner_id=owner_id,
        client_id="internal_pitch", target_id="all_adults",
        channel_id="tv_advertising", half_life_days=30,
    )
    rows = _get_rows(client)
    assert len(rows) == 1
    expected = 2.0 ** (-180.0 / 30.0)  # ≈ 0.015625
    assert abs(rows[0]["effective_weight"] - expected) < 1e-3, (
        f"Expected weight {expected}, got {rows[0]['effective_weight']}"
    )
