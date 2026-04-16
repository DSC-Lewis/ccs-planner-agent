"""TS-13 · Session TTL retention (FR-9 / NFR-3.4).

Rewritten for the v4 SQLite storage. The TTL semantics stay the same.
"""
from __future__ import annotations

import importlib

import pytest

from app.schemas import AgentSession, Plan, PlanKind, SessionMode


def _fresh_store(monkeypatch, tmp_path, ttl="60"):
    monkeypatch.setenv("CCS_DATABASE_PATH", str(tmp_path / "ccs.db"))
    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "legacy.json"))
    monkeypatch.setenv("CCS_SESSION_TTL_SECONDS", ttl)
    import app.config
    import app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.init_schema()
    u = store.ensure_admin(name="admin", api_key="x")
    pr = store.ensure_default_project(u.id)
    return store, u, pr


def test_default_ttl_is_seven_days():
    from app.services import storage
    assert storage.DEFAULT_SESSION_TTL_SECONDS == 7 * 86400


def test_fresh_session_is_not_purged(monkeypatch, tmp_path):
    store, u, pr = _fresh_store(monkeypatch, tmp_path)
    s = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)
    assert any(x.id == s.id for x in store.list_sessions(owner_id=u.id))


def test_session_older_than_ttl_is_purged_on_next_write(monkeypatch, tmp_path):
    store, u, pr = _fresh_store(monkeypatch, tmp_path)
    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)
    fake_now[0] += 120
    b = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)
    ids = [x.id for x in store.list_sessions(owner_id=u.id)]
    assert b.id in ids
    assert a.id not in ids


def test_orphaned_plans_are_purged_with_their_sessions(monkeypatch, tmp_path):
    store, u, pr = _fresh_store(monkeypatch, tmp_path)
    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)
    plan = store.save_plan(Plan(brief_id=a.id, name="Plan 1", kind=PlanKind.MANUAL),
                           owner_id=u.id)
    assert store.get_plan(plan.id, owner_id=u.id) is not None

    fake_now[0] += 120
    store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                       owner_id=u.id, project_id=pr.id)
    assert store.get_plan(plan.id, owner_id=u.id) is None


def test_resaving_the_same_session_does_not_purge_it(monkeypatch, tmp_path):
    store, u, pr = _fresh_store(monkeypatch, tmp_path)
    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)

    fake_now[0] += 120
    a.brief.project_name = "long-edit"
    store.save_session(a, owner_id=u.id, project_id=pr.id)

    ids = [x.id for x in store.list_sessions(owner_id=u.id)]
    assert a.id in ids
    reloaded = store.get_session(a.id, owner_id=u.id)
    assert reloaded.brief.project_name == "long-edit"


def test_ttl_env_override_is_honoured(monkeypatch, tmp_path):
    store, u, pr = _fresh_store(monkeypatch, tmp_path, ttl="5")
    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                           owner_id=u.id, project_id=pr.id)
    fake_now[0] += 10
    store.save_session(AgentSession(id="", mode=SessionMode.MANUAL),
                       owner_id=u.id, project_id=pr.id)
    ids = [x.id for x in store.list_sessions(owner_id=u.id)]
    assert a.id not in ids
