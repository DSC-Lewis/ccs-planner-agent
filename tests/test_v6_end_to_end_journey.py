"""End-to-end user journey across the whole v6 feature set.

Gap-audit issues 20 + 21: nothing in the existing suites traces the full
pipeline — overrides on the brief, plan maths using the override, actuals
capture, profile materialisation, CAL endpoint reflecting history,
banner-coverage flip, next-session CPM resolved via the profile.

This test is ONE flow to catch integration regressions that a
unit-layer suite wouldn't. If this fails, something between the layers
drifted.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import (
    ChannelOverride,
    SessionMode,
    StepPayload,
)
from app.services import agent, calibration, optimizer, storage

from ._v6_helpers import any_channel_id, finish_manual_plan


def test_override_then_actuals_then_next_session_calibration(
    client: TestClient, owner_id, project_id
):
    # --- 1. Fresh combo: coverage endpoint reports no history.
    r = client.get(
        "/api/calibration/coverage",
        params={"client_id": "internal_pitch", "target_id": "all_adults"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_history"] is False
    assert body["n"] == 0

    # --- 2. Build a plan with a planner-override on TV CPM.
    session = agent.create_session(SessionMode.MANUAL, owner_id=owner_id)
    session = agent.advance(
        session,
        StepPayload(survey_id="tw_2025", client_id="internal_pitch"),
        owner_id=owner_id,
    )
    session = agent.advance(
        session,
        StepPayload(
            project_name="e2e-override-journey",
            start_date="2026-02-16",
            weeks=4,
        ),
        owner_id=owner_id,
    )
    session = agent.advance(
        session, StepPayload(target_ids=["all_adults"]), owner_id=owner_id
    )
    session = agent.advance(
        session, StepPayload(planning_type="Reach"), owner_id=owner_id
    )
    channels = ["tv_advertising", "youtube_video_ads", "meta_video_ads"]
    session = agent.advance(
        session, StepPayload(channel_ids=channels), owner_id=owner_id
    )

    # Override CPM for TV — 5x the survey default. This MUST propagate
    # into the computed plan (PR C · Issue 1 regression).
    tv_default_cpm = optimizer.default_channel_cpm("tv_advertising")
    override_cpm = tv_default_cpm * 5
    session = agent.advance(
        session,
        StepPayload(
            overrides={
                "tv_advertising": ChannelOverride(cpm_twd=override_cpm),
            }
        ),
        owner_id=owner_id,
    )
    assert "tv_advertising" in session.brief.overrides
    assert session.brief.overrides["tv_advertising"].cpm_twd == override_cpm

    # Continue through calibration + manual_plan to REVIEW.
    session = agent.advance(session, StepPayload(), owner_id=owner_id)
    session = agent.advance(
        session,
        StepPayload(
            weekly_budgets={
                "tv_advertising":    [100_000] * 4,
                "youtube_video_ads": [100_000] * 4,
                "meta_video_ads":    [100_000] * 4,
            }
        ),
        owner_id=owner_id,
    )
    assert session.plan_id

    plan = storage.get_plan(session.plan_id, owner_id=owner_id)
    assert plan is not None

    # --- 3. The override must have changed the plan's TV impressions.
    #     Without override: 400k spend / 83.14 default CPM * 1000 ≈ 4.8M impressions.
    #     With 5× override : 400k spend / (5 × 83.14) * 1000   ≈ 962k impressions.
    #     Allow ±10% fuzz for integer rounding.
    tv_alloc = next(a for a in plan.allocations if a.channel_id == "tv_advertising")

    expected_impr_with_override = int(round(400_000 / override_cpm * 1000))
    actual_impr = tv_alloc.total_impressions
    tolerance = int(expected_impr_with_override * 0.10)
    assert abs(actual_impr - expected_impr_with_override) <= tolerance, (
        f"Override CPM={override_cpm} should yield ~{expected_impr_with_override} "
        f"impressions on 400k spend; got {actual_impr}"
    )

    # Sanity: this is materially LESS than what the default CPM would give.
    expected_impr_default = int(round(400_000 / tv_default_cpm * 1000))
    assert actual_impr < expected_impr_default / 2, (
        f"Override should cut impressions to < half the default path "
        f"(default≈{expected_impr_default}, got {actual_impr})"
    )

    # --- 4. Record FINAL actuals with a realistic CPM (≈default).
    ch = "tv_advertising"
    client.put(
        f"/api/plans/{plan.id}/actuals",
        json={
            "records": [
                {
                    "scope": "FINAL",
                    "period_week": None,
                    "per_channel": {
                        ch: {
                            "spend_twd": 400_000,
                            "impressions": 2_000_000,
                            "cpm_twd": 200.0,  # realistic CPM
                            "net_reach_pct": 41.0,
                            "frequency": 2.6,
                            "penetration_pct": 38.0,
                            "buying_audience_000": 8500,
                        }
                    },
                    "notes": "End-of-campaign wrap",
                }
            ]
        },
    ).raise_for_status()

    # --- 5. Coverage endpoint now reports history AND a confidence score.
    r = client.get(
        "/api/calibration/coverage",
        params={"client_id": "internal_pitch", "target_id": "all_adults"},
    )
    body = r.json()
    assert body["has_history"] is True
    assert body["n"] >= 1
    assert body["confidence_score"] is not None

    # --- 6. CAL channel summary endpoint shows TV calibrated, others not.
    r = client.get(
        "/api/calibration/channel-summary",
        params={"client_id": "internal_pitch", "target_id": "all_adults"},
    )
    summary = r.json()
    assert summary[ch]["has_profile"] is True
    assert summary[ch]["confidence_score"] is not None
    assert "cpm_twd" in summary[ch]["metrics"]
    # YouTube shouldn't have a profile — we didn't record FINAL per-channel
    # actuals for it.
    assert summary["youtube_video_ads"]["has_profile"] is False

    # --- 7. Direct resolver: optimizer.resolve_channel_cpm for a NEW session
    #     (no override) should now surface ~200 instead of the default ~83.
    resolved = optimizer.resolve_channel_cpm(
        channel_id=ch,
        client_id="internal_pitch",
        target_id="all_adults",
        owner_id=owner_id,
    )
    assert abs(resolved - 200.0) < 10.0, (
        f"Fresh session should see calibrated ~200 CPM, not default; got {resolved}"
    )

    # --- 8. Profile exposes the confidence breakdown (Agent B · Issue 8).
    prof = calibration.get_profile(
        client_id="internal_pitch", target_id="all_adults",
        channel_id=ch, metric="cpm_twd", owner_id=owner_id,
    )
    assert prof is not None
    assert prof.confidence_score >= 1
    # One observation → sample_factor > 0 but low (tops at n_eff≈15).
    assert 0 < prof.sample_factor < 1
    # Single observation → cv must be 0 (no variance), consistency = 1.
    assert prof.cv == 0
    assert prof.consistency_factor == 1.0

    # --- 9. Observation endpoint surfaces the enriched fields
    #     (Agent B · Issue 7).
    obs = client.get(
        "/api/calibration/observations",
        params={
            "client_id": "internal_pitch", "target_id": "all_adults",
            "channel_id": ch, "metric": "cpm_twd",
        },
    ).json()
    assert len(obs) >= 1
    row = obs[0]
    assert "effective_weight" in row
    assert "age_days" in row
    # Observation is stamped at brief.end_date (Issue 12), so the
    # age should be positive (end_date < now) but within a reasonable
    # window given we're testing in 2026.
    assert row["age_days"] >= 0


def test_cross_user_isolation_across_full_journey(
    client: TestClient, owner_id, project_id
):
    """Prove the learning loop is tenant-scoped end-to-end."""
    # Alice records actuals.
    _, alice_plan = finish_manual_plan(
        owner_id, client_id="internal_pitch", target_ids=["all_adults"]
    )
    ch = any_channel_id(alice_plan)
    client.put(
        f"/api/plans/{alice_plan.id}/actuals",
        json={
            "records": [
                {
                    "scope": "FINAL", "period_week": None,
                    "per_channel": {
                        ch: {
                            "spend_twd": 500_000, "impressions": 1_000_000,
                            "cpm_twd": 500.0, "net_reach_pct": 50.0,
                            "frequency": 1.0, "penetration_pct": 50.0,
                            "buying_audience_000": 5000,
                        }
                    },
                }
            ]
        },
    ).raise_for_status()

    # Bob logs in fresh.
    storage.ensure_admin(name="bob", api_key="bob-key")
    bob = TestClient(app)
    bob.headers.update({"X-API-Key": "bob-key"})

    # Bob's coverage endpoint shows NOTHING for the same combo.
    r = bob.get(
        "/api/calibration/coverage",
        params={"client_id": "internal_pitch", "target_id": "all_adults"},
    )
    assert r.json()["has_history"] is False

    # Bob's channel-summary — no calibrated channels.
    r = bob.get(
        "/api/calibration/channel-summary",
        params={"client_id": "internal_pitch", "target_id": "all_adults"},
    )
    summary = r.json()
    for entry in summary.values():
        assert entry["has_profile"] is False

    # Bob's optimizer resolves to the static default (not Alice's 500).
    bob_user = storage.get_user_by_name("bob")
    resolved = optimizer.resolve_channel_cpm(
        channel_id=ch, client_id="internal_pitch",
        target_id="all_adults", owner_id=bob_user.id,
    )
    assert abs(resolved - optimizer.default_channel_cpm(ch)) < 0.01
