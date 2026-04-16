"""SQLite-backed storage for users, projects, sessions, plans, conversations.

Design
------
* Single-file SQLite DB at ``CCS_DATABASE_PATH`` — zero new dependencies.
* All queries are parametrised (NFR-5.1). No f-string SQL anywhere.
* Connection-per-call with a tiny thread-local cache; relies on SQLite's
  own WAL-mode for concurrent readers + single writer. Combined with
  ``PRAGMA foreign_keys = ON``.
* Public surface preserves the pre-v4 signature for ``save_session`` /
  ``get_session`` / ``save_plan`` etc. (keyword-only ``owner_id`` added).
* Full legacy ``storage.json`` import via ``import_legacy_json()`` — called
  automatically by ``init_schema`` on first boot when the JSON exists and
  the DB is empty.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import DATABASE_PATH, STORAGE_PATH
from ..schemas import (
    AgentSession,
    ConversationTurn,
    Plan,
    Project,
    User,
)

DEFAULT_SESSION_TTL_SECONDS = 7 * 86400


def _now() -> float:
    """Monkeypatchable wall-clock seam. Tests override."""
    return time.time()


def _ttl() -> int:
    from ..config import SESSION_TTL_SECONDS as current
    return current


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _hash_key(key: str) -> str:
    """Constant-time comparison-friendly hash. SHA-256 is plenty for a
    random 32-byte token; we do NOT need a password-grade KDF here
    (tokens are high-entropy, not user-chosen)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---------- Connection pool ----------

_lock = threading.Lock()
_tls = threading.local()


def _conn() -> sqlite3.Connection:
    c = getattr(_tls, "conn", None)
    if c is None:
        c = sqlite3.connect(str(DATABASE_PATH), isolation_level=None,
                            check_same_thread=False, timeout=5.0)
        c.execute("PRAGMA foreign_keys = ON;")
        # WAL mode persists once set in the DB file. Retry on transient
        # contention during concurrent first-open from multiple processes.
        for _ in range(5):
            try:
                c.execute("PRAGMA journal_mode = WAL;")
                break
            except sqlite3.OperationalError:
                time.sleep(0.05)
        c.row_factory = sqlite3.Row
        _tls.conn = c
    return c


# ---------- Schema ----------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version  INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner_id    TEXT NOT NULL REFERENCES users(id),
    created_at  REAL NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id, archived);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL REFERENCES users(id),
    project_id  TEXT REFERENCES projects(id),
    mode        TEXT NOT NULL,
    payload     TEXT NOT NULL,   -- full AgentSession JSON
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);

CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL REFERENCES users(id),
    brief_id    TEXT NOT NULL,    -- session/brief id
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    payload     TEXT NOT NULL,    -- full Plan JSON
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_owner ON plans(owner_id);
CREATE INDEX IF NOT EXISTS idx_plans_brief ON plans(brief_id);

CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_index   INTEGER NOT NULL,
    step         TEXT NOT NULL,
    payload      TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    brief_snapshot TEXT NOT NULL,
    ts           REAL NOT NULL,
    UNIQUE(session_id, turn_index)
);
CREATE INDEX IF NOT EXISTS idx_convo_session ON conversations(session_id);
"""

_SCHEMA_VERSION = 1


def init_schema() -> None:
    """Idempotent schema setup. Does NOT auto-migrate from JSON —
    callers who want that behaviour should call ``import_legacy_json()``
    themselves (done by ``app.main`` on boot)."""
    with _lock:
        c = _conn()
        c.executescript(_SCHEMA_SQL)
        cur = c.execute("SELECT version FROM schema_version LIMIT 1;")
        row = cur.fetchone()
        if row is None:
            c.execute("INSERT INTO schema_version(version) VALUES (?);", (_SCHEMA_VERSION,))
        # v5 additive migration: ensure `is_active` exists on pre-v5 DBs.
        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "is_active" not in cols:
            c.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;"
            )


def auto_migrate_legacy_if_empty() -> int:
    """Convenience for the startup hook — migrate only when the DB is empty.
    Returns the number of rows imported (0 if skipped)."""
    if not STORAGE_PATH.exists():
        return 0
    if _count("sessions") > 0 or _count("plans") > 0:
        return 0
    return import_legacy_json()


def list_tables() -> List[str]:
    c = _conn()
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    ).fetchall()
    return [r["name"] for r in rows]


def _count(table: str) -> int:
    # table name is NOT user-controlled — restrict to a fixed whitelist.
    assert table in {"users", "projects", "sessions", "plans", "conversations"}
    c = _conn()
    return c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


# ---------- Users ----------

def create_user(name: str, api_key: str, is_admin: bool = False) -> User:
    uid = _new_id("usr")
    now = _now()
    c = _conn()
    c.execute(
        "INSERT INTO users(id, name, api_key_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, name, _hash_key(api_key), 1 if is_admin else 0, now),
    )
    return User(id=uid, name=name, is_admin=is_admin, created_at=now)


def get_user_by_api_key(api_key: str) -> Optional[User]:
    """Look up an active user by their plaintext key. Disabled (is_active=0)
    users are treated as non-existent — a revoked key immediately stops
    authenticating without needing a server restart (NFR-6.2)."""
    if not api_key:
        return None
    c = _conn()
    row = c.execute(
        "SELECT * FROM users WHERE api_key_hash = ? AND is_active = 1 LIMIT 1",
        (_hash_key(api_key),),
    ).fetchone()
    return _user_from_row(row) if row else None


def get_user(user_id: str) -> Optional[User]:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_from_row(row) if row else None


def get_user_by_name(name: str) -> Optional[User]:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    return _user_from_row(row) if row else None


def list_users() -> List[User]:
    rows = _conn().execute(
        "SELECT * FROM users ORDER BY created_at ASC"
    ).fetchall()
    return [_user_from_row(r) for r in rows]


def set_user_active(user_id: str, active: bool) -> bool:
    """Enable or disable a user. Returns True if a row was updated."""
    cur = _conn().execute(
        "UPDATE users SET is_active = ? WHERE id = ?",
        (1 if active else 0, user_id),
    )
    return cur.rowcount > 0


def rotate_user_key(user_id: str, new_key: str) -> bool:
    """Replace a user's key hash. Returns True if the user exists."""
    cur = _conn().execute(
        "UPDATE users SET api_key_hash = ? WHERE id = ?",
        (_hash_key(new_key), user_id),
    )
    return cur.rowcount > 0


def _user_from_row(row: sqlite3.Row) -> User:
    # ``is_active`` may be absent on DBs migrated from pre-v5; the column
    # always exists after ``init_schema`` but the row might still be NULL
    # during a rolling upgrade.
    is_active = True
    try:
        is_active = bool(row["is_active"])
    except (IndexError, KeyError):
        is_active = True
    return User(id=row["id"], name=row["name"],
                is_admin=bool(row["is_admin"]),
                is_active=is_active,
                created_at=row["created_at"])


def ensure_admin(name: str, api_key: str) -> User:
    """Create or update the admin user. Idempotent."""
    existing = get_user_by_name(name)
    if existing:
        _conn().execute(
            "UPDATE users SET api_key_hash = ?, is_admin = 1 WHERE id = ?",
            (_hash_key(api_key), existing.id),
        )
        return User(id=existing.id, name=name, is_admin=True,
                    created_at=existing.created_at)
    return create_user(name=name, api_key=api_key, is_admin=True)


# ---------- Projects ----------

def create_project(owner_id: str, name: str) -> Project:
    pid = _new_id("proj")
    now = _now()
    _conn().execute(
        "INSERT INTO projects(id, name, owner_id, created_at, archived) "
        "VALUES (?, ?, ?, ?, 0)",
        (pid, name, owner_id, now),
    )
    return Project(id=pid, name=name, owner_id=owner_id, created_at=now)


def get_project(project_id: str, *, owner_id: str) -> Optional[Project]:
    row = _conn().execute(
        "SELECT * FROM projects WHERE id = ? AND owner_id = ? AND archived = 0",
        (project_id, owner_id),
    ).fetchone()
    if not row:
        return None
    p = _project_from_row(row)
    p.session_count = _conn().execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE project_id = ?", (p.id,)
    ).fetchone()["n"]
    p.plan_count = _conn().execute(
        "SELECT COUNT(*) AS n FROM plans pl JOIN sessions s ON s.id = pl.brief_id "
        "WHERE s.project_id = ?", (p.id,)
    ).fetchone()["n"]
    return p


def list_projects(owner_id: str) -> List[Project]:
    rows = _conn().execute(
        "SELECT * FROM projects WHERE owner_id = ? AND archived = 0 "
        "ORDER BY created_at DESC",
        (owner_id,),
    ).fetchall()
    out: List[Project] = []
    for r in rows:
        p = _project_from_row(r)
        # Fill counts inline (small enough for demo volume).
        p.session_count = _conn().execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE project_id = ?", (p.id,)
        ).fetchone()["n"]
        p.plan_count = _conn().execute(
            "SELECT COUNT(*) AS n FROM plans pl JOIN sessions s ON s.id = pl.brief_id "
            "WHERE s.project_id = ?", (p.id,)
        ).fetchone()["n"]
        out.append(p)
    return out


def archive_project(project_id: str, *, owner_id: str) -> bool:
    cur = _conn().execute(
        "UPDATE projects SET archived = 1 WHERE id = ? AND owner_id = ?",
        (project_id, owner_id),
    )
    return cur.rowcount > 0


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(id=row["id"], name=row["name"], owner_id=row["owner_id"],
                   created_at=row["created_at"], archived=bool(row["archived"]))


def ensure_default_project(owner_id: str) -> Project:
    existing = _conn().execute(
        "SELECT * FROM projects WHERE owner_id = ? AND name = 'Default' AND archived = 0 "
        "LIMIT 1", (owner_id,),
    ).fetchone()
    if existing:
        return _project_from_row(existing)
    return create_project(owner_id=owner_id, name="Default")


# ---------- Sessions ----------

def save_session(session: AgentSession, *, owner_id: str,
                 project_id: Optional[str] = None) -> AgentSession:
    with _lock:
        c = _conn()
        now = _now()
        if not session.id:
            session.id = _new_id("ses")
        existing = c.execute(
            "SELECT created_at, project_id FROM sessions WHERE id = ?",
            (session.id,),
        ).fetchone()
        if existing:
            created_at = existing["created_at"]
            project_id = project_id or existing["project_id"]
        else:
            created_at = now
            if not project_id:
                project_id = ensure_default_project(owner_id).id
        payload = json.dumps(session.model_dump(mode="json"),
                             ensure_ascii=False)
        c.execute(
            "INSERT INTO sessions(id, owner_id, project_id, mode, payload, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at, project_id=excluded.project_id",
            (session.id, owner_id, project_id, session.mode.value, payload,
             created_at, now),
        )
        _sweep_stale(keep_session_id=session.id)
        return session


def get_session(session_id: str, *, owner_id: Optional[str] = None) -> Optional[AgentSession]:
    sql = "SELECT * FROM sessions WHERE id = ?"
    args: Tuple[Any, ...] = (session_id,)
    if owner_id:
        sql += " AND owner_id = ?"
        args = (session_id, owner_id)
    row = _conn().execute(sql, args).fetchone()
    return _session_from_row(row) if row else None


def list_sessions(*, owner_id: str, project_id: Optional[str] = None) -> List[AgentSession]:
    if project_id:
        rows = _conn().execute(
            "SELECT * FROM sessions WHERE owner_id = ? AND project_id = ? "
            "ORDER BY updated_at DESC",
            (owner_id, project_id),
        ).fetchall()
    else:
        rows = _conn().execute(
            "SELECT * FROM sessions WHERE owner_id = ? ORDER BY updated_at DESC",
            (owner_id,),
        ).fetchall()
    return [_session_from_row(r) for r in rows]


def delete_session(session_id: str, *, owner_id: str) -> bool:
    with _lock:
        cur = _conn().execute(
            "DELETE FROM sessions WHERE id = ? AND owner_id = ?",
            (session_id, owner_id),
        )
        return cur.rowcount > 0


def _session_from_row(row: sqlite3.Row) -> AgentSession:
    payload = json.loads(row["payload"])
    return AgentSession(**payload)


# ---------- Plans ----------

def save_plan(plan: Plan, *, owner_id: str) -> Plan:
    with _lock:
        c = _conn()
        if not plan.id:
            plan.id = _new_id("plan")
        payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
        c.execute(
            "INSERT INTO plans(id, owner_id, brief_id, kind, name, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
            "name=excluded.name, kind=excluded.kind",
            (plan.id, owner_id, plan.brief_id, plan.kind.value, plan.name,
             payload, _now()),
        )
        return plan


def get_plan(plan_id: str, *, owner_id: Optional[str] = None) -> Optional[Plan]:
    sql = "SELECT * FROM plans WHERE id = ?"
    args: Tuple[Any, ...] = (plan_id,)
    if owner_id:
        sql += " AND owner_id = ?"
        args = (plan_id, owner_id)
    row = _conn().execute(sql, args).fetchone()
    return _plan_from_row(row) if row else None


def list_plans(*, owner_id: str, brief_id: Optional[str] = None) -> List[Plan]:
    if brief_id:
        rows = _conn().execute(
            "SELECT * FROM plans WHERE owner_id = ? AND brief_id = ? "
            "ORDER BY created_at ASC",
            (owner_id, brief_id),
        ).fetchall()
    else:
        rows = _conn().execute(
            "SELECT * FROM plans WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
    return [_plan_from_row(r) for r in rows]


def _plan_from_row(row: sqlite3.Row) -> Plan:
    return Plan(**json.loads(row["payload"]))


# ---------- Conversations ----------

_SENSITIVE_PAYLOAD_KEYS = {"api_key", "admin_key", "password"}


def _scrub(payload: Dict) -> Dict:
    """Strip keys that obviously carry secrets. Belt-and-suspenders — the
    agent surface shouldn't accept these anyway, but defense in depth."""
    return {k: v for k, v in payload.items()
            if k.lower() not in _SENSITIVE_PAYLOAD_KEYS}


def log_turn(session_id: str, step: str, payload: Dict, prompt: str,
             brief_snapshot: Dict) -> ConversationTurn:
    with _lock:
        c = _conn()
        idx_row = c.execute(
            "SELECT COALESCE(MAX(turn_index), -1) + 1 AS next "
            "FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        turn_idx = idx_row["next"]
        tid = _new_id("turn")
        ts = _now()
        c.execute(
            "INSERT INTO conversations(id, session_id, turn_index, step, "
            "payload, prompt, brief_snapshot, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, session_id, turn_idx, step,
             json.dumps(_scrub(payload), ensure_ascii=False),
             prompt,
             json.dumps(brief_snapshot, ensure_ascii=False),
             ts),
        )
        return ConversationTurn(
            id=tid, session_id=session_id, turn_index=turn_idx,
            step=step, payload=_scrub(payload), prompt=prompt,
            brief_snapshot=brief_snapshot, ts=ts,
        )


def get_conversation(session_id: str, *, owner_id: str) -> List[ConversationTurn]:
    # Scope: only return turns for a session the caller owns.
    sess = get_session(session_id, owner_id=owner_id)
    if not sess:
        return []
    rows = _conn().execute(
        "SELECT * FROM conversations WHERE session_id = ? ORDER BY turn_index ASC",
        (session_id,),
    ).fetchall()
    return [_turn_from_row(r) for r in rows]


def _turn_from_row(row: sqlite3.Row) -> ConversationTurn:
    return ConversationTurn(
        id=row["id"], session_id=row["session_id"],
        turn_index=row["turn_index"], step=row["step"],
        payload=json.loads(row["payload"]),
        prompt=row["prompt"],
        brief_snapshot=json.loads(row["brief_snapshot"]),
        ts=row["ts"],
    )


# ---------- TTL sweep ----------

def _sweep_stale(*, keep_session_id: Optional[str] = None) -> None:
    """Purge sessions older than TTL; plans + conversations cascade."""
    cutoff = _now() - _ttl()
    c = _conn()
    if keep_session_id:
        c.execute(
            "DELETE FROM plans WHERE brief_id IN "
            "(SELECT id FROM sessions WHERE updated_at < ? AND id != ?)",
            (cutoff, keep_session_id),
        )
        c.execute(
            "DELETE FROM sessions WHERE updated_at < ? AND id != ?",
            (cutoff, keep_session_id),
        )
    else:
        c.execute(
            "DELETE FROM plans WHERE brief_id IN "
            "(SELECT id FROM sessions WHERE updated_at < ?)",
            (cutoff,),
        )
        c.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))


# ---------- Reset (tests) ----------

def reset() -> None:
    """Testing only — drop & recreate. Caller must reinit."""
    with _lock:
        c = _conn()
        for t in ["conversations", "plans", "sessions", "projects", "users",
                  "schema_version"]:
            c.execute(f"DROP TABLE IF EXISTS {t}")
    init_schema()


# ---------- Legacy JSON migration ----------

def import_legacy_json() -> int:
    """One-shot: move sessions & plans out of the v1..v3 storage.json into
    SQLite. Idempotent — skips rows whose id already exists. Returns the
    number of *new* sessions imported."""
    if not STORAGE_PATH.exists():
        return 0
    data = json.loads(STORAGE_PATH.read_text(encoding="utf-8"))

    # Every orphaned row lands under a "legacy" user + its default project.
    # The key is random so no one logs in as this user; downgrade to
    # non-admin so the semantics are honest (code-review #2).
    legacy = get_user_by_name("legacy") or create_user(
        name="legacy", api_key=secrets.token_hex(16), is_admin=False,
    )
    proj = ensure_default_project(legacy.id)

    imported = 0
    with _lock:
        c = _conn()
        for sid, raw in (data.get("sessions") or {}).items():
            exists = c.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
            if exists:
                continue
            raw_sess = {k: v for k, v in raw.items() if not k.startswith("_")}
            session = AgentSession(**raw_sess)
            session.id = sid
            ts = raw.get("_ts") or _now()
            c.execute(
                "INSERT INTO sessions(id, owner_id, project_id, mode, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, legacy.id, proj.id, session.mode.value,
                 json.dumps(session.model_dump(mode="json"), ensure_ascii=False),
                 ts, ts),
            )
            imported += 1

        for pid, raw in (data.get("plans") or {}).items():
            exists = c.execute(
                "SELECT 1 FROM plans WHERE id = ?", (pid,)
            ).fetchone()
            if exists:
                continue
            raw_plan = {k: v for k, v in raw.items() if not k.startswith("_")}
            plan = Plan(**raw_plan)
            plan.id = pid
            c.execute(
                "INSERT INTO plans(id, owner_id, brief_id, kind, name, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, legacy.id, plan.brief_id, plan.kind.value, plan.name,
                 json.dumps(plan.model_dump(mode="json"), ensure_ascii=False),
                 raw.get("_ts") or _now()),
            )
    return imported
