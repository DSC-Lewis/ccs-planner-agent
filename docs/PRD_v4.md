# CCS Planner Agent · PRD v4 — Multi-tenant persistence

> Combines the scopes originally outlined as iterations 4, 5, 6.
> Date: 2026-04-17 · Owner: Dentsu PM team.

## 1. Context

After v1..v3 the agent is functional but single-tenant and single-session.
Every page load starts from zero; closing the tab loses the turn-by-turn
history; there's no way for two people to share the same deployment without
seeing each other's briefs. v4 reshapes the data model to match the real
CCS Planner (Home → Project → Brief → Plan) and adds first-class
conversation history so every advance can be replayed.

## 2. Goals

1. **Accounts** — multiple users can share one deployment without leaking
   each other's work. Per-user API keys.
2. **Projects** — a first-class container owned by one user, holding many
   briefs and their plans. Home page mirrors the training video's
   Recent-projects grid.
3. **Conversation log** — every `/advance` call persists a full snapshot
   of the brief (option C), rendered prompt, and user payload — replayable
   with timestamps.
4. **Durable storage** — move from a single JSON file to SQLite so the
   dataset can grow and queries stay fast. Existing deploys migrate
   automatically.

Out of scope (separate follow-ups): SSO/OIDC, Postgres, project sharing
between users, per-project ACLs finer than owner/none.

## 3. Functional requirements

### FR-15 · User accounts
- `User(id, name, api_key_hash, is_admin, created_at)`.
- `POST /api/users` (admin-only) creates a user and returns a one-time
  plain-text key.
- `GET /api/me` returns the calling user (404 if anonymous).
- API-key header `X-API-Key` now looks the value up in the `users` table
  (hashed with `hashlib.sha256` and constant-time compared) instead of the
  old single-value match.

### FR-16 · Admin bootstrap
- On boot, if `CCS_ADMIN_KEY` is set, ensure a user `admin` exists with
  that key (and `is_admin=true`). Backward-compat: existing `CCS_API_KEY`
  env var is migrated to `CCS_ADMIN_KEY` at read time (fall-through).
- If no admin exists **and** no env key is set, the server still starts
  but every `/api/*` except `/api/health` returns 401 with a "no admin
  configured" detail.

### FR-17 · Projects
- `Project(id, name, owner_id, created_at, archived)`.
- `POST /api/projects` `{name}` → 201 with the new project owned by the
  caller.
- `GET /api/projects` → caller's non-archived projects (includes counts:
  `session_count`, `plan_count`).
- `GET /api/projects/{id}` → project detail with nested sessions + plans
  (403 if caller doesn't own it).
- `DELETE /api/projects/{id}` → soft delete (`archived=true`), cascades to
  hide sessions/plans from listings.

### FR-18 · Brief ↔ Project
- `Brief` gains a `project_id` field (nullable only during migration).
- `POST /api/sessions` now accepts `{mode, project_id}`; falls back to the
  caller's "default" project if omitted.
- `GET /api/projects/{id}/sessions` returns sessions in the project.

### FR-19 · Conversation log (option C)
- `ConversationTurn(id, session_id, turn_index, step, payload, prompt,
  brief_snapshot, ts)`.
- Every successful `agent.advance()` call appends one turn with the
  **full** post-advance brief as `brief_snapshot`.
- `GET /api/sessions/{id}/conversation` returns the full turn list
  (owner-scoped).
- No PII beyond what the user typed — same retention rules as sessions.

### FR-20 · SQLite storage
- `storage.py` rewritten against `sqlite3` (stdlib, zero new deps).
- Database path configurable via `CCS_DATABASE_PATH` (default
  `./app/var/ccs.db`).
- Schema migrations are idempotent SQL run at startup
  (`CREATE TABLE IF NOT EXISTS`). Version tracked in a `schema_version`
  table.
- On first boot against an existing `storage.json`, an import script
  moves sessions/plans in — executed by
  `scripts/migrate_json_to_sqlite.py`.

### FR-21 · Frontend
- Login view: enter API key → stored in localStorage (never logged).
- Home page: Projects grid (cards with name, session count, last-updated),
  "New project" CTA.
- Project detail: list of saved plans + sessions + "New session" CTA.
- Inside a session: existing chat flow + a **"History"** side panel
  listing every turn (step name, timestamp, diff of brief_snapshot).

## 4. Non-functional requirements

| Ref | Requirement |
|---|---|
| NFR-5.1 | SQLite access uses `PRAGMA foreign_keys = ON` and **parametrised queries only** (no f-string SQL). |
| NFR-5.2 | All data tables are scoped by `owner_id`; a user can't read or write a row they don't own (enforced at the service layer, not just the route). |
| NFR-5.3 | Migration from `storage.json` MUST be idempotent — running it twice leaves the DB unchanged. |
| NFR-5.4 | Conversation snapshots MUST NOT store raw API keys or other secrets — only the `StepPayload` sanitised fields. |
| NFR-5.5 | Existing 77 tests must stay green; backwards-compatible behaviour when env knobs are untouched. |

## 5. Tracking ID map

| PRD | TDD suite |
|---|---|
| FR-20, NFR-5.1, NFR-5.3 | TS-18 SQLite storage |
| FR-17, FR-18 | TS-19 Projects |
| FR-19, NFR-5.4 | TS-20 Conversation log |
| FR-15, NFR-5.2 | TS-21 Users + scoping |
| FR-16 | TS-22 Admin bootstrap + backward-compat |
| FR-21 | TS-23 Frontend login + home + history |

## 6. Acceptance
- `pytest -q` ≥ 100 green.
- Migration: run server against the existing `storage.json`, DB populated
  with the same session/plan count; run migration script a second time,
  no duplicates.
- Browser: log in with admin key → see empty project grid → create
  project → create session → finish 2 plans → project detail shows them
  → open session → history panel replays every turn.
