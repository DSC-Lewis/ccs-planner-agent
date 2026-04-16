# CCS Planner · Conversational Briefing Agent · PRD

> Derived from: training-video transcript (00:00 – 24:49), the fork/handoff
> shipped in Iteration 2, and the security review from Iteration 3.
> Owner: Dentsu PM team · Date: 2026-04-17

## 1. Problem

PMs and Planners currently edit a CCS Planner **Brief** by clicking through 7+
screens of the web UI (Survey → Client → Project → Dates → TA → Planning
type → Comms sliders → Channels → Manual/Auto plan). The flow is correct but
mechanical: the same information is entered twice when a brief gets both a
Manual and an Automatic plan, data-entry errors are easy, and there is no
audit trail for what changed during a brief revision.

## 2. Goal

Replace the Brief-modification UI with a **conversational Agent** that:

1. Walks the user through the same data model as CCS Planner.
2. Exposes two sibling agents — **Manual** (00:00 – 18:09) and **Automatic**
   (18:10 – end) — whose plans coexist against the same Brief.
3. Lets one agent **hand off** its Brief to the other without re-asking shared
   questions.
4. Runs as a single-container FastAPI + static frontend that a PM team can
   host on their laptop, a shared server, or a managed container service.

Out of scope: real-time collaboration, notifications, import from PPT, direct
write-back into the live CCS Planner service.

## 3. Functional requirements

### FR-1 · Brief data model
The Agent captures a Brief with these fields, each backed by mock data from
the training video:
- `survey_id` (required) — enum from `surveys.json`
- `client_id` (required)
- `project_name` (required, **≤ 120 chars**)
- `start_date`, `weeks` (1..52), `end_date` (auto-computed)
- `target_ids` (≥ 1, from `targets.json`)
- `planning_type` ∈ {Reach, Comm}
- `comms` (only when Comm) — 5 sliders ∈ 0..10 + `kpi_ids` (≥ 1)
- `channel_ids` — from `channels.json`, **≤ 50 distinct channels**

### FR-2 · Manual flow
After channels: `calibration` preview → `manual_plan` editor where each
(channel × week) cell accepts a budget ∈ [0, 1 × 10¹²] TWD, rolls up into a
Performance Summary (budget, impressions, GRP, net reach, frequency, brand
effect).

### FR-3 · Automatic flow
After channels: `criterion` (Net Reach / Attentive Reach / …) + `strategy` →
`budget_channels` (total budget + Mandatory/Optional split) → `min_max`
(per-channel constraints) → `optimize` runs a `(penetration × attention) ÷
CPM` allocation respecting constraints, and persists a Plan 2.

### FR-4 · Agent handoff (fork)
From a session that has at least chosen `channel_ids`, the user can fork into
the opposite mode. The new session:
- inherits the entire Brief (deep copy)
- starts at the first mode-specific step (`criterion` for Auto, `calibration`
  for Manual)
- seeds `automatic_input.mandatory_channel_ids` from the source brief
- records provenance (`source_session_id`, `source_mode`, `source_plan_id`)
  in `history`

### FR-5 · Plan comparison
`POST /api/plans/compare` returns per-metric deltas for 2+ plan ids (budget,
impressions, net reach, frequency).

### FR-6 · Mock data validation
A CLI validator (`scripts/validate_with_survey.py`) compares
`channel_metrics.json.penetration_pct` against values derived from a CCS
survey CSV export via a curated mapping file. `--write` rebases the mocks
to the CSV. Tolerance is configurable (default 15 pp).

## 4. Non-functional requirements

### NFR-1 · Security

| Req | Description |
|---|---|
| NFR-1.1 | The static-file fallback MUST reject any request whose resolved path escapes `STATIC_DIR`. Requests like `/..%2Fconfig.py` return **404**, not the file. |
| NFR-1.2 | CORS MUST refuse to start with `allow_credentials=True` AND `allow_origins=["*"]`. Misconfiguration fails fast at boot. |
| NFR-1.3 | Inputs MUST enforce length caps: `project_name ≤ 120`, `channel_ids ≤ 50`, per-week budget ≤ 1 × 10¹². Violations return **422** with a user-friendly detail. |
| NFR-1.4 | Bot messages in the frontend MUST NOT render user-provided strings as HTML — use textContent or escape. |

### NFR-2 · Concurrency
`storage.py` must be safe under `uvicorn --workers N`. A cross-process
advisory lock (`fcntl.flock`) wraps `_load`+mutate+`_save` so concurrent
`advance()` calls on the same session do not drop updates.

### NFR-3 · Deployability
A single `docker compose up --build` exposes the app on `:8000` with a
mounted volume for storage and an HTTP healthcheck that passes within 30 s.

### NFR-4 · Reproducibility
`requirements.txt` pins direct deps. `pytest` in a fresh venv passes on
Python 3.11. Running `scripts/validate_with_survey.py` against the bundled
sample CSV returns 28/28 PASS.

## 5. Success criteria

- `pytest` green (≥ 18 tests) on Python 3.11.
- The live `curl` probes for path traversal and CORS wildcard+credentials
  return a **safe** response (404 and a single explicit origin, respectively).
- Manual → fork → Automatic handoff produces two distinct plans against one
  Brief, with compare deltas available via API.
- README gets a **Security** section documenting deployment posture (no auth
  by default; meant for internal network) and how to enable CORS allowlist.

## 6. Tracking ID map (for TDD.md)

| PRD ref | TDD suite |
|---|---|
| FR-1, NFR-1.3 | TS-1 Brief input validation |
| FR-2          | TS-2 Manual flow |
| FR-3          | TS-3 Automatic flow |
| FR-4          | TS-4 Fork handoff |
| FR-5          | TS-5 Plan comparison |
| FR-6          | TS-6 Survey CSV validator |
| NFR-1.1       | TS-7 Static-file safety |
| NFR-1.2       | TS-8 CORS guard |
| NFR-1.4       | TS-9 Frontend escape |
| NFR-2         | TS-10 Cross-process storage lock |
