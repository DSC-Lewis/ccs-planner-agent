"""TS-28 · Planned-vs-actual report math (PRD v6 · FR-29).

The report endpoint diffs plan allocations against recorded actuals, computes
per-channel and aggregate variance, and prefers a FINAL record over the sum
of WEEKLY rows when both exist.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ._v6_helpers import any_channel_id, finish_manual_plan


def _final(plan, *, spend, impressions, net_reach, penetration):
    ch = any_channel_id(plan)
    return {
        "scope": "FINAL",
        "period_week": None,
        "per_channel": {
            ch: {
                "spend_twd": spend,
                "impressions": impressions,
                "cpm_twd": (spend / impressions * 1000) if impressions else 0,
                "net_reach_pct": net_reach,
                "frequency": 2.5,
                "penetration_pct": penetration,
                "buying_audience_000": 8500,
            }
        },
    }


def _weekly(plan, *, week, spend, impressions, net_reach=10.0, penetration=35.0):
    ch = any_channel_id(plan)
    return {
        "scope": "WEEKLY",
        "period_week": week,
        "per_channel": {
            ch: {
                "spend_twd": spend,
                "impressions": impressions,
                "cpm_twd": (spend / impressions * 1000) if impressions else 0,
                "net_reach_pct": net_reach,
                "frequency": 1.8,
                "penetration_pct": penetration,
                "buying_audience_000": 8500,
            }
        },
    }


def test_report_empty_when_no_actuals(client: TestClient, owner_id, project_id):
    """No actuals → report returns a sentinel, not a 500."""
    _, plan = finish_manual_plan(owner_id)
    r = client.get(f"/api/plans/{plan.id}/report")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "no_actuals"
    # Planned numbers still echoed so the client can render an "empty" state.
    assert body.get("planned", {}).get("total_budget_twd") == plan.summary.total_budget_twd


def test_report_per_channel_variance(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    ch = any_channel_id(plan)
    planned = plan.allocations[0]

    # Over-spent 10% on channel 0.
    actual_spend = planned.total_budget_twd * 1.10
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final(plan, spend=actual_spend,
                                        impressions=planned.total_impressions,
                                        net_reach=planned.net_reach_pct + 2,
                                        penetration=38.0)]})

    r = client.get(f"/api/plans/{plan.id}/report")
    assert r.status_code == 200
    body = r.json()
    per_ch = {row["channel_id"]: row for row in body["per_channel"]}
    assert ch in per_ch
    row = per_ch[ch]
    # Variance percent = (actual - planned) / planned * 100  → ~+10.0
    assert abs(row["spend_variance_pct"] - 10.0) < 0.5


def test_report_aggregate_deltas(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    ch = any_channel_id(plan)

    # Exactly on-plan spend, +5pp net reach.
    r = client.put(f"/api/plans/{plan.id}/actuals",
                   json={"records": [_final(plan,
                                            spend=plan.summary.total_budget_twd,
                                            impressions=plan.summary.total_impressions,
                                            net_reach=plan.summary.net_reach_pct + 5,
                                            penetration=40.0)]})
    assert r.status_code == 200

    body = client.get(f"/api/plans/{plan.id}/report").json()
    agg = body["aggregate"]
    assert abs(agg["spend_variance_pct"]) < 0.5
    assert abs(agg["net_reach_delta_pp"] - 5) < 0.5


def test_report_prefers_final_over_weekly(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id, weeks=4)
    ch = any_channel_id(plan)

    # Weekly totals sum to 400k, but FINAL says 420k. FINAL wins.
    for w in range(1, 5):
        client.put(f"/api/plans/{plan.id}/actuals",
                   json={"records": [_weekly(plan, week=w,
                                             spend=100_000,
                                             impressions=500_000)]})
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final(plan,
                                        spend=420_000,
                                        impressions=2_000_000,
                                        net_reach=40.0,
                                        penetration=40.0)]})
    body = client.get(f"/api/plans/{plan.id}/report").json()
    per_ch = {row["channel_id"]: row for row in body["per_channel"]}
    assert per_ch[ch]["actual_spend_twd"] == 420_000, (
        "FINAL record should take precedence over sum-of-weekly"
    )
    assert body.get("source") == "final"


def test_report_falls_back_to_weekly_sum_when_final_missing(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id, weeks=4)
    ch = any_channel_id(plan)

    for w in range(1, 5):
        client.put(f"/api/plans/{plan.id}/actuals",
                   json={"records": [_weekly(plan, week=w,
                                             spend=100_000,
                                             impressions=500_000)]})
    body = client.get(f"/api/plans/{plan.id}/report").json()
    per_ch = {row["channel_id"]: row for row in body["per_channel"]}
    assert per_ch[ch]["actual_spend_twd"] == 400_000, (
        "Without FINAL, the report should sum the weekly rows"
    )
    assert body.get("source") == "weekly"


def test_report_variance_badge_buckets(client, owner_id, project_id):
    """Per PRD §5 — variance colour: ≤±10 green, ±10-25 amber, >25 red."""
    _, plan = finish_manual_plan(owner_id)
    # +30% blows past amber → red bucket
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final(plan,
                                        spend=plan.summary.total_budget_twd * 1.30,
                                        impressions=plan.summary.total_impressions,
                                        net_reach=plan.summary.net_reach_pct,
                                        penetration=40.0)]})
    body = client.get(f"/api/plans/{plan.id}/report").json()
    assert body["aggregate"]["spend_badge"] == "red"


def test_report_html_endpoint_serves_printable_view(client, owner_id, project_id):
    """FR-29 — printable HTML so a planner can export/print without JS deps."""
    _, plan = finish_manual_plan(owner_id)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final(plan,
                                        spend=plan.summary.total_budget_twd,
                                        impressions=plan.summary.total_impressions,
                                        net_reach=plan.summary.net_reach_pct,
                                        penetration=40.0)]})
    r = client.get(f"/api/plans/{plan.id}/report.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Plan vs Actual" in r.text or "成效回顧" in r.text
