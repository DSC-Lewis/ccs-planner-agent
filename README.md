# CCS Planner · Conversational Briefing Agent

An object-lesson reproduction of the Dentsu CCS Planner Brief-modification flow,
re-imagined as two conversational agents:

| Mode | Mirrors video segment | What it does |
|------|-----------------------|--------------|
| **Manual Agent** | 00:00 – 18:09 | Walks the user through the Brief, lets them hand-edit weekly budgets for every channel, and computes a Performance Summary (reach, frequency, GRP, brand effect). |
| **Automatic Agent** | 18:10 – end | Same Brief intake, then takes total budget + Mandatory/Optional channels + Min/Max constraints and runs a transparent `(penetration × attention) / CPM` optimizer. |

Both modes share the same Brief data model so a user can generate **Plan 1 (Manual)** and **Plan 2 (Automatic)** for the same brief and compare them.

## Project layout

```
ccs-planner-agent/
├── app/
│   ├── main.py               # FastAPI entry point (API + static)
│   ├── schemas.py            # Brief / Plan / Session pydantic models
│   ├── config.py             # env-driven settings
│   ├── services/
│   │   ├── agent.py          # state machine for both flows
│   │   ├── optimizer.py      # manual roll-up + automatic allocation + budget sweep
│   │   ├── storage.py        # file-backed session & plan store
│   │   ├── reference.py      # loads mock data (cached)
│   │   └── survey_loader.py  # parses the CCS CSV export
│   ├── data/
│   │   ├── surveys.json / clients.json / targets.json
│   │   ├── brand_kpis.json / channels.json / channel_metrics.json
│   │   ├── optimization_options.json
│   │   └── samples/
│   │       ├── ccs_taiwan_2025_export.csv    # validation data (your file)
│   │       └── channel_survey_mapping.json   # survey code → channel_id
│   └── static/               # vanilla JS frontend
├── scripts/
│   └── validate_with_survey.py
├── tests/                    # pytest (agent, optimizer, API, survey)
├── Dockerfile · docker-compose.yml · run.sh · requirements.txt
└── README.md
```

## Quick start

### 1. Local dev (macOS / Linux)

```bash
cd ccs-planner-agent
./run.sh                     # creates .venv, installs deps, starts uvicorn
open http://localhost:8000
```

Hot-reload is enabled, so editing `app/services/*.py` or `app/static/*` triggers
a rebuild.

### 2. Docker (recommended for pilot deploys)

```bash
docker compose up --build -d
docker compose logs -f ccs-agent
```

The compose file mounts a `ccs_data` volume at `/data` so sessions & plans
survive restarts. Override the port with `CCS_PORT=9000 docker compose up`.

### 3. Tests

```bash
./run.sh  # run once so .venv exists
source .venv/bin/activate
pytest -q                       # all tests
pytest tests/test_agent_manual.py -q
```

### 4. CSV validation (uses your uploaded CCS export)

```bash
python -m scripts.validate_with_survey                # compare only
python -m scripts.validate_with_survey --write        # also rewrite metrics
python -m scripts.validate_with_survey --tolerance 10 # stricter bar
```

The script reads `app/data/samples/ccs_taiwan_2025_export.csv`, applies the
`channel_survey_mapping.json`, and prints a PASS/FAIL table for every mapped
channel.

## API surface

| Verb + path | Purpose |
|-------------|---------|
| `GET  /api/health` | Liveness probe |
| `GET  /api/reference/{surveys,clients,targets,brand-kpis,channels,optimization}` | Drop-down data |
| `POST /api/sessions`                       | Start a new session `{mode: "manual" \| "automatic"}` |
| `GET  /api/sessions/{id}`                  | Resume |
| `POST /api/sessions/{id}/advance`          | Submit current step's payload → returns next prompt/options |
| `DELETE /api/sessions/{id}`                | Reset |
| `GET  /api/plans?brief_id=...`             | List saved plans |
| `GET  /api/plans/{id}`                     | Plan detail |
| `POST /api/plans/compare`                  | Compare 2+ plans (body: `["plan_1","plan_2"]`) |

The interactive docs live at `/docs` (Swagger) and `/redoc`.

## State machine

```
survey_client → project_dates → target_audience → planning_type
   ↓                                                     ↓
   ├── planning_type == "Reach" → skip comms_setup ─────┤
   ↓                                                     ↓
                   comms_setup (Comm only)
                           ↓
                        channels
              ┌────────────┴────────────┐
       Manual ┤                        ├ Automatic
   calibration │                      │ criterion
   manual_plan │                      │ budget_channels
        review ┘                      │ min_max
                                      │ optimize
                                      └ review
```

Commands in the chat composer:

| Command | Effect |
|---------|--------|
| `/skip` | Skip the current step (calls `action: "skip"`) |
| `/back` | Go back one step |
| `/show` | Dump the current brief JSON in chat |

## Deployment notes

- **Single container**: the Dockerfile bakes both API and static frontend.
  Point a reverse proxy (Cloud Run, Fly.io, Azure Container Apps, EC2 + Caddy)
  at port 8000.
- **State**: sessions & plans live in `CCS_STORAGE_PATH`. For production swap
  the `storage.py` module for Postgres — the service signature (`save_session`,
  `get_plan`, …) stays the same.
- **CORS**: set `CCS_CORS_ORIGINS="https://your.domain,https://other"`.
- **Observability**: every step's payload is appended to `session.history`, so
  you can replay any conversation by re-posting those payloads in order.

## Recreating a specific video moment

The "套用影片示範" (Apply video demo) buttons in each step pre-fill the values
from the training video so you can reproduce the exact flow shown on screen:

```
Manual:    Taiwan 2025 / Internal Pitch / test 260128 / 2026-02-16 / 4w /
           All adults + TA 30-54 A / Reach / TV + YouTube + Meta Video /
           2,500 + 125,000 + 100,000 per week
Automatic: same Brief → 6,000,000 total budget → TV + Meta Mandatory /
           YouTube Optional → no Min/Max → global net-reach optimization
```

## Multi-tenant persistence (v4)

| Capability | Env | Default |
|---|---|---|
| Admin bootstrap key | `CCS_ADMIN_KEY` (or legacy `CCS_API_KEY`) | empty |
| SQLite database path | `CCS_DATABASE_PATH` | `./app/var/ccs.db` |
| Session TTL | `CCS_SESSION_TTL_SECONDS` | 7 days |

- **Users** — every call carries an `X-API-Key`. The key is looked up
  (hashed `sha256`) in the `users` table. Admin users call `POST /api/users`
  to mint keys for others; the key is returned exactly once.
- **Projects** — every user has a `Default` project plus any they create.
  `GET /api/projects` lists only theirs. `DELETE` soft-archives.
- **Sessions** — `POST /api/sessions` accepts `project_id`; missing = default.
- **Conversations (option C)** — every `/advance` call appends a
  `ConversationTurn` with the **full** brief snapshot, payload, and the
  server-rendered prompt. `GET /api/sessions/{id}/conversation` replays.
- **Migration** — on first boot the legacy `storage.json` (v1..v3) is
  imported into SQLite idempotently (rerun safe).

Admin flow to onboard a teammate:

```bash
# 1. ship env with a random admin key
export CCS_ADMIN_KEY="$(openssl rand -hex 24)"
./run.sh

# 2. as admin, mint a key for Bob
curl -sX POST http://localhost:8000/api/users \
     -H "X-API-Key: $CCS_ADMIN_KEY" -H "Content-Type: application/json" \
     -d '{"name":"bob"}' | jq .
# { "user": {...}, "api_key": "one-time-token", "note": "Store this key NOW" }

# 3. Bob logs into the UI with that key
```

## Plan Comparison (v3)

Once two or more plans exist against a Brief, the Review step shows a
**Compare plans ▶** button that opens a picker (checkbox list of saved
plans) and renders a full comparison view:

| Chart | What it shows |
|---|---|
| Performance summary (grouped bar) | Reach / attentive / engagement / frequency × 10 / brand consideration across plans |
| Budget per channel (stacked horizontal bar) | Where each plan spends |
| Reach metrics (grouped bar) | Net reach + Attitude + Consideration + Knowledge |
| Frequency distribution (line) | Reach at 1+ … 10+ exposures (derived via `optimizer.frequency_distribution`) |
| Weekly GRP trend (line) | Week-by-week roll-up (`optimizer.weekly_grp`) |

Plus a **Duplication & Exclusivity** table per plan (from
`optimizer.duplication_matrix`).

Chart.js 4 is loaded lazily from jsDelivr on first Compare click. If the
CDN is blocked (air-gapped install, firewall) the tables still render and
the user sees a "charts unavailable" notice.

## Security

The [security review](docs/) drove a TDD pass (see `docs/TDD.md`). Current
posture:

| Risk | Mitigation | Test |
|---|---|---|
| Path traversal via static fallback (`/..%2Fconfig.py`) | `_safe_static_path()` resolves to absolute and verifies the target is inside `STATIC_DIR`; out-of-bounds requests fall through to `index.html`. | [tests/test_static_safety.py](tests/test_static_safety.py) |
| CORS wildcard + credentials | `app.config.validate_cors()` raises `RuntimeError` at startup if `CCS_CORS_ORIGINS=*` is combined with `CCS_CORS_CREDENTIALS=true`. Credentials are OFF by default. | [tests/test_cors_guard.py](tests/test_cors_guard.py) |
| Unbounded inputs (runaway JSON / overflow) | Agent enforces `project_name ≤ 120`, `channel_ids ≤ 50`, weekly budget ≤ 1 × 10¹² TWD with friendly error messages. | [tests/test_input_limits.py](tests/test_input_limits.py) |
| Cross-worker race on `storage.json` | `_cross_process_lock()` adds an `fcntl.flock` advisory lock + per-PID tmp file, so `uvicorn --workers N` stays correct. | [tests/test_storage_locking.py](tests/test_storage_locking.py) |
| Reflected XSS when echoing user text into the chat | `userSay()` uses `document.createTextNode()`; there is also an `escapeHTML()` helper for any future HTML-including echo path. | [tests/test_frontend_escape.py](tests/test_frontend_escape.py) |

### Deployment posture (v2)

Landed in iteration 2 ([docs/PRD_v2.md](docs/PRD_v2.md)):

| Knob | Env var | Default | Effect |
|---|---|---|---|
| API-key auth | `CCS_API_KEY` | empty = off | When set, every `/api/*` except `/api/health` requires `X-API-Key` header (constant-time compared). |
| Per-IP rate limit | `CCS_RATE_LIMIT` | `30/60` | Format `N/SECONDS`. Applies to POST/PUT/PATCH/DELETE; reads are unrestricted. 429 with `Retry-After` on exceed. |
| Session retention | `CCS_SESSION_TTL_SECONDS` | `604800` (7 d) | Sessions older than TTL are purged on the next write. Orphaned plans go too. Currently-saving session is protected. |

Not yet addressed (explicit follow-ups):

1. **SSO / SAML / OAuth** — separate project; API key is the short-term lever.
2. **Postgres migration** — still deferred until horizontal scaling is on the
   roadmap. The `storage.py` surface is already swap-ready.
3. **TLS + HSTS** — terminate at the reverse proxy; don't ship the container
   listening on plain HTTP.

### Verifying the fixes

```bash
# All tests
pytest -q
# 35 passed

# Live path-traversal probe (should NOT return config.py contents):
./run.sh &
curl -s http://localhost:8000/..%2Fconfig.py | grep -c "Runtime configuration"
# 0

# CORS guard check (should exit non-zero at boot):
CCS_CORS_ORIGINS=* CCS_CORS_CREDENTIALS=true uvicorn app.main:app --port 8001
# RuntimeError: Insecure CORS config ...
```

## License

Internal demo / educational use inside Dentsu Taiwan. No external distribution.
