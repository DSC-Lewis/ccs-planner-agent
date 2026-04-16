"""TS-10 · Cross-process storage safety (NFR-2).

Post-v4: SQLite in WAL mode handles per-process locking for us. The old
fcntl-on-JSON race is gone. We keep a subprocess-based test that exercises
two writers against the same DB file, proving SQLite's own locking keeps
writes atomic.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


WORKER = textwrap.dedent("""
    import os, sys
    os.environ["CCS_DATABASE_PATH"] = sys.argv[1]
    os.environ["CCS_STORAGE_PATH"] = sys.argv[1] + ".json"
    sys.path.insert(0, sys.argv[2])
    from app.schemas import AgentSession, SessionMode
    from app.services import storage
    storage.init_schema()
    # Share a single admin/project so writes all go to the same owner.
    u = storage.get_user_by_name("shared") or storage.ensure_admin("shared", "k")
    pr = storage.ensure_default_project(u.id)
    count = int(sys.argv[3])
    worker_id = sys.argv[4]
    for i in range(count):
        s = AgentSession(id="", mode=SessionMode.MANUAL)
        s.brief.project_name = f"{worker_id}-{i}"
        storage.save_session(s, owner_id=u.id, project_id=pr.id)
""")


def test_concurrent_sqlite_writers_do_not_drop_writes(tmp_path):
    db = tmp_path / "ccs.db"
    worker_script = tmp_path / "worker.py"
    worker_script.write_text(WORKER)

    count_per_worker = 15
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(db), str(ROOT),
             str(count_per_worker), wid],
        )
        for wid in ("A", "B")
    ]
    for p in procs:
        p.wait(timeout=30)
        assert p.returncode == 0, f"worker failed rc={p.returncode}"

    import importlib, os
    os.environ["CCS_DATABASE_PATH"] = str(db)
    os.environ["CCS_STORAGE_PATH"] = str(db) + ".json"
    import app.config, app.services.storage as store
    importlib.reload(app.config)
    importlib.reload(store)

    u = store.get_user_by_name("shared")
    sessions = store.list_sessions(owner_id=u.id)
    # Each worker wrote 15; the TTL sweep shouldn't kick in with the default
    # 7-day TTL, so we expect exactly 30 distinct sessions.
    assert len(sessions) == count_per_worker * 2, (
        f"Expected {count_per_worker * 2} sessions, got {len(sessions)} — "
        "SQLite writer serialization should prevent drops."
    )
