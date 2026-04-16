# CCS Planner Agent · PRD v2 — Deployment readiness

> Supersedes nothing in [PRD.md](PRD.md); adds the three follow-ups the
> security review deferred ("Deployment posture (not yet addressed)" §).
> Date: 2026-04-17 · Owner: Dentsu PM team.

## 1. Context

PRD v1 closed the Brief / Plan / Fork / Security-hardening story. What remains
is the **deployment posture**: the agent currently assumes an internal network
(VPN / office LAN). To run it on a shared host — even inside Dentsu — we need:

1. A way to gate access without shipping a full SSO integration.
2. A way to stop a bad actor (or a broken client) from saturating the app.
3. A way to keep storage from growing forever.

PRD v2 addresses those three and nothing more.

## 2. Goals (and non-goals)

**Goals**
- Opt-in API-key authentication, enabled by env var, that protects every
  `/api/*` route except `/api/health`.
- Per-IP rate limiting on the write-heavy routes (`POST /api/sessions`,
  `POST /api/sessions/{id}/advance`, `POST /api/sessions/{id}/fork`).
- Automatic purge of sessions older than a configurable TTL, running
  lazily (no background thread — the next mutating call sweeps).

**Non-goals**
- SSO / SAML / OAuth — separate project.
- Per-user quotas (beyond a shared per-IP bucket).
- Postgres migration — still deferred until horizontal scaling is on the
  roadmap.
- Admin UI for key rotation — keys are env-managed, rotated by redeploy.

## 3. Functional requirements

### FR-7 · Optional API-key authentication
- When `CCS_API_KEY` is **set** (non-empty), every `/api/*` route except
  `/api/health` MUST require an `X-API-Key` header whose value equals
  `CCS_API_KEY`. Missing or wrong key → **HTTP 401**.
- When `CCS_API_KEY` is **unset or empty**, the auth check is a no-op (current
  behaviour preserved, so existing demos keep working).
- Static frontend (`/`, `/assets/*`) is NOT gated — it needs to load before
  the user can type their key. The page reads the key from a localStorage
  input box and sends it on every XHR.

### FR-8 · Per-IP rate limiting
- Applies to the three write endpoints listed above.
- Default: **30 requests per minute per IP**, configurable via
  `CCS_RATE_LIMIT` (format "N/SECONDS", e.g. `"60/60"` or `"5/10"`).
- Exceeding the limit → **HTTP 429 Too Many Requests** with header
  `Retry-After: <seconds>`.
- Rate state is in-process (no Redis) — acceptable because the app is
  single-worker in the current deploy.

### FR-9 · Session retention (TTL)
- Sessions with `created_at` older than `CCS_SESSION_TTL_SECONDS` (default
  `604800` = 7 days) MUST be removed on the next mutating call to
  `storage.save_session` / `save_plan`.
- Plans whose `brief_id` no longer maps to any session MUST also be removed.
- The sweep is lazy (amortised O(n) per write) — simpler and deterministic
  for tests than a background thread.
- The cut-off timestamp is computed from `time.time()` so tests can inject
  via `monkeypatch`.

## 4. Non-functional requirements

| Ref | Requirement |
|---|---|
| NFR-3.1 | All three features MUST be disabled by default (empty key, default rate, 7-day TTL) so existing demos don't break on upgrade. |
| NFR-3.2 | The auth middleware MUST use constant-time comparison (`secrets.compare_digest`) to avoid timing-oracle key leaks. |
| NFR-3.3 | Rate-limit state MUST be wiped between pytest runs (fixture). |
| NFR-3.4 | TTL sweep MUST NOT delete sessions created during an in-flight `advance()` call on the same session (re-check `created_at` after re-reading state). |

## 5. Tracking ID map

| PRD ref | TDD suite | Description |
|---|---|---|
| FR-7, NFR-3.2 | TS-11 | API-key auth |
| FR-8          | TS-12 | Per-IP rate limit |
| FR-9, NFR-3.4 | TS-13 | Session TTL sweep |

## 6. Acceptance

- `pytest -q` ≥ 45 tests passing.
- Starting the server without `CCS_API_KEY` → all existing smoke tests still
  pass unchanged (backward compatibility).
- Starting with `CCS_API_KEY=secret` → `curl /api/sessions` without the header
  returns 401; with `X-API-Key: secret` returns 200.
- Hammering `POST /api/sessions` 100× in 1 s → at least one response is 429.
- Creating a session then advancing `time.time()` by > 7 days and calling
  another `save_session` → the first session no longer appears in
  `list_sessions()`.
