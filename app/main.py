"""FastAPI entrypoint — v4 multi-tenant wiring.

Every ``/api/*`` route (except ``/api/health``) pulls the caller's
``User`` off ``request.state.user`` (set by ``APIKeyMiddleware``). Service
calls carry ``owner_id`` explicitly so scoping is enforced at the data
layer, not just the HTTP layer (NFR-5.2).
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import (
    ADMIN_KEY,
    CORS_CREDENTIALS,
    CORS_ORIGINS,
    STATIC_DIR,
    validate_cors,
)
from .schemas import (
    AgentSession,
    ConversationTurn,
    CreateProjectRequest,
    CreateSessionRequest,
    CreateUserRequest,
    Plan,
    PlanActualsWrite,
    Project,
    SessionMode,
    SessionStepResponse,
    StepPayload,
    User,
)
from .services import actuals as actuals_service
from .services import agent as agent_service
from .services import optimizer, reference, storage
from .services.auth import APIKeyMiddleware
from .services.rate_limit import RateLimitMiddleware

# Fail fast on insecure CORS combos (wildcard + credentials).
validate_cors()


class ForkSessionRequest(BaseModel):
    target_mode: SessionMode
    project_id: Optional[str] = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Replaces the deprecated @app.on_event pattern. Runs schema init +
    admin bootstrap at startup."""
    storage.init_schema()
    storage.auto_migrate_legacy_if_empty()
    if ADMIN_KEY:
        storage.ensure_admin(name="admin", api_key=ADMIN_KEY)
    yield
    # Nothing to clean up on shutdown.


app = FastAPI(
    title="CCS Planner · Conversational Agent",
    version=__version__,
    description=(
        "Replicates the CCS Planner Brief-modification flow as a multi-tenant "
        "conversational Agent with Manual and Automatic modes. v4 adds Projects, "
        "per-user auth, and full conversation history."
    ),
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(APIKeyMiddleware)
app.add_middleware(RateLimitMiddleware)


# ---------- Auth dependency ----------

def current_user(request: Request) -> User:
    """Read the User set by ``APIKeyMiddleware`` or raise 401."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required. Set X-API-Key header.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admin-only endpoint.")
    return user


# ---------- Users ----------

@app.get("/api/me", response_model=User)
def get_me(user: User = Depends(current_user)):
    return user


@app.post("/api/users", status_code=201)
def create_user(req: CreateUserRequest, admin: User = Depends(require_admin)):
    if storage.get_user_by_name(req.name):
        raise HTTPException(409, f"User '{req.name}' already exists.")
    # Generate a random 32-byte token once and return it — NEVER stored plain.
    plain_key = secrets.token_urlsafe(32)
    u = storage.create_user(name=req.name, api_key=plain_key, is_admin=req.is_admin)
    return {"user": u.model_dump(), "api_key": plain_key,
            "note": "Store this key NOW — it cannot be retrieved again."}


@app.get("/api/users", response_model=List[User])
def list_users(admin: User = Depends(require_admin)):
    """Admin-only — no plaintext keys are ever returned."""
    return storage.list_users()


@app.post("/api/users/{user_id}/disable")
def disable_user(user_id: str, admin: User = Depends(require_admin)):
    if user_id == admin.id:
        raise HTTPException(422, "Cannot disable self — you'd lock out admin.")
    if not storage.get_user(user_id):
        raise HTTPException(404, f"User '{user_id}' not found.")
    storage.set_user_active(user_id, False)
    return {"user_id": user_id, "is_active": False}


@app.post("/api/users/{user_id}/enable")
def enable_user(user_id: str, admin: User = Depends(require_admin)):
    if not storage.get_user(user_id):
        raise HTTPException(404, f"User '{user_id}' not found.")
    storage.set_user_active(user_id, True)
    return {"user_id": user_id, "is_active": True}


@app.post("/api/users/{user_id}/rotate")
def rotate_user_key(user_id: str, admin: User = Depends(require_admin)):
    if not storage.get_user(user_id):
        raise HTTPException(404, f"User '{user_id}' not found.")
    plain_key = secrets.token_urlsafe(32)
    storage.rotate_user_key(user_id, plain_key)
    return {"user_id": user_id, "api_key": plain_key,
            "note": "Store this key NOW — the old one is now invalid."}


# ---------- Projects ----------

@app.get("/api/projects", response_model=List[Project])
def list_projects(user: User = Depends(current_user)):
    return storage.list_projects(owner_id=user.id)


@app.post("/api/projects", response_model=Project, status_code=201)
def create_project(req: CreateProjectRequest, user: User = Depends(current_user)):
    return storage.create_project(owner_id=user.id, name=req.name)


@app.get("/api/projects/{project_id}", response_model=Project)
def get_project(project_id: str, user: User = Depends(current_user)):
    p = storage.get_project(project_id, owner_id=user.id)
    if not p:
        raise HTTPException(404, f"Project '{project_id}' not found or inaccessible.")
    return p


@app.get("/api/projects/{project_id}/sessions", response_model=List[AgentSession])
def list_project_sessions(project_id: str, user: User = Depends(current_user)):
    if not storage.get_project(project_id, owner_id=user.id):
        raise HTTPException(404, "Project not found.")
    return storage.list_sessions(owner_id=user.id, project_id=project_id)


@app.get("/api/projects/{project_id}/plans", response_model=List[Plan])
def list_project_plans(project_id: str, user: User = Depends(current_user)):
    if not storage.get_project(project_id, owner_id=user.id):
        raise HTTPException(404, "Project not found.")
    sessions = storage.list_sessions(owner_id=user.id, project_id=project_id)
    out: List[Plan] = []
    for s in sessions:
        out.extend(storage.list_plans(owner_id=user.id, brief_id=s.id))
    return out


@app.delete("/api/projects/{project_id}")
def archive_project(project_id: str, user: User = Depends(current_user)):
    ok = storage.archive_project(project_id, owner_id=user.id)
    if not ok:
        raise HTTPException(404, "Project not found.")
    return {"archived": True}


# ---------- Reference data (read-only) ----------

@app.get("/api/reference/surveys")
def list_surveys():
    return [s.model_dump() for s in reference.surveys()]


@app.get("/api/reference/clients")
def list_clients():
    return [c.model_dump() for c in reference.clients()]


@app.get("/api/reference/targets")
def list_targets():
    return [t.model_dump() for t in reference.targets()]


@app.get("/api/reference/brand-kpis")
def list_brand_kpis():
    return [k.model_dump() for k in reference.brand_kpis()]


@app.get("/api/reference/channels")
def list_channels():
    return {
        "groups": [g.model_dump() for g in reference.channel_groups()],
        "metrics": {k: v.model_dump() for k, v in reference.channel_metrics().items()},
    }


@app.get("/api/reference/optimization")
def list_optimization():
    return {
        "criteria":   [o.model_dump() for o in reference.optimization_options()["criteria"]],
        "strategies": [o.model_dump() for o in reference.optimization_options()["strategies"]],
        "frequency_thresholds": reference.frequency_thresholds(),
    }


# ---------- Session & conversation flow ----------

def _respond(session: AgentSession, user: User) -> SessionStepResponse:
    prompt, opts = agent_service.render_prompt(session)
    plan = storage.get_plan(session.plan_id, owner_id=user.id) if session.plan_id else None
    return SessionStepResponse(
        session=session,
        prompt=prompt,
        available_options=opts,
        warnings=agent_service.warnings_for(session),
        completed=agent_service.is_completed(session),
        plan=plan,
    )


@app.post("/api/sessions", response_model=SessionStepResponse)
def create_session(req: CreateSessionRequest, user: User = Depends(current_user)):
    project_id = req.project_id
    if project_id:
        if not storage.get_project(project_id, owner_id=user.id):
            raise HTTPException(404, "Project not found for this user.")
    else:
        project_id = storage.ensure_default_project(user.id).id
    session = agent_service.create_session(req.mode, owner_id=user.id,
                                           project_id=project_id)
    return _respond(session, user)


@app.get("/api/sessions", response_model=List[AgentSession])
def list_sessions(project_id: Optional[str] = None,
                  user: User = Depends(current_user)):
    return storage.list_sessions(owner_id=user.id, project_id=project_id)


@app.get("/api/sessions/{session_id}", response_model=SessionStepResponse)
def get_session(session_id: str, user: User = Depends(current_user)):
    session = storage.get_session(session_id, owner_id=user.id)
    if not session:
        raise HTTPException(404, f"Session '{session_id}' not found.")
    return _respond(session, user)


@app.post("/api/sessions/{session_id}/advance", response_model=SessionStepResponse)
def advance_session(session_id: str, payload: StepPayload,
                    user: User = Depends(current_user)):
    session = storage.get_session(session_id, owner_id=user.id)
    if not session:
        raise HTTPException(404, f"Session '{session_id}' not found.")
    try:
        prompt, _ = agent_service.render_prompt(session)
        session = agent_service.advance(session, payload, owner_id=user.id,
                                        prompt=prompt)
    except agent_service.StepError as e:
        raise HTTPException(400, str(e))
    return _respond(session, user)


@app.post("/api/sessions/{session_id}/fork", response_model=SessionStepResponse)
def fork_session(session_id: str, req: ForkSessionRequest,
                 user: User = Depends(current_user)):
    source = storage.get_session(session_id, owner_id=user.id)
    if not source:
        raise HTTPException(404, f"Session '{session_id}' not found.")
    project_id = req.project_id or storage.ensure_default_project(user.id).id
    if req.project_id and not storage.get_project(req.project_id, owner_id=user.id):
        raise HTTPException(404, "Project not found for this user.")
    try:
        new = agent_service.fork(source, req.target_mode,
                                 owner_id=user.id, project_id=project_id)
    except agent_service.StepError as e:
        raise HTTPException(400, str(e))
    return _respond(new, user)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, user: User = Depends(current_user)):
    return {"deleted": storage.delete_session(session_id, owner_id=user.id)}


@app.get("/api/sessions/{session_id}/conversation",
         response_model=List[ConversationTurn])
def get_conversation(session_id: str, user: User = Depends(current_user)):
    if not storage.get_session(session_id, owner_id=user.id):
        raise HTTPException(404, "Session not found.")
    return storage.get_conversation(session_id, owner_id=user.id)


# ---------- Plans ----------

@app.get("/api/plans", response_model=List[Plan])
def list_plans(brief_id: Optional[str] = None,
               user: User = Depends(current_user)):
    return storage.list_plans(owner_id=user.id, brief_id=brief_id)


@app.get("/api/plans/{plan_id}", response_model=Plan)
def get_plan(plan_id: str, user: User = Depends(current_user)):
    plan = storage.get_plan(plan_id, owner_id=user.id)
    if not plan:
        raise HTTPException(404, f"Plan '{plan_id}' not found.")
    return plan


# ---------- Plan actuals (v6 · FR-27..29) ----------

def _get_plan_or_404(plan_id: str, user: User) -> Plan:
    plan = storage.get_plan(plan_id, owner_id=user.id)
    if not plan:
        raise HTTPException(404, f"Plan '{plan_id}' not found.")
    return plan


@app.get("/api/plans/{plan_id}/actuals")
def get_plan_actuals(plan_id: str, user: User = Depends(current_user)):
    _get_plan_or_404(plan_id, user)
    rows = storage.list_actuals(plan_id, owner_id=user.id)
    return [r.model_dump(mode="json") for r in rows]


@app.put("/api/plans/{plan_id}/actuals")
def put_plan_actuals(plan_id: str, body: PlanActualsWrite,
                     user: User = Depends(current_user)):
    plan = _get_plan_or_404(plan_id, user)
    plan_weeks = len(plan.allocations[0].weeks) if plan.allocations else 0
    # Validate + normalise every record before touching the DB.
    for rec in body.records:
        try:
            actuals_service.validate_record(rec, plan_weeks=plan_weeks)
        except actuals_service.ActualsError as e:
            raise HTTPException(422, str(e))
    # Make sure plan_id on each record matches the URL.
    normalised = [rec.model_copy(update={"plan_id": plan_id}) for rec in body.records]
    stored = storage.upsert_actuals_records(
        plan_id, normalised, owner_id=user.id, recorded_by=user.id,
    )
    return {"records": [r.model_dump(mode="json") for r in stored]}


@app.delete("/api/plans/{plan_id}/actuals/{record_id}")
def delete_plan_actuals_record(plan_id: str, record_id: str,
                               user: User = Depends(current_user)):
    _get_plan_or_404(plan_id, user)
    ok = storage.delete_actuals_record(plan_id, record_id, owner_id=user.id)
    if not ok:
        raise HTTPException(404, "Actuals record not found.")
    return {"deleted": True}


@app.get("/api/plans/{plan_id}/actuals/history")
def get_plan_actuals_history(plan_id: str, user: User = Depends(current_user)):
    _get_plan_or_404(plan_id, user)
    return storage.list_actuals_history(plan_id, owner_id=user.id)


@app.get("/api/plans/{plan_id}/report")
def get_plan_report(plan_id: str, user: User = Depends(current_user)):
    plan = _get_plan_or_404(plan_id, user)
    records = storage.list_actuals(plan_id, owner_id=user.id)
    return actuals_service.build_report(plan, records)


@app.get("/api/plans/{plan_id}/report.html")
def get_plan_report_html(plan_id: str, user: User = Depends(current_user)):
    from fastapi.responses import HTMLResponse
    plan = _get_plan_or_404(plan_id, user)
    records = storage.list_actuals(plan_id, owner_id=user.id)
    report = actuals_service.build_report(plan, records)
    return HTMLResponse(actuals_service.render_report_html(plan, report))


# ---------- Calibration coverage (v6 · FR-32) ----------

@app.get("/api/calibration/coverage")
def calibration_coverage(client_id: str, target_id: str,
                         user: User = Depends(current_user)):
    """Banner-driver. PR A surfaces `has_history` + `n`; PR B extends
    this payload with `confidence_score` once the profile layer lands."""
    n = storage.count_actuals_for_client_target(client_id, target_id,
                                                owner_id=user.id)
    return {
        "client_id": client_id,
        "target_id": target_id,
        "has_history": n > 0,
        "n": n,
    }


@app.post("/api/plans/compare")
def compare_plans(plan_ids: List[str], user: User = Depends(current_user)):
    plans = [storage.get_plan(p, owner_id=user.id) for p in plan_ids]
    plans = [p for p in plans if p]
    if len(plans) < 2:
        raise HTTPException(400, "Need at least 2 valid plan ids to compare.")

    def _augment(plan) -> dict:
        d = plan.model_dump()
        d["frequency_distribution"] = optimizer.frequency_distribution(plan)
        d["duplication"]            = optimizer.duplication_matrix(plan)
        d["weekly_grp"]             = optimizer.weekly_grp(plan)
        return d

    return {
        "plans": [_augment(p) for p in plans],
        "delta": {
            "total_budget_twd":  plans[1].summary.total_budget_twd  - plans[0].summary.total_budget_twd,
            "net_reach_pct":     plans[1].summary.net_reach_pct     - plans[0].summary.net_reach_pct,
            "frequency":         plans[1].summary.frequency         - plans[0].summary.frequency,
            "total_impressions": plans[1].summary.total_impressions - plans[0].summary.total_impressions,
        },
    }


# ---------- Health ----------

@app.get("/api/health")
def health():
    """Liveness + shallow DB readiness probe.

    Returns 503 when the SQLite backend is unreachable so a load balancer
    can route traffic away from a broken node instead of happily claiming
    liveness while every user-facing request errors out."""
    from fastapi.responses import JSONResponse
    try:
        # Cheapest possible probe — counts are index-only lookups.
        storage._count("sessions")
        db_state = "ok"
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": __version__, "db": "error"},
        )
    return {"status": "ok", "version": __version__, "db": db_state}


# ---------- Static frontend ----------

def _safe_static_path(user_path: str) -> Path | None:
    base = STATIC_DIR.resolve()
    try:
        target = (base / user_path.lstrip("/")).resolve()
    except (OSError, RuntimeError):
        return None
    if target != base and base not in target.parents:
        return None
    return target


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR, html=False), name="assets")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{path:path}")
    def spa_fallback(path: str):
        target = _safe_static_path(path)
        if target is None:
            return FileResponse(STATIC_DIR / "index.html")
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")
