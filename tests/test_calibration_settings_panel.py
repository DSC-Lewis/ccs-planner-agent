"""TS-33 · Calibration Settings panel (PRD v6 · FR-34).

Planners need to inspect and tune the learning loop from the UI:
  · global / per-client / per-channel half_life override
  · confidence threshold override
  · per-observation weight pinning
Without dropping into a DB shell or admin CLI.

Backend surface:
  GET   /api/calibration/settings                 — user's config tree
  PUT   /api/calibration/settings                 — upsert global/per-scope settings
  GET   /api/calibration/profiles                 — all profiles (scoped)
  GET   /api/calibration/observations             — filter by (client,target,channel,metric)
  PATCH /api/calibration/observations/{id}        — weight_override pin

Frontend surface: static analysis — look for the expected copy + the
half-life slider + observation drawer wiring.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ---------- Settings endpoint ----------

def test_settings_endpoint_returns_defaults(client: TestClient, owner_id, project_id):
    r = client.get("/api/calibration/settings")
    assert r.status_code == 200
    body = r.json()
    # Default global half-life is 180 days per PRD.
    assert body.get("global", {}).get("half_life_days") == 180
    thresholds = body.get("global", {}).get("thresholds") or {}
    assert thresholds.get("high") == 70
    assert thresholds.get("mid") == 40


def test_settings_upsert_global_half_life(client, owner_id, project_id):
    r = client.put("/api/calibration/settings",
                   json={"scope": "global", "half_life_days": 90})
    assert r.status_code == 200
    again = client.get("/api/calibration/settings").json()
    assert again["global"]["half_life_days"] == 90


def test_settings_upsert_per_client(client, owner_id, project_id):
    r = client.put("/api/calibration/settings", json={
        "scope": "client",
        "client_id": "internal_pitch",
        "half_life_days": 60,
    })
    assert r.status_code == 200
    body = client.get("/api/calibration/settings").json()
    per_client = {c["client_id"]: c for c in body.get("per_client", [])}
    assert per_client["internal_pitch"]["half_life_days"] == 60


def test_settings_upsert_per_channel_triple(client, owner_id, project_id):
    r = client.put("/api/calibration/settings", json={
        "scope": "channel",
        "client_id": "internal_pitch",
        "target_id": "all_adults",
        "channel_id": "tv_advertising",
        "half_life_days": 30,
    })
    assert r.status_code == 200


def test_settings_reset_scope_removes_override(client, owner_id, project_id):
    client.put("/api/calibration/settings", json={
        "scope": "client", "client_id": "internal_pitch",
        "half_life_days": 60,
    })
    # Reset → row removed.
    r = client.delete("/api/calibration/settings",
                      params={"scope": "client", "client_id": "internal_pitch"})
    assert r.status_code == 200
    body = client.get("/api/calibration/settings").json()
    per_client = {c["client_id"]: c for c in body.get("per_client", [])}
    assert "internal_pitch" not in per_client


# ---------- Profile + observation listing ----------

def test_profiles_endpoint_lists_all_profiles(client, owner_id, project_id):
    from ._v6_helpers import any_channel_id, finish_manual_plan
    _, plan = finish_manual_plan(owner_id,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
        "scope": "FINAL", "period_week": None,
        "per_channel": {ch: {
            "spend_twd": 400_000, "impressions": 2_000_000, "cpm_twd": 200.0,
            "net_reach_pct": 40.0, "frequency": 2.5,
            "penetration_pct": 38.0, "buying_audience_000": 8500,
        }},
    }]})
    r = client.get("/api/calibration/profiles")
    assert r.status_code == 200
    rows = r.json()
    assert any(row["client_id"] == "internal_pitch"
               and row["target_id"] == "all_adults"
               and row["channel_id"] == ch
               for row in rows)


def test_observations_listing_scoped_to_owner(client, owner_id, project_id):
    from app.main import app
    from app.services import storage
    from ._v6_helpers import any_channel_id, finish_manual_plan

    _, plan = finish_manual_plan(owner_id)
    ch = any_channel_id(plan)
    client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
        "scope": "FINAL", "period_week": None,
        "per_channel": {ch: {
            "spend_twd": 1.0, "impressions": 1, "cpm_twd": 1.0,
            "net_reach_pct": 1.0, "frequency": 1.0,
            "penetration_pct": 1.0, "buying_audience_000": 1,
        }},
    }]})

    # Same user sees rows.
    r = client.get("/api/calibration/observations",
                   params={"client_id": "internal_pitch",
                           "target_id": "all_adults",
                           "channel_id": ch, "metric": "cpm_twd"})
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Different user sees none.
    storage.ensure_admin(name="dave", api_key="dave-key")
    dave = TestClient(app)
    dave.headers.update({"X-API-Key": "dave-key"})
    r2 = dave.get("/api/calibration/observations",
                  params={"client_id": "internal_pitch",
                          "target_id": "all_adults",
                          "channel_id": ch, "metric": "cpm_twd"})
    assert r2.status_code == 200
    assert r2.json() == []


def test_patch_observation_pin_weight(client, owner_id, project_id):
    from ._v6_helpers import any_channel_id, finish_manual_plan
    _, plan = finish_manual_plan(owner_id)
    ch = any_channel_id(plan)
    client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
        "scope": "FINAL", "period_week": None,
        "per_channel": {ch: {
            "spend_twd": 1.0, "impressions": 1, "cpm_twd": 1.0,
            "net_reach_pct": 1.0, "frequency": 1.0,
            "penetration_pct": 1.0, "buying_audience_000": 1,
        }},
    }]})
    obs = client.get("/api/calibration/observations", params={
        "client_id": "internal_pitch", "target_id": "all_adults",
        "channel_id": ch, "metric": "cpm_twd",
    }).json()
    assert obs, "No observations returned"
    r = client.patch(f"/api/calibration/observations/{obs[0]['id']}",
                     json={"weight_override": 0.0})
    assert r.status_code == 200
    again = client.get("/api/calibration/observations", params={
        "client_id": "internal_pitch", "target_id": "all_adults",
        "channel_id": ch, "metric": "cpm_twd",
    }).json()
    assert again[0]["weight_override"] == 0.0


# ---------- Frontend shell ----------

def test_settings_panel_entry_exists():
    """The bare word 'Calibration' already appears in app.js (CALIBRATION
    step key). The *panel* needs a more specific marker — a function that
    opens it, or the localised 校正設定 label."""
    src = _js()
    markers = ["openCalibrationSettings", "renderCalibrationSettings",
               "校正設定", "校準設定"]
    assert any(m in src for m in markers), (
        f"Expected a Calibration Settings entry point (one of {markers})"
    )


def test_half_life_slider_rendered():
    src = _js()
    markers = ["half_life", "halfLife", "半衰期", "近期權重"]
    assert any(m in src for m in markers), (
        f"Expected a half-life slider control (one of {markers})"
    )


def test_settings_panel_calls_settings_endpoint():
    src = _js()
    assert "/api/calibration/settings" in src, (
        "Settings panel must hit /api/calibration/settings"
    )


def test_observation_drawer_references_weight_override():
    """The planner can inspect observations and pin weights — the UI must
    expose a weight_override field binding."""
    src = _js()
    assert "weight_override" in src or "weightOverride" in src, (
        "Observation drawer must bind to weight_override for pinning"
    )


def test_confidence_badge_renders_bucket_colour():
    """FR-30b — traffic-light colours must appear in the reports code path."""
    src = _js()
    # Expect at least one of the bucket names to be referenced (covered
    # by data-returned-from-backend OR literal zh-Hant copy).
    hints = ["高信心", "中等信心", "資料不足", "confidence-high",
             "confidence-mid", "confidence-low"]
    assert any(h in src for h in hints), (
        f"Confidence badge copy missing (expected one of {hints})"
    )
