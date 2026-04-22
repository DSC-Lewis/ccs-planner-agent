"""Regression: init_schema must boot cleanly against a pre-v6 DB.

In production the ivy VM had a SQLite file from PR B that lacked the
v6 denormalised columns on `sessions` (client_id, first_target_id).
The schema script had a `CREATE INDEX ... ON sessions(client_id, ...)`
statement that ran BEFORE the ALTER TABLE idempotent migration, so
sqlite3 raised ``OperationalError: no such column: client_id`` and the
application crashed on startup.

This test simulates that scenario by hand-crafting a pre-v6 sessions
table, then calling storage.init_schema() and verifying the migration
succeeds + the new columns + index are present.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path


def test_init_schema_migrates_pre_v6_sessions_table(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp(prefix="ccs_migrate_test_"))
    db_path = tmpdir / "prev6.db"

    # --- 1. Build a pre-v6 sessions table by hand (no client_id columns).
    c = sqlite3.connect(str(db_path), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY, name TEXT, api_key_hash TEXT UNIQUE,
            is_admin INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1,
            created_at REAL
        );
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT, owner_id TEXT REFERENCES users(id),
            created_at REAL, archived INTEGER DEFAULT 0
        );
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            owner_id TEXT REFERENCES users(id),
            project_id TEXT REFERENCES projects(id),
            mode TEXT, payload TEXT,
            created_at REAL, updated_at REAL
        );
        CREATE TABLE plans (
            id TEXT PRIMARY KEY, owner_id TEXT REFERENCES users(id),
            brief_id TEXT, kind TEXT, name TEXT, payload TEXT, created_at REAL
        );
    """)
    # FK from sessions.owner_id → users requires a parent row first.
    c.execute(
        "INSERT INTO users(id, name, api_key_hash, is_admin, is_active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("usr_legacy", "legacy", "hashed", 0, 1, 1700000000.0),
    )
    c.execute(
        "INSERT INTO sessions(id, owner_id, project_id, mode, payload, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ses_legacy", "usr_legacy", None, "manual",
         '{"id":"ses_legacy","mode":"manual","brief":{"client_id":"internal_pitch",'
         '"target_ids":["all_adults"],"weeks":4,"overrides":{}}}',
         1700000000.0, 1700000000.0),
    )
    c.close()

    # --- 2. Point the storage module at this DB and re-import it fresh so
    #     the module-level connection cache is warm from scratch.
    monkeypatch.setenv("CCS_DATABASE_PATH", str(db_path))
    # Reload both config + storage so the new env var is picked up.
    import importlib
    from app import config as cfg
    importlib.reload(cfg)
    from app.services import storage
    importlib.reload(storage)

    # --- 3. init_schema() must not raise on the pre-v6 DB.
    storage.init_schema()

    # --- 4. Verify the migration did its job: new columns present +
    #     the legacy row was backfilled from its JSON payload.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "client_id" in cols
    assert "first_target_id" in cols

    row = conn.execute(
        "SELECT client_id, first_target_id FROM sessions WHERE id = 'ses_legacy'"
    ).fetchone()
    assert row["client_id"] == "internal_pitch"
    assert row["first_target_id"] == "all_adults"

    # --- 5. idx_sessions_scope index must exist post-migration.
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sessions_scope'"
    ).fetchone()
    assert idx is not None, "idx_sessions_scope should be created by init_schema"
    conn.close()
