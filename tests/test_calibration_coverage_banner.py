"""TS-31 · "Recommend-fill" banner (PRD v6 · FR-32).

When a planner selects a (client, target) combination that has zero prior
actuals recorded, we strongly recommend they fill Channel Calibration /
Penetration Adjustment because those two knobs dominate estimate accuracy.

Backend: /api/calibration/coverage answers `{has_history, n}`.
Frontend: a dedicated banner constant + fill-now CTA exists in app.js.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ._v6_helpers import any_channel_id, finish_manual_plan


APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


# ---------- Backend endpoint ----------

def test_coverage_endpoint_fresh_combo_has_no_history(client: TestClient, owner_id, project_id):
    """No one has ever run a plan for (internal_pitch × all_adults) in this
    fresh DB, so the endpoint should report has_history=false."""
    r = client.get("/api/calibration/coverage",
                   params={"client_id": "internal_pitch",
                           "target_id": "all_adults"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("has_history") is False
    assert body.get("n", 0) == 0


def test_coverage_endpoint_sees_actuals_after_recording(client, owner_id, project_id):
    """Once a plan for (internal_pitch × all_adults) has FINAL actuals,
    a subsequent coverage query must report has_history=true."""
    _, plan = finish_manual_plan(owner_id,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
        "scope": "FINAL",
        "period_week": None,
        "per_channel": {ch: {
            "spend_twd": 400_000, "impressions": 2_000_000, "cpm_twd": 200.0,
            "net_reach_pct": 40.0, "frequency": 2.5,
            "penetration_pct": 38.0, "buying_audience_000": 8500,
        }},
    }]})

    r = client.get("/api/calibration/coverage",
                   params={"client_id": "internal_pitch",
                           "target_id": "all_adults"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("has_history") is True
    assert body.get("n", 0) >= 1


def test_coverage_endpoint_scoped_per_client_target(client, owner_id, project_id):
    """A history for (client A × target A) must NOT leak into (client A × target B)."""
    _, plan = finish_manual_plan(owner_id,
                                 client_id="internal_pitch",
                                 target_ids=["all_adults"])
    ch = any_channel_id(plan)
    client.put(f"/api/plans/{plan.id}/actuals", json={"records": [{
        "scope": "FINAL", "period_week": None,
        "per_channel": {ch: {
            "spend_twd": 100.0, "impressions": 1000, "cpm_twd": 100.0,
            "net_reach_pct": 1.0, "frequency": 1.0,
            "penetration_pct": 1.0, "buying_audience_000": 1,
        }},
    }]})

    r = client.get("/api/calibration/coverage",
                   params={"client_id": "internal_pitch",
                           "target_id": "ta_30_54_a"})
    assert r.status_code == 200
    assert r.json().get("has_history") is False, (
        "Different target audience must not share calibration history"
    )


# ---------- Frontend copy ----------

def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_banner_constant_exists():
    src = _js()
    assert "RECOMMEND_FILL_BANNER" in src or "recommendFillBanner" in src, (
        "Expected a dedicated banner constant/helper (RECOMMEND_FILL_BANNER)"
    )


def test_banner_mentions_calibration_and_penetration():
    """Users must see WHY they should fill it — reference the two knobs.

    Scope the check to the banner's own body so pre-existing uses of
    Calibration / Penetration elsewhere in app.js can't make the test
    pass trivially. We find the banner body by splitting around the
    banner constant name and checking a 600-char window after it.
    """
    src = _js()
    assert "RECOMMEND_FILL_BANNER" in src or "recommendFillBanner" in src, (
        "RECOMMEND_FILL_BANNER constant must be defined first"
    )
    marker = "RECOMMEND_FILL_BANNER" if "RECOMMEND_FILL_BANNER" in src else "recommendFillBanner"
    idx = src.index(marker)
    window = src[idx: idx + 600]
    hits_calibration = "Calibration" in window or "校正" in window
    hits_penetration = "Penetration" in window or "滲透" in window
    assert hits_calibration, (
        f"Banner body must reference Calibration; window head: {window[:200]}"
    )
    assert hits_penetration, (
        f"Banner body must reference Penetration; window head: {window[:200]}"
    )


def test_banner_has_fill_now_cta():
    src = _js()
    cta_markers = ["Fill now", "現在填", "馬上填", "去填寫", "立即填寫"]
    assert any(m in src for m in cta_markers), (
        f"Banner must have a 'Fill now' CTA (one of {cta_markers})"
    )


def test_banner_calls_coverage_endpoint():
    src = _js()
    assert "/api/calibration/coverage" in src, (
        "Frontend must call /api/calibration/coverage to decide whether to "
        "show the banner."
    )
