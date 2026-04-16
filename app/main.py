"""FastAPI entrypoint — wires the agent, storage, and static frontend."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import CORS_CREDENTIALS, CORS_ORIGINS, STATIC_DIR, validate_cors
from .services.auth import APIKeyMiddleware
from .services.rate_limit import RateLimitMiddleware

# Fail fast on insecure CORS combos (wildcard + credentials).
validate_cors()
from pydantic import BaseModel

from .schemas import (
    AgentSession,
    CreateSessionRequest,
    Plan,
    SessionMode,
    SessionStepResponse,
    StepPayload,
)


class ForkSessionRequest(BaseModel):
    target_mode: SessionMode
from .services import agent as agent_service
from .services import optimizer, reference, storage


app = FastAPI(
    title="CCS Planner · Conversational Agent",
    version=__version__,
    description=(
        "Replicates the CCS Planner Brief-modification flow as a conversational "
        "Agent with Manual and Automatic modes. Mock data mirrors the training "
        "video (Taiwan 2025 / Internal Pitch / Bread)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware is always installed; it's a no-op when CCS_API_KEY is empty.
app.add_middleware(APIKeyMiddleware)

# Rate limit is always on — with a generous default (30 req/min per IP).
app.add_middleware(RateLimitMiddleware)


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

def _respond(session: AgentSession) -> SessionStepResponse:
    prompt, opts = agent_service.render_prompt(session)
    plan = storage.get_plan(session.plan_id) if session.plan_id else None
    return SessionStepResponse(
        session=session,
        prompt=prompt,
        available_options=opts,
        warnings=agent_service.warnings_for(session),
        completed=agent_service.is_completed(session),
        plan=plan,
    )


@app.post("/api/sessions", response_model=SessionStepResponse)
def create_session(req: CreateSessionRequest):
    session = agent_service.create_session(req.mode)
    return _respond(session)


@app.get("/api/sessions", response_model=List[AgentSession])
def list_sessions():
    return storage.list_sessions()


@app.get("/api/sessions/{session_id}", response_model=SessionStepResponse)
def get_session(session_id: str):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session '{session_id}' not found.")
    return _respond(session)


@app.post("/api/sessions/{session_id}/advance", response_model=SessionStepResponse)
def advance_session(session_id: str, payload: StepPayload):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session '{session_id}' not found.")
    try:
        session = agent_service.advance(session, payload)
    except agent_service.StepError as e:
        raise HTTPException(400, str(e))
    return _respond(session)


@app.post("/api/sessions/{session_id}/fork", response_model=SessionStepResponse)
def fork_session(session_id: str, req: ForkSessionRequest):
    """Clone the brief into a new session in the other mode.

    Typical use: right after saving Plan 1 (Manual) the user asks CCS Planner
    to also build Plan 2 (Automatic). Instead of re-entering the whole brief
    we copy it and jump to the first mode-specific step.
    """
    source = storage.get_session(session_id)
    if not source:
        raise HTTPException(404, f"Session '{session_id}' not found.")
    try:
        new = agent_service.fork(source, req.target_mode)
    except agent_service.StepError as e:
        raise HTTPException(400, str(e))
    return _respond(new)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    ok = storage.delete_session(session_id)
    return {"deleted": ok}


# ---------- Plans ----------

@app.get("/api/plans", response_model=List[Plan])
def list_plans(brief_id: Optional[str] = None):
    return storage.list_plans(brief_id)


@app.get("/api/plans/{plan_id}", response_model=Plan)
def get_plan(plan_id: str):
    plan = storage.get_plan(plan_id)
    if not plan:
        raise HTTPException(404, f"Plan '{plan_id}' not found.")
    return plan


@app.post("/api/plans/compare")
def compare_plans(plan_ids: List[str]):
    plans = [storage.get_plan(p) for p in plan_ids]
    plans = [p for p in plans if p]
    if len(plans) < 2:
        raise HTTPException(400, "Need at least 2 valid plan ids to compare.")
    return {
        "plans": [p.model_dump() for p in plans],
        "delta": {
            "total_budget_twd": plans[1].summary.total_budget_twd - plans[0].summary.total_budget_twd,
            "net_reach_pct":    plans[1].summary.net_reach_pct   - plans[0].summary.net_reach_pct,
            "frequency":        plans[1].summary.frequency       - plans[0].summary.frequency,
            "total_impressions": plans[1].summary.total_impressions - plans[0].summary.total_impressions,
        }
    }


# ---------- Health ----------

@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


# ---------- Static frontend ----------

def _safe_static_path(user_path: str) -> Path | None:
    """Return the resolved path if it lives under STATIC_DIR, else None.

    Protects against path-traversal probes like ``/..%2Fconfig.py`` that would
    otherwise escape the static directory and leak arbitrary files. We compare
    resolved absolute paths (which collapse ``..``) rather than raw
    concatenation.
    """
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
        # Out-of-bounds → serve index.html (SPA router handles the 404 UX)
        if target is None:
            return FileResponse(STATIC_DIR / "index.html")
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")
