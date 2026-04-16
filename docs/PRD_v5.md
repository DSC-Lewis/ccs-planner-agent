# PRD v5 — Admin UI for user management

> Date: 2026-04-17 · Scope: small, additive. Previous iterations unchanged.

## 1. Problem
v4 shipped per-user auth but admins must mint invites via `curl` (`POST /api/users`) and there's no way to disable a compromised key without opening a SQLite shell. Two-person pilot is fine either way, but the moment a third person needs a key or someone leaves the team, we need a UI.

## 2. Goals
1. Admin-only **Users** tab listing every user + creation date.
2. One-click "Invite user" flow — modal captures name, returns the new key exactly once.
3. **Disable** user (revoke their key) without deleting their projects/sessions.
4. No schema migration — reuse the `users` table, add an `is_active` column.

## 3. Functional requirements

### FR-22 · `is_active` flag
- `User.is_active: bool = True`.
- `storage.get_user_by_api_key()` returns `None` for inactive users.
- Schema migration: `ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;` run idempotently at `init_schema` (guarded by a check on `PRAGMA table_info`).

### FR-23 · `GET /api/users` (admin-only)
Returns every user (minus `api_key_hash`) — id, name, is_admin, is_active, created_at.

### FR-24 · `POST /api/users/{id}/disable` (admin-only)
Sets `is_active=0`. Idempotent. Admin cannot disable themselves (422).

### FR-25 · `POST /api/users/{id}/rotate` (admin-only)
Generates a new random key, replaces the hash, returns the plaintext exactly once.

### FR-26 · Frontend Users tab
- Visible only when the current user is admin.
- List table + "Invite user" modal (captures name) + "Disable" / "Rotate" buttons per row.
- One-time-key modal with copy-to-clipboard.

## 4. Non-functional requirements
- NFR-6.1: existing 113 tests must stay green.
- NFR-6.2: `get_user_by_api_key` filters on `is_active=1` so a revoked key is immediately useless — no server restart needed.

## 5. TDD suites
| PRD | TDD |
|---|---|
| FR-22 | TS-24 schema + `is_active` lookup |
| FR-23, FR-24, FR-25 | TS-25 admin user endpoints |
| FR-26 | TS-26 frontend admin tab (static analysis) |
