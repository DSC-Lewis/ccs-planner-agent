# TDD Test Specification
> Generated from: `docs/PRD.md`
> Date: 2026-04-17
> Language/Framework: Python 3.11 · pytest + FastAPI TestClient
> Convention: file `tests/test_<topic>.py`, IDs map 1-to-1 to PRD section 6.

## Overview
Drives the CCS Planner Agent refactor from "working but insecure" to
"working and passes the security review". Every suite below either already
has tests that must keep passing, or adds RED → GREEN steps for a specific
security hardening.

---

## Test Suite 1 (TS-1) — Brief input validation (FR-1 / NFR-1.3)
File: `tests/test_agent_manual.py`, `tests/test_input_limits.py`

### TC-1.1: weeks=0 is rejected
- Category: error-handling · Priority: P0 · **exists**
- Input: `advance({project_name:"x", weeks:0})`
- Expected: raises `StepError` or `ValidationError`

### TC-1.2: project_name > 120 chars is rejected
- Category: edge-case · Priority: P0 · **new**
- Input: `advance({project_name: "A" * 200, weeks:4})`
- Expected: HTTP 422 with detail mentioning "project_name"

### TC-1.3: channel_ids > 50 items is rejected
- Category: edge-case · Priority: P0 · **new**
- Input: 60 channel ids
- Expected: HTTP 422 with detail mentioning "channel_ids"

### TC-1.4: weekly budget cell > 1e12 is rejected
- Category: edge-case · Priority: P1 · **new**
- Input: `weekly_budgets.tv_advertising = [1e18, …]`
- Expected: `StepError` with wording "too large"

### TC-1.5: unknown survey_id / client_id / target_id is rejected
- Category: error-handling · Priority: P0 · **exists**

---

## Test Suite 2 (TS-2) — Manual flow end-to-end (FR-2)
File: `tests/test_agent_manual.py` · **all existing, must stay green**

### TC-2.1: happy path → Plan 1 with summary
### TC-2.2: Reach planning skips Comms step
### TC-2.3: unknown channel rejected at channels step

---

## Test Suite 3 (TS-3) — Automatic flow end-to-end (FR-3)
File: `tests/test_agent_automatic.py` · **all existing**

### TC-3.1: constraints respected (min/max budget)
### TC-3.2: budget sweep is monotonic in reach

---

## Test Suite 4 (TS-4) — Fork handoff (FR-4)
File: `tests/test_fork_handoff.py` · **all existing**

### TC-4.1: brief + automatic seed carries over
### TC-4.2: incomplete source is refused
### TC-4.3: same-mode fork is refused
### TC-4.4: provenance recorded in history
### TC-4.5: fork over HTTP returns next-step payload

---

## Test Suite 5 (TS-5) — Plan comparison (FR-5)
File: `tests/test_api_http.py` · **exists (indirect)**
Consider adding a dedicated 2-plan compare test in Phase 3 if time permits.

---

## Test Suite 6 (TS-6) — Survey CSV validator (FR-6)
File: `tests/test_survey_validation.py` · **all existing**

### TC-6.1: CSV parses ≥ 1000 rows
### TC-6.2: every mock channel has a mapping or is explicitly skipped
### TC-6.3: all overrides stay within 0..100 %

---

## Test Suite 7 (TS-7) — Static-file path traversal (NFR-1.1)
File: `tests/test_static_safety.py` · **new**

### TC-7.1: legitimate asset returns 200
- Input: `GET /assets/styles.css`
- Expected: 200, correct content-type

### TC-7.2: root `/` serves index.html
- Input: `GET /`
- Expected: 200, HTML

### TC-7.3: traversal with `../` returns 404 (NOT file content)
- Input: `GET /..%2Fconfig.py`
- Expected: status != 200 (either 404 or 200 with index.html content as
  SPA fallback — must NOT be the contents of `config.py`)

### TC-7.4: absolute path is refused
- Input: `GET //etc/hosts`
- Expected: must NOT return `/etc/hosts` content

### TC-7.5: traversal probe reproducing the live-probe finding
- Input: HTTP path `..%2Fconfig.py`
- Expected: response body does NOT contain `Runtime configuration loaded from env vars`

---

## Test Suite 8 (TS-8) — CORS startup guard (NFR-1.2)
File: `tests/test_cors_guard.py` · **new**

### TC-8.1: wildcard + credentials raises at startup
- Input: env `CCS_CORS_ORIGINS="*"` and `CCS_CORS_CREDENTIALS="true"`
- Expected: application refuses to construct (raises `RuntimeError`)

### TC-8.2: explicit allowlist + credentials is fine
- Input: `CCS_CORS_ORIGINS="https://a,https://b"`, credentials=true
- Expected: app constructs; `/api/health` returns 200

### TC-8.3: wildcard without credentials is fine (legacy behavior)
- Expected: app constructs

---

## Test Suite 9 (TS-9) — Frontend rendering escape (NFR-1.4)
File: `tests/test_frontend_escape.py` · **new (static-analysis-style)**

### TC-9.1: `app.js` contains no `.innerHTML = user.*` patterns where `user`
is a user-controlled variable (echo of project name etc.).

### TC-9.2: bot echo of user input goes through a text-only path
(`textContent` or an explicit HTML escape helper).

---

## Test Suite 10 (TS-10) — Cross-process storage lock (NFR-2)
File: `tests/test_storage_locking.py` · **new**

### TC-10.1: concurrent updates from two processes both land
- Setup: spawn 2 subprocess workers that each append 20 plans to the same
  `storage.json` via the public API.
- Expected: final plan count = 40 (no lost writes).

### TC-10.2: atomic replace leaves no partial file
- Kill a write mid-flight (simulated via patching); re-open file still parses.

---

## Priority summary

| Priority | Count | Focus |
|---|---|---|
| P0 | TS-1, TS-2, TS-7, TS-8, TS-10 | Security blockers + core flow |
| P1 | TS-3, TS-4, TS-5, TS-6, TS-9 | Features + defense-in-depth |

---

## Exit criteria
- `pytest -q` shows ≥ 23 passing tests.
- `curl /..%2Fconfig.py` returns something other than the raw file.
- Starting with `CCS_CORS_ORIGINS=* CCS_CORS_CREDENTIALS=true uvicorn app.main:app` fails at boot.
