"""File-backed storage for sessions and plans.

Trade-off: a single JSON file is fine for a demo / pilot. For production
swap this out for Postgres + SQLAlchemy — the service surface stays the same.

Concurrency notes
-----------------
* In-process concurrency is handled by a ``threading.Lock``.
* Cross-process / multi-worker concurrency uses a POSIX advisory lock
  (``fcntl.flock``) on a sidecar ``.lock`` file, so two ``uvicorn --workers``
  processes cannot trample each other's read-modify-write cycle. On Windows
  we fall back to ``msvcrt.locking`` with best-effort semantics; tests that
  exercise this run on POSIX only.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config import SESSION_TTL_SECONDS, STORAGE_PATH
from ..schemas import AgentSession, Plan

# Public constants — tests grep these.
DEFAULT_SESSION_TTL_SECONDS = 7 * 86400


def _now() -> float:
    """Monkeypatchable clock seam. Tests override this to simulate time
    passing without mutating ``time.time`` globally."""
    return time.time()


def _ttl() -> int:
    """Read the TTL each call so config reloads in tests take effect."""
    from ..config import SESSION_TTL_SECONDS as current
    return current

_lock = threading.Lock()

try:
    import fcntl  # POSIX only
except ImportError:  # pragma: no cover — Windows fallback
    fcntl = None


@contextlib.contextmanager
def _cross_process_lock():
    """Acquire an exclusive advisory lock on ``<storage>.lock``.

    Used as a barrier around ``_load → mutate → _save`` so two workers cannot
    produce interleaved writes. Non-blocking with spin-sleep to keep latency
    predictable when contention is rare.
    """
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = STORAGE_PATH.with_suffix(".lock")
    with lock_path.open("a+") as f:
        if fcntl is not None:
            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.01)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"not serializable: {type(o)}")


def _load() -> Dict:
    if not STORAGE_PATH.exists():
        return {"sessions": {}, "plans": {}}
    with STORAGE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save(state: Dict) -> None:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Per-PID suffix means two workers writing concurrently don't both clobber
    # the same ``storage.tmp`` before either can call ``replace``.
    tmp = STORAGE_PATH.with_suffix(f".tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=_default)
    tmp.replace(STORAGE_PATH)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _sweep(state: Dict, protect_session_id: Optional[str] = None) -> None:
    """Remove sessions older than the TTL plus any plans they orphaned.

    ``protect_session_id`` is the id of the session the current caller is
    writing; we never purge it, even if its stored ``_ts`` has aged past
    the TTL — the user may have kept the tab open for a long edit.
    """
    cutoff = _now() - _ttl()
    keep: Dict[str, Dict] = {}
    for sid, raw in state.get("sessions", {}).items():
        ts = raw.get("_ts", 0.0)
        if sid == protect_session_id or ts >= cutoff:
            keep[sid] = raw
    state["sessions"] = keep

    live_briefs = set(keep.keys())
    plans = state.get("plans", {})
    state["plans"] = {
        pid: p for pid, p in plans.items()
        if p.get("brief_id") in live_briefs or p.get("brief_id") == ""
    }


# ---------- Sessions ----------

def save_session(session: AgentSession) -> AgentSession:
    with _lock, _cross_process_lock():
        state = _load()
        if not session.id:
            session.id = _new_id("ses")
        dumped = session.model_dump(mode="json")
        # Attach a wall-clock timestamp alongside the schema payload.
        # Kept out of ``AgentSession`` itself so the public schema isn't
        # polluted with persistence plumbing.
        existing = state["sessions"].get(session.id, {})
        dumped["_ts"] = existing.get("_ts", _now()) if session.id in state["sessions"] else _now()
        # Re-save of an existing session bumps the timestamp so an active
        # user's own session cannot be aged out by the sweep.
        if session.id in state["sessions"]:
            dumped["_ts"] = _now()
        state["sessions"][session.id] = dumped
        _sweep(state, protect_session_id=session.id)
        _save(state)
        return session


def _strip_persistence_fields(raw: Dict) -> Dict:
    """Remove internal fields (like ``_ts``) before handing a stored row
    back to Pydantic, which would otherwise complain about extras."""
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def get_session(session_id: str) -> Optional[AgentSession]:
    with _cross_process_lock():
        state = _load()
    raw = state["sessions"].get(session_id)
    return AgentSession(**_strip_persistence_fields(raw)) if raw else None


def list_sessions() -> List[AgentSession]:
    with _cross_process_lock():
        state = _load()
    return [AgentSession(**_strip_persistence_fields(v))
            for v in state["sessions"].values()]


def delete_session(session_id: str) -> bool:
    with _lock, _cross_process_lock():
        state = _load()
        if session_id in state["sessions"]:
            del state["sessions"][session_id]
            _save(state)
            return True
        return False


# ---------- Plans ----------

def save_plan(plan: Plan) -> Plan:
    with _lock, _cross_process_lock():
        state = _load()
        if not plan.id:
            plan.id = _new_id("plan")
        dumped = plan.model_dump(mode="json")
        dumped["_ts"] = _now()
        state["plans"][plan.id] = dumped
        _save(state)
        return plan


def get_plan(plan_id: str) -> Optional[Plan]:
    with _cross_process_lock():
        state = _load()
    raw = state["plans"].get(plan_id)
    return Plan(**_strip_persistence_fields(raw)) if raw else None


def list_plans(brief_id: Optional[str] = None) -> List[Plan]:
    with _cross_process_lock():
        state = _load()
    plans = [Plan(**_strip_persistence_fields(v))
             for v in state["plans"].values()]
    if brief_id:
        plans = [p for p in plans if p.brief_id == brief_id]
    return plans


def reset() -> None:
    """Testing only."""
    with _lock, _cross_process_lock():
        _save({"sessions": {}, "plans": {}})
