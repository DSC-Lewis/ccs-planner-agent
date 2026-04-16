"""TS-10 · Cross-process storage safety (NFR-2).

Spawns two subprocesses that each append N sessions to the same
``storage.json``. Under the current ``threading.Lock()`` they would race on
``_load → mutate → _save`` and lose writes. The file must end up with all
writes after we add an ``fcntl`` advisory lock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


WORKER = textwrap.dedent("""
    import os, sys
    os.environ["CCS_STORAGE_PATH"] = sys.argv[1]
    sys.path.insert(0, sys.argv[2])
    from app.schemas import AgentSession, SessionMode
    from app.services import storage
    count = int(sys.argv[3])
    worker_id = sys.argv[4]
    for i in range(count):
        s = AgentSession(id="", mode=SessionMode.MANUAL)
        s.brief.project_name = f"{worker_id}-{i}"
        storage.save_session(s)
""")


READ_WHILE_WRITE_WORKER = textwrap.dedent("""
    '''Interleaves reads and writes against the same storage.json.'''
    import os, sys, json
    os.environ["CCS_STORAGE_PATH"] = sys.argv[1]
    sys.path.insert(0, sys.argv[2])
    from app.schemas import AgentSession, SessionMode
    from app.services import storage
    role, iters = sys.argv[3], int(sys.argv[4])
    errors = []
    for i in range(iters):
        try:
            if role == "writer":
                s = AgentSession(id="", mode=SessionMode.MANUAL)
                s.brief.project_name = f"w-{i}"
                storage.save_session(s)
            else:
                # any torn-write exposes as JSONDecodeError here
                storage.list_sessions()
        except json.JSONDecodeError as e:
            errors.append(f"{role}-{i}: {e}")
    if errors:
        print("ERRORS:" + "|".join(errors))
        sys.exit(2)
""")


def test_every_read_path_acquires_the_cross_process_lock(monkeypatch, tmp_path):
    '''TS-10.2 · Regression for review #3.

    The original fix only locked writes. Atomic ``tmp.replace`` on the same
    device makes torn-read windows rare in practice, but the contract
    regression is "reads don't coordinate with writes at all". Rather than
    attempt to provoke a race (flaky), assert the lock context manager is
    actually entered on every public read function.
    '''
    import app.services.storage as store

    monkeypatch.setenv("CCS_STORAGE_PATH", str(tmp_path / "storage.json"))
    # Pre-seed a session so reads have something to decode.
    from app.schemas import AgentSession, Plan, PlanKind, SessionMode
    s = store.save_session(AgentSession(id="", mode=SessionMode.MANUAL))
    p = store.save_plan(Plan(brief_id=s.id, name="P", kind=PlanKind.MANUAL))

    acquisitions = {"count": 0}
    original_lock = store._cross_process_lock

    @contextmanager
    def counting_lock():
        acquisitions["count"] += 1
        with original_lock():
            yield

    monkeypatch.setattr(store, "_cross_process_lock", counting_lock)

    before = acquisitions["count"]
    store.get_session(s.id)
    store.list_sessions()
    store.get_plan(p.id)
    store.list_plans()
    after = acquisitions["count"]

    assert after - before == 4, (
        f"Expected every read to acquire the cross-process lock, "
        f"got {after - before} of 4 acquisitions."
    )


def test_concurrent_workers_do_not_drop_writes(tmp_path):
    store = tmp_path / "storage.json"
    worker_script = tmp_path / "worker.py"
    worker_script.write_text(WORKER)

    count_per_worker = 20
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker_script), str(store), str(ROOT),
             str(count_per_worker), wid],
        )
        for wid in ("A", "B")
    ]
    for p in procs:
        p.wait(timeout=30)
        assert p.returncode == 0, f"worker failed: rc={p.returncode}"

    assert store.exists(), "storage file was never created"
    data = json.loads(store.read_text())
    # Each session has a unique id; losing writes would show up as < expected.
    assert len(data["sessions"]) == count_per_worker * 2, (
        f"Expected {count_per_worker*2} sessions, got {len(data['sessions'])} "
        "— writes were dropped by the race between workers."
    )
