# TDD v2 Test Specification
> Derived from `docs/PRD_v2.md`
> Date: 2026-04-17 · Python 3.11 · pytest + FastAPI TestClient

## Overview
Three new test suites (TS-11, TS-12, TS-13) cover auth, rate-limit, and
retention. Existing 38 tests must remain green. Every test uses a fixture
that cleans env vars + rate-limit state between runs.

---

## Test Suite 11 (TS-11) — API-key authentication (FR-7 / NFR-3.2)
File: `tests/test_auth_guard.py`

### TC-11.1: default behaviour — no key configured, endpoints open
- Preconditions: `CCS_API_KEY` unset
- Input: `GET /api/reference/surveys`
- Expected: 200

### TC-11.2: key configured, request without header rejected
- Preconditions: `CCS_API_KEY=secret`
- Input: `GET /api/reference/surveys`
- Expected: 401 with detail "API key required"

### TC-11.3: key configured, wrong value rejected
- Input: `GET /api/reference/surveys` with `X-API-Key: wrong`
- Expected: 401

### TC-11.4: key configured, correct header accepted
- Input: `GET /api/reference/surveys` with `X-API-Key: secret`
- Expected: 200

### TC-11.5: `/api/health` is always open (probe-friendly)
- Preconditions: `CCS_API_KEY=secret`
- Input: `GET /api/health` without header
- Expected: 200

### TC-11.6: static frontend (`/`) not gated by API key
- Preconditions: `CCS_API_KEY=secret`
- Input: `GET /`
- Expected: 200, HTML

### TC-11.7: comparison is constant-time
- Static-analysis-style: the auth code uses `secrets.compare_digest`.
- Asserted by grepping `app/services/auth.py`.

---

## Test Suite 12 (TS-12) — Per-IP rate limit (FR-8)
File: `tests/test_rate_limit.py`

### TC-12.1: within limit, all requests succeed
- Preconditions: `CCS_RATE_LIMIT=5/10` (5 req per 10 s)
- Input: 5 × `POST /api/sessions`
- Expected: all 200

### TC-12.2: exceeding limit returns 429 with Retry-After
- Input: 6th request in the same 10-second window
- Expected: 429, response header `Retry-After` exists and is an int > 0

### TC-12.3: GET endpoints are NOT rate-limited (read path)
- Input: 50 × `GET /api/reference/surveys` in a tight loop
- Expected: all 200

### TC-12.4: rate-limit state isolated between tests
- Fixture: auto-reset state so tests don't leak into each other.

---

## Test Suite 13 (TS-13) — Session TTL retention (FR-9 / NFR-3.4)
File: `tests/test_retention.py`

### TC-13.1: fresh session never purged
- Input: save session, immediately list
- Expected: session present

### TC-13.2: session older than TTL purged on next write
- Input: save session A, advance clock to now+8d, save session B
- Expected: list returns only B

### TC-13.3: TTL default is 7 days
- Assertion on `storage.DEFAULT_SESSION_TTL_SECONDS == 7*86400`

### TC-13.4: TTL configurable via env
- Input: `CCS_SESSION_TTL_SECONDS=60`, save A, advance clock 61 s, save B
- Expected: A purged

### TC-13.5: orphaned plans purged with their sessions
- Input: create session A + plan linked to A, advance clock, save session B
- Expected: the plan is gone too

### TC-13.6: purge does not race the current caller
- The session being saved MUST NOT be purged even if its timestamp is stale
  (e.g. during a re-save from an edit). Test: save A, artificially set
  A.created_at to the past, save A again → A still present.

---

## Exit criteria
- 3 new test files, ≥ 14 new tests, all GREEN.
- `pytest` total ≥ 52 green (was 38).
- Backwards compatibility smoke: existing 38 tests untouched.
