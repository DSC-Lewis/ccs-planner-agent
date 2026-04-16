"""TS-18 · SQLite storage layer (FR-20 / NFR-5.1 / NFR-5.3)."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from app.schemas import AgentSession, Plan, PlanKind, SessionMode


def _fresh_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CCS_DATABASE_PATH", str(tmp_path / "ccs.db"))
    # Legacy JSON path goes somewhere empty so migration is a no-op.
    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "legacy.json"))
    import app.config
    import app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.init_schema()
    return store


def test_idempotent_schema_init_creates_all_tables(monkeypatch, tmp_path):
    store = _fresh_store(monkeypatch, tmp_path)
    store.init_schema()  # twice — must not error
    tables = store.list_tables()
    expected = {"users", "projects", "sessions", "plans", "conversations", "schema_version"}
    assert expected.issubset(set(tables)), f"missing tables: {expected - set(tables)}"


def test_save_and_fetch_session_round_trip(monkeypatch, tmp_path):
    store = _fresh_store(monkeypatch, tmp_path)
    # Seed a default owner so scoping doesn't bite us.
    u = store.create_user(name="t1", api_key="k-1", is_admin=True)
    pr = store.create_project(owner_id=u.id, name="P")

    s = AgentSession(id="", mode=SessionMode.MANUAL)
    s.brief.project_name = "round-trip"
    saved = store.save_session(s, owner_id=u.id, project_id=pr.id)
    assert saved.id
    fetched = store.get_session(saved.id, owner_id=u.id)
    assert fetched.brief.project_name == "round-trip"


def test_save_and_fetch_plan_round_trip(monkeypatch, tmp_path):
    store = _fresh_store(monkeypatch, tmp_path)
    u = store.create_user(name="t1", api_key="k-1", is_admin=True)
    pr = store.create_project(owner_id=u.id, name="P")
    s = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)
    p = Plan(brief_id=s.id, name="Plan 1", kind=PlanKind.MANUAL)
    saved = store.save_plan(p, owner_id=u.id)
    assert saved.id
    fetched = store.get_plan(saved.id, owner_id=u.id)
    assert fetched.name == "Plan 1"


def test_listing_respects_owner_id_filter(monkeypatch, tmp_path):
    store = _fresh_store(monkeypatch, tmp_path)
    alice = store.create_user(name="alice", api_key="ka", is_admin=True)
    bob   = store.create_user(name="bob",   api_key="kb", is_admin=False)
    pa = store.create_project(owner_id=alice.id, name="A")
    pb = store.create_project(owner_id=bob.id,   name="B")
    store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                       owner_id=alice.id, project_id=pa.id)
    store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                       owner_id=bob.id,   project_id=pb.id)

    assert len(store.list_sessions(owner_id=alice.id)) == 1
    assert len(store.list_sessions(owner_id=bob.id)) == 1
    # Alice cannot fetch Bob's sessions.
    bob_sessions = store.list_sessions(owner_id=bob.id)
    assert store.get_session(bob_sessions[0].id, owner_id=alice.id) is None


def test_ttl_sweep_still_purges_stale_rows(monkeypatch, tmp_path):
    """NFR-2 carry-over: retention still works after the SQLite rewrite."""
    monkeypatch.setenv("CCS_SESSION_TTL_SECONDS", "60")
    store = _fresh_store(monkeypatch, tmp_path)
    u = store.create_user(name="t1", api_key="k-1", is_admin=True)
    pr = store.create_project(owner_id=u.id, name="P")

    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)
    fake_now[0] += 120
    store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                       owner_id=u.id, project_id=pr.id)
    ids = [s.id for s in store.list_sessions(owner_id=u.id)]
    assert a.id not in ids, "stale session should be purged"


def test_migration_from_legacy_json_is_idempotent(monkeypatch, tmp_path):
    """TC-18.6 — running the importer twice leaves the DB unchanged."""
    legacy = tmp_path / "storage.json"
    legacy.write_text(json.dumps({
        "sessions": {
            "ses_legacy1": {
                "id": "ses_legacy1", "mode": "manual", "step": "survey_client",
                "brief": {"survey_id": None, "client_id": None, "project_name": "old",
                          "start_date": "2026-02-16", "weeks": 4, "end_date": "2026-03-15",
                          "target_ids": ["all_adults"], "planning_type": "Reach",
                          "comms": {"brand_strength": 6, "parent_brand": 5,
                                    "competitor_clutter": 5, "new_creative": 5,
                                    "message_complexity": 5, "kpi_ids": []},
                          "channel_ids": []},
                "manual_input": {"weekly_budgets": {}},
                "automatic_input": {"criterion_id": "net_reach", "strategy_id": "global_plan",
                                    "total_budget_twd": 0.0, "mandatory_channel_ids": [],
                                    "optional_channel_ids": [], "constraints": {}},
                "plan_id": None, "history": [],
                "_ts": 1234567890.0,
            }
        },
        "plans": {},
    }, ensure_ascii=False))

    monkeypatch.setenv("CCS_DATABASE_PATH", str(tmp_path / "ccs.db"))
    monkeypatch.setenv("CCS_STORAGE_PATH", str(legacy))
    import app.config
    import app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.init_schema()

    n1 = store.import_legacy_json()
    n2 = store.import_legacy_json()
    assert n1 == 1, f"first run should import 1 session, got {n1}"
    assert n2 == 0, f"second run should be a no-op, got {n2}"


def test_legacy_import_creates_non_admin_owner(monkeypatch, tmp_path):
    """Code-review #2: a migrated "legacy" user should not get admin rights.
    The key is random so there's no realistic abuse path, but semantically
    imported historical data shouldn't grant elevated privileges."""
    import json as _json
    legacy = tmp_path / "storage.json"
    legacy.write_text(_json.dumps({"sessions": {}, "plans": {}}))

    monkeypatch.setenv("CCS_DATABASE_PATH", str(tmp_path / "ccs.db"))
    monkeypatch.setenv("CCS_STORAGE_PATH", str(legacy))
    import app.config
    import app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.init_schema()
    # Import even an empty file just to materialise the user.
    store.import_legacy_json()

    owner = store.get_user_by_name("legacy")
    assert owner is not None
    assert owner.is_admin is False, (
        "Imported legacy user should not carry admin rights."
    )


def test_parametrised_queries_reject_injection(monkeypatch, tmp_path):
    """NFR-5.1 — no f-string SQL. A name full of SQL shouldn't delete tables."""
    store = _fresh_store(monkeypatch, tmp_path)
    u = store.create_user(name="alice", api_key="ka", is_admin=True)
    evil = "'; DROP TABLE users; --"
    pr = store.create_project(owner_id=u.id, name=evil)
    # All of these must still work after the "injection":
    assert store.get_project(pr.id, owner_id=u.id).name == evil
    assert "users" in store.list_tables()
