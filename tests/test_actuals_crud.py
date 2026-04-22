"""TS-27 · Actuals CRUD + schema migration (PRD v6 · FR-27, FR-28, NFR-7.1).

Planners record what actually ran — weekly during-flight, or a single
final snapshot at campaign end, or both. The service must keep weekly
rows unique per (plan × week), enforce at-most-one FINAL per plan, and
preserve replaced rows in a history table for audit.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import storage

from ._v6_helpers import any_channel_id, finish_manual_plan


# ---------- Schema migration ----------

def test_actuals_tables_exist_after_init_schema():
    """FR-27 / NFR-7.1 — idempotent migration creates the new tables."""
    tables = storage.list_tables()
    assert "plan_actuals" in tables, (
        "plan_actuals table must be created by storage.init_schema()"
    )
    assert "plan_actuals_history" in tables, (
        "plan_actuals_history table must be created by storage.init_schema()"
    )


# ---------- PUT + GET ----------

def _weekly_record(plan, *, week: int, spend: float = 100_000,
                   impressions: int = 500_000, net_reach: float = 12.5,
                   penetration: float = 38.0):
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
                "frequency": 2.1,
                "penetration_pct": penetration,
                "buying_audience_000": 8500,
            }
        },
    }


def _final_record(plan, *, spend: float = 400_000):
    ch = any_channel_id(plan)
    return {
        "scope": "FINAL",
        "period_week": None,
        "per_channel": {
            ch: {
                "spend_twd": spend,
                "impressions": 2_000_000,
                "cpm_twd": 200.0,
                "net_reach_pct": 42.0,
                "frequency": 2.8,
                "penetration_pct": 40.0,
                "buying_audience_000": 8500,
            }
        },
        "notes": "Closed out on 2026-03-16",
    }


def test_put_single_final_record(client: TestClient, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    r = client.put(f"/api/plans/{plan.id}/actuals",
                   json={"records": [_final_record(plan)]})
    assert r.status_code == 200, r.text
    listing = client.get(f"/api/plans/{plan.id}/actuals").json()
    assert len(listing) == 1
    assert listing[0]["scope"] == "FINAL"


def test_put_weekly_records_batch(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id, weeks=4)
    records = [_weekly_record(plan, week=w) for w in range(1, 5)]
    r = client.put(f"/api/plans/{plan.id}/actuals", json={"records": records})
    assert r.status_code == 200
    got = client.get(f"/api/plans/{plan.id}/actuals").json()
    weeks = sorted(x["period_week"] for x in got if x["scope"] == "WEEKLY")
    assert weeks == [1, 2, 3, 4]


def test_weekly_and_final_can_coexist(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_weekly_record(plan, week=1)]})
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final_record(plan)]})
    got = client.get(f"/api/plans/{plan.id}/actuals").json()
    scopes = sorted(x["scope"] for x in got)
    assert scopes == ["FINAL", "WEEKLY"]


# ---------- Uniqueness + replace semantics ----------

def test_second_put_same_week_replaces_and_writes_history(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_weekly_record(plan, week=1, spend=100_000)]})
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_weekly_record(plan, week=1, spend=123_456)]})

    listing = client.get(f"/api/plans/{plan.id}/actuals").json()
    week1 = [x for x in listing if x["scope"] == "WEEKLY" and x["period_week"] == 1]
    assert len(week1) == 1, "Only the latest week-1 record should survive"
    ch = any_channel_id(plan)
    assert week1[0]["per_channel"][ch]["spend_twd"] == 123_456

    history = client.get(f"/api/plans/{plan.id}/actuals/history").json()
    assert any(h["per_channel"][ch]["spend_twd"] == 100_000 for h in history), (
        "Superseded weekly records must be preserved in actuals history"
    )


def test_second_put_final_replaces(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final_record(plan, spend=300_000)]})
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final_record(plan, spend=333_333)]})
    finals = [x for x in client.get(f"/api/plans/{plan.id}/actuals").json()
              if x["scope"] == "FINAL"]
    assert len(finals) == 1, "At most one FINAL record per plan"
    ch = any_channel_id(plan)
    assert finals[0]["per_channel"][ch]["spend_twd"] == 333_333


# ---------- Validation ----------

def test_invalid_week_number_rejected(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id, weeks=4)
    bad = _weekly_record(plan, week=99)  # plan.weeks == 4
    r = client.put(f"/api/plans/{plan.id}/actuals", json={"records": [bad]})
    assert r.status_code in (400, 422), (
        f"Week > brief.weeks must be rejected; got {r.status_code}"
    )


def test_weekly_without_period_week_rejected(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    bad = _weekly_record(plan, week=1)
    bad["period_week"] = None  # WEEKLY scope must specify a week
    r = client.put(f"/api/plans/{plan.id}/actuals", json={"records": [bad]})
    assert r.status_code in (400, 422)


def test_final_with_period_week_rejected(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    bad = _final_record(plan)
    bad["period_week"] = 2  # FINAL must not carry a week
    r = client.put(f"/api/plans/{plan.id}/actuals", json={"records": [bad]})
    assert r.status_code in (400, 422)


# ---------- Delete ----------

def test_delete_record_removes_from_current_but_keeps_history(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_weekly_record(plan, week=1)]})
    listing = client.get(f"/api/plans/{plan.id}/actuals").json()
    rid = listing[0]["id"]

    r = client.delete(f"/api/plans/{plan.id}/actuals/{rid}")
    assert r.status_code == 200
    assert client.get(f"/api/plans/{plan.id}/actuals").json() == []

    history = client.get(f"/api/plans/{plan.id}/actuals/history").json()
    assert any(h["id"] == rid for h in history), (
        "Deleted record must survive in history"
    )


# ---------- Cross-tenant access ----------

def test_cross_user_cannot_read_or_write_actuals(client, owner_id, project_id):
    _, plan = finish_manual_plan(owner_id)
    client.put(f"/api/plans/{plan.id}/actuals",
               json={"records": [_final_record(plan)]})

    storage.ensure_admin(name="malory", api_key="malory-key")
    malory = TestClient(app)
    malory.headers.update({"X-API-Key": "malory-key"})

    # Read blocked
    r = malory.get(f"/api/plans/{plan.id}/actuals")
    assert r.status_code in (403, 404)

    # Write blocked
    r = malory.put(f"/api/plans/{plan.id}/actuals",
                   json={"records": [_final_record(plan, spend=0)]})
    assert r.status_code in (403, 404)


# ---------- 404 when no actuals ----------

def test_get_actuals_returns_empty_list_when_none(client, owner_id, project_id):
    """Per PRD: GET returns 200 with [] when no records exist (not 404) so
    the frontend can render the empty-state without special-casing."""
    _, plan = finish_manual_plan(owner_id)
    r = client.get(f"/api/plans/{plan.id}/actuals")
    assert r.status_code == 200
    assert r.json() == []
