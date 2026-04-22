"""v6 · PR C Issue 3 — GET /api/calibration/channel-summary.

Drives the per-channel CAL pill in the Brief/Plan UI. For a given
(client, target) scope it returns one entry per known channel id, with
``has_profile``, ``confidence_score``, a traffic-light ``bucket``, and a
list of tracked metrics.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services import calibration, reference

from ._v6_helpers import any_channel_id, finish_manual_plan


def _summary(client: TestClient, client_id: str, target_id: str):
    r = client.get("/api/calibration/channel-summary",
                   params={"client_id": client_id, "target_id": target_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_summary_fresh_user_all_channels_empty(client: TestClient, owner_id):
    """Every known channel must appear; all must show no profile."""
    body = _summary(client, "internal_pitch", "all_adults")
    expected_channels = set(reference.all_channel_ids())
    # Every known channel id surfaces — even the ones without data — so
    # the UI can render a consistent row list.
    assert set(body.keys()) == expected_channels, (
        f"Expected all channels; missing: {expected_channels - set(body.keys())}"
    )
    for ch, entry in body.items():
        assert entry["has_profile"] is False, f"{ch} should have no profile"
        assert entry["confidence_score"] is None
        assert entry["bucket"] is None
        assert entry["metrics"] == []


def test_summary_reflects_recorded_actuals(client: TestClient, owner_id):
    """After recording actuals, exactly that channel flips to has_profile=true
    with a positive confidence score and a non-empty metrics list."""
    _, plan = finish_manual_plan(owner_id,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    r = client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
        "scope": "FINAL", "period_week": None,
        "per_channel": {ch: {
            "spend_twd": 400_000, "impressions": 2_000_000, "cpm_twd": 200.0,
            "net_reach_pct": 40.0, "frequency": 2.5,
            "penetration_pct": 38.0, "buying_audience_000": 8500,
        }},
    }]})
    assert r.status_code == 200, r.text

    body = _summary(client, "internal_pitch", "all_adults")
    assert body[ch]["has_profile"] is True, (
        f"{ch} should flip to has_profile=True after actuals recorded"
    )
    assert body[ch]["confidence_score"] is not None
    assert body[ch]["confidence_score"] > 0
    assert body[ch]["bucket"] in {"high", "mid", "low"}
    assert body[ch]["metrics"], "metrics list must be non-empty"

    # Other channels untouched in the same call stay empty.
    other_ch = next((c for c in body if c != ch), None)
    assert other_ch is not None
    assert body[other_ch]["has_profile"] is False


def test_summary_scoped_per_owner(client: TestClient, owner_id):
    """User A's profiles must not bleed into user B's summary."""
    # User A records actuals for (internal_pitch × all_adults).
    _, plan = finish_manual_plan(owner_id,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    r = client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
        "scope": "FINAL", "period_week": None,
        "per_channel": {ch: {
            "spend_twd": 400_000, "impressions": 2_000_000, "cpm_twd": 200.0,
            "net_reach_pct": 40.0, "frequency": 2.5,
            "penetration_pct": 38.0, "buying_audience_000": 8500,
        }},
    }]})
    assert r.status_code == 200

    # User B logs in — must see ZERO profiles for the same scope.
    from app.services import storage
    storage.create_user(name="user_b", api_key="__user_b_key__")
    from fastapi.testclient import TestClient
    from app.main import app
    c_b = TestClient(app)
    c_b.headers.update({"X-API-Key": "__user_b_key__"})

    body = _summary(c_b, "internal_pitch", "all_adults")
    assert all(entry["has_profile"] is False for entry in body.values()), (
        "User B must not see user A's calibration profiles."
    )
