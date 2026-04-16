"""FastAPI entrypoint — v4 multi-tenant wiring.

Every ``/api/*`` route (except ``/api/health``) pulls the caller's
``User`` off ``request.state.user`` (set by ``APIKeyMiddleware``). Service
calls carry ``owner_id`` explicitly so scoping is enforced at the data
layer, not just the HTTP layer (NFR-5.2).
"""
from __future__ import annotations

import secrets
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
    Project,
    SessionMode,
    SessionStepResponse,
    StepPayload,
    User,
)
from .services import agent as agent_service
from .services import optimizer, reference, storage
from .services.auth import APIKeyMiddleware
from .services.rate_limit import RateLimitMiddleware

# Fail fast on insecure CORS combos (wildcard + credentials).
validate_cors()


class ForkSessionRequest(BaseModel):
    target_mode: SessionMode
    project_id: Optional[str] = None


app = FastAPI(
    title="CCS Planner · Conversational Agent",
    version=__version__,
    description=(
        "Replicates the CCS Planner Brief-modification flow as a multi-tenant "
        "conversational Agent with Manual and Automatic modes. v4 adds Projects, "
        "per-user auth, and full conversation history."
    ),
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


# ---------- Startup ----------

@app.on_event("startup")
def _startup() -> None:
    storage.init_schema()
    # Best-effort import of the pre-v4 storage.json, idempotent.
    storage.auto_migrate_legacy_if_empty()
    # Bootstrap admin user from env key (CCS_ADMIN_KEY or legacy CCS_API_KEY).
    if ADMIN_KEY:
        storage.ensure_admin(name="admin", api_key=ADMIN_KEY)


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
    return {"status": "ok", "version": __version__}


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
