"""v7 · Review dashboard — single-plan augmented endpoint.

The Compare view (/api/plans/compare) requires ≥ 2 plans. When a planner
finishes ONE plan and lands on the Review step we still want the same
kind of charts: summary bars, per-channel budget, frequency
distribution, weekly GRP. This test suite locks the contract of a
single-plan equivalent — ``GET /api/plans/{id}/augmented``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import storage

from ._v6_helpers import any_channel_id, finish_manual_plan


def test_augmented_endpoint_returns_plan_plus_derived_stats(
    client: TestClient, owner_id, project_id
):
    _, plan = finish_manual_plan(owner_id)
    r = client.get(f"/api/plans/{plan.id}/augmented")
    assert r.status_code == 200, r.text
    body = r.json()
    # Plan itself
    assert body["id"] == plan.id
    assert body["name"] == plan.name
    assert "allocations" in body and body["allocations"]
    assert "summary" in body
    # Derived stats (same shape Compare already returns per plan)
    assert "frequency_distribution" in body
    assert isinstance(body["frequency_distribution"], list)
    assert "duplication" in body
    assert isinstance(body["duplication"], dict)
    assert "weekly_grp" in body
    assert isinstance(body["weekly_grp"], list)


def test_augmented_endpoint_404_on_missing_plan(client: TestClient, owner_id, project_id):
    r = client.get("/api/plans/plan_does_not_exist/augmented")
    assert r.status_code == 404


def test_augmented_endpoint_is_owner_scoped(client: TestClient, owner_id, project_id):
    """Cross-tenant access must 404 (keeps plan existence private)."""
    _, plan = finish_manual_plan(owner_id)
    storage.ensure_admin(name="malory", api_key="malory-augmented-key")
    other = TestClient(app)
    other.headers.update({"X-API-Key": "malory-augmented-key"})
    r = other.get(f"/api/plans/{plan.id}/augmented")
    assert r.status_code == 404


def test_augmented_frequency_distribution_shape(client: TestClient, owner_id, project_id):
    """frequency_distribution must be a list of {threshold, reach_pct}."""
    _, plan = finish_manual_plan(owner_id)
    body = client.get(f"/api/plans/{plan.id}/augmented").json()
    fd = body["frequency_distribution"]
    assert len(fd) >= 1
    for row in fd:
        assert "threshold" in row or "reach_pct" in row, (
            f"Frequency row must carry threshold + reach_pct; got {row}"
        )


def test_augmented_weekly_grp_shape(client: TestClient, owner_id, project_id):
    """weekly_grp must be a list of {week, grp} — one row per plan week."""
    _, plan = finish_manual_plan(owner_id, weeks=4)
    body = client.get(f"/api/plans/{plan.id}/augmented").json()
    weekly = body["weekly_grp"]
    assert len(weekly) == 4
    for row in weekly:
        assert "week" in row
        assert "grp" in row


def test_augmented_duplication_references_plan_channels(
    client: TestClient, owner_id, project_id
):
    """duplication keys are channel_ids from the plan."""
    _, plan = finish_manual_plan(owner_id)
    body = client.get(f"/api/plans/{plan.id}/augmented").json()
    alloc_ids = {a["channel_id"] for a in body["allocations"]}
    # At least one plan channel must show up in the duplication table.
    assert alloc_ids & set(body["duplication"].keys()), (
        f"Duplication must reference plan channels; got keys "
        f"{list(body['duplication'].keys())}"
    )
