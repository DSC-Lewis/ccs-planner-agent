"""TS-13 · Session TTL retention (FR-9 / NFR-3.4)."""
from __future__ import annotations

import importlib
import os
from datetime import date, datetime, timedelta

import pytest

from app.schemas import AgentSession, Plan, PlanKind, SessionMode


def _adv_clock(store, seconds: float):
    """Teleport the stored ``created_at`` of every session backwards in time
    by the given number of seconds — simulates the passage of time without
    needing to freeze ``time.time`` globally."""
    state = store._load()
    # sessions carry a session.history entry with a ``step`` timestamp; but
    # the primary cut-off we want is the session's ``created_at`` marker.
    # Our sessions don't store created_at yet, so the purge relies on the
    # history's implicit timestamp. For testing we instead monkeypatch the
    # ``_now()`` helper below.
    # Left intentionally as a hook — tests call _now() directly.


def test_default_ttl_is_seven_days():
    from app.services import storage
    assert storage.DEFAULT_SESSION_TTL_SECONDS == 7 * 86400


def test_fresh_session_is_not_purged(monkeypatch, tmp_path):
    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("CCS_SESSION_TTL_SECONDS", "60")
    import app.config, app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.reset()

    s = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))
    assert any(x.id == s.id for x in store.list_sessions())


def test_session_older_than_ttl_is_purged_on_next_write(monkeypatch, tmp_path):
    """TC-13.2 — after TTL elapses, the next write sweeps stale sessions."""
    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("CCS_SESSION_TTL_SECONDS", "60")
    import app.config, app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.reset()

    # Session A created at t=0
    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))

    # Advance well beyond the 60s TTL
    fake_now[0] += 120
    b = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))

    ids = [x.id for x in store.list_sessions()]
    assert b.id in ids
    assert a.id not in ids, (
        f"Stale session {a.id} should have been purged; found {ids}"
    )


def test_orphaned_plans_are_purged_with_their_sessions(monkeypatch, tmp_path):
    """TC-13.5 — plans whose brief_id no longer maps to a session go too."""
    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("CCS_SESSION_TTL_SECONDS", "60")
    import app.config, app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.reset()

    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))
    plan = store.save_plan(Plan(brief_id=a.id, name="Plan 1", kind=PlanKind.MANUAL))
    assert store.get_plan(plan.id) is not None

    fake_now[0] += 120
    store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))

    assert store.get_plan(plan.id) is None, (
        "Plan linked to a purged session should also be gone"
    )


def test_resaving_the_same_session_does_not_purge_it(monkeypatch, tmp_path):
    """TC-13.6 — the caller's own session must survive a purge sweep,
    even if its timestamp has aged past the TTL while we were editing."""
    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("CCS_SESSION_TTL_SECONDS", "60")
    import app.config, app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.reset()

    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))

    # A long edit session — the user left the tab open for 2 minutes
    fake_now[0] += 120
    # Resave the SAME session; it should survive.
    a.brief.project_name = "long-edit"
    store.save_session(a)

    ids = [x.id for x in store.list_sessions()]
    assert a.id in ids, "Re-saving a stale session must not purge the caller"
    reloaded = store.get_session(a.id)
    assert reloaded.brief.project_name == "long-edit"


def test_ttl_env_override_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setenv("CCS_SESSION_TTL_SECONDS", "5")
    import app.config, app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)
    store.reset()

    fake_now = [1000.0]
    monkeypatch.setattr(store, "_now", lambda: fake_now[0])
    a = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))
    fake_now[0] += 10  # > 5 s TTL
    store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))
    ids = [x.id for x in store.list_sessions()]
    assert a.id not in ids
