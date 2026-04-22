"""Issue 13 · v6 · FR-30 — PATCH /api/calibration/observations/{id} must reject
out-of-range weight_override values (422) rather than silently persisting them.
None is still valid — it clears the override and falls back to decay.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _seed_observation(owner_id, *, weight_override=None):
    from app.services import calibration
    when = datetime.now(tz=timezone.utc) - timedelta(days=7)
    obs = calibration.record_observation(
        client_id="internal_pitch", target_id="all_adults",
        channel_id="tv_advertising", metric="cpm_twd", value=200.0,
        observed_at=when, owner_id=owner_id,
        weight_override=weight_override,
    )
    return obs.id


def test_patch_rejects_weight_override_above_one(client, owner_id):
    obs_id = _seed_observation(owner_id)
    resp = client.patch(
        f"/api/calibration/observations/{obs_id}",
        json={"weight_override": 1.5},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for weight_override=1.5, got {resp.status_code}: {resp.text}"
    )


def test_patch_rejects_negative_weight_override(client, owner_id):
    obs_id = _seed_observation(owner_id)
    resp = client.patch(
        f"/api/calibration/observations/{obs_id}",
        json={"weight_override": -0.1},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for weight_override=-0.1, got {resp.status_code}: {resp.text}"
    )


def test_patch_accepts_zero(client, owner_id):
    """0.0 is a legitimate pin — "exclude this outlier from the mean"."""
    obs_id = _seed_observation(owner_id)
    resp = client.patch(
        f"/api/calibration/observations/{obs_id}",
        json={"weight_override": 0.0},
    )
    assert resp.status_code == 200, (
        f"weight_override=0 must succeed; got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["weight_override"] == 0.0


def test_patch_accepts_null_to_clear(client, owner_id):
    """None means "clear the pin, fall back to decay"."""
    obs_id = _seed_observation(owner_id, weight_override=0.5)
    resp = client.patch(
        f"/api/calibration/observations/{obs_id}",
        json={"weight_override": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["weight_override"] is None
