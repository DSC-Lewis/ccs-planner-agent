"""v6 gap-audit · Issue 18 — no-op guard on settings writes.

When a planner opens the settings modal, types the existing value, and
hits 套用 — or double-clicks the button — we shouldn't re-walk every
observation and rewrite every profile row just to land on the exact
same number. The service layer must short-circuit when the effective
half-life / threshold values haven't actually changed.

This is a correctness *and* perf test: we count the number of
rematerialisation calls via monkeypatch.
"""
from __future__ import annotations

from app.services import calibration


def test_set_half_life_noop_skips_rematerialise(owner_id, monkeypatch):
    call_count = {"n": 0}
    original = calibration._rematerialise_all_for_owner

    def spy(owner_id):
        call_count["n"] += 1
        return original(owner_id)

    monkeypatch.setattr(calibration, "_rematerialise_all_for_owner", spy)

    # First call persists 60 and rebuilds (expected).
    calibration.set_half_life(owner_id=owner_id, half_life_days=60)
    assert call_count["n"] == 1

    # Second call with the exact same value is a no-op.
    calibration.set_half_life(owner_id=owner_id, half_life_days=60)
    assert call_count["n"] == 1, (
        "Setting half_life to the SAME value must not trigger another "
        "rematerialisation pass."
    )

    # Changing the value triggers another rebuild.
    calibration.set_half_life(owner_id=owner_id, half_life_days=30)
    assert call_count["n"] == 2


def test_set_thresholds_noop_skips_rematerialise(owner_id, monkeypatch):
    call_count = {"n": 0}
    original = calibration._rematerialise_all_for_owner

    def spy(owner_id):
        call_count["n"] += 1
        return original(owner_id)

    monkeypatch.setattr(calibration, "_rematerialise_all_for_owner", spy)

    calibration.set_confidence_thresholds(owner_id=owner_id, high=80, mid=50)
    assert call_count["n"] == 1

    # Identical values should not re-run the expensive pass.
    calibration.set_confidence_thresholds(owner_id=owner_id, high=80, mid=50)
    assert call_count["n"] == 1, (
        "Setting thresholds to identical values should be a no-op."
    )

    # Different values do trigger the rebuild.
    calibration.set_confidence_thresholds(owner_id=owner_id, high=75, mid=45)
    assert call_count["n"] == 2


def test_set_per_client_noop_skips_rematerialise(owner_id, monkeypatch):
    call_count = {"n": 0}
    original = calibration._rematerialise_all_for_owner

    def spy(owner_id):
        call_count["n"] += 1
        return original(owner_id)

    monkeypatch.setattr(calibration, "_rematerialise_all_for_owner", spy)

    calibration.set_half_life(
        owner_id=owner_id, client_id="internal_pitch", half_life_days=45
    )
    calibration.set_half_life(
        owner_id=owner_id, client_id="internal_pitch", half_life_days=45
    )
    assert call_count["n"] == 1, "Per-client no-op should also be short-circuited"
