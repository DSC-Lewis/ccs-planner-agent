# TDD v4 Test Specification
> From `PRD_v4.md` · 2026-04-17 · Python 3.11 · pytest + FastAPI TestClient

## Overview
Six test suites (TS-18..TS-23) drive the multi-tenant/persistence rewrite.
Existing 77 tests stay green under the new storage (NFR-5.5).

---

## Test Suite 18 (TS-18) — SQLite storage (FR-20, NFR-5.1, 5.3)
File: `tests/test_sqlite_storage.py`

### TC-18.1: idempotent schema init creates all tables
### TC-18.2: save_session → get_session round-trip via SQLite
### TC-18.3: save_plan → get_plan round-trip
### TC-18.4: list_sessions / list_plans respect owner_id filter
### TC-18.5: TTL sweep still purges stale rows (re-validate NFR-2)
### TC-18.6: migration from storage.json is idempotent
### TC-18.7: parametrised queries — raw SQL injection attempt returns empty

---

## Test Suite 19 (TS-19) — Projects (FR-17, FR-18)
File: `tests/test_projects.py`

### TC-19.1: POST /api/projects creates + returns 201
### TC-19.2: GET /api/projects lists only caller's projects
### TC-19.3: cross-user access returns 403
### TC-19.4: DELETE archives (not hard-deletes)
### TC-19.5: POST /api/sessions requires/defaults project_id
### TC-19.6: project detail includes session + plan counts

---

## Test Suite 20 (TS-20) — Conversation log (FR-19, NFR-5.4)
File: `tests/test_conversations.py`

### TC-20.1: every advance appends one turn
### TC-20.2: turn includes FULL brief snapshot (option C)
### TC-20.3: GET /api/sessions/{id}/conversation returns turns in order
### TC-20.4: cross-user access to another user's conversation → 403
### TC-20.5: snapshots don't leak API keys / constraints
### TC-20.6: failed advance (StepError) does NOT append a turn

---

## Test Suite 21 (TS-21) — Users + scoping (FR-15, NFR-5.2)
File: `tests/test_users.py`

### TC-21.1: admin POST /api/users returns a new key (one-time)
### TC-21.2: GET /api/me returns the calling user
### TC-21.3: non-admin cannot create users (403)
### TC-21.4: looking up a user by key uses constant-time compare
### TC-21.5: plain-text keys are never stored
### TC-21.6: two users cannot see each other's sessions / plans / projects

---

## Test Suite 22 (TS-22) — Admin bootstrap (FR-16)
File: `tests/test_admin_bootstrap.py`

### TC-22.1: CCS_ADMIN_KEY env creates admin user at boot
### TC-22.2: legacy CCS_API_KEY env also creates admin (backcompat)
### TC-22.3: no admin + no env → /api/* returns 401 with actionable detail
### TC-22.4: admin exists + correct key → normal flow works

---

## Test Suite 23 (TS-23) — Frontend shell (FR-21)
File: `tests/test_frontend_shell.py`

Static-analysis style (grep app.js + index.html).

### TC-23.1: login form scaffolding exists
### TC-23.2: renderProjects() function referenced
### TC-23.3: renderProjectDetail() function referenced
### TC-23.4: renderHistory() function referenced
### TC-23.5: localStorage write uses a key that includes "apiKey"
### TC-23.6: every fetch() call adds the X-API-Key header when present

---

## Exit criteria
- ≥ 100 green pytest tests (77 existing + ≥ 23 new).
- Live browser smoke: log in as admin, create project, finish session,
  click History → every turn visible with brief snapshots.
- Migration smoke: run server with old storage.json in place, sessions
  appear in SQLite; rerun migration script → no duplicates.
