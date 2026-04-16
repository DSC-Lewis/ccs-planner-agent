"""Conversational agent state machine.

Two flows — Manual and Automatic — share the first 6 steps, then diverge.
The state machine answers two questions each turn:

1. Given the current step + payload, is the input valid? If so, advance.
2. What's the next prompt, and what options should the UI render?
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple  # noqa: F401

from ..schemas import (
    AgentSession,
    AutomaticPlanInput,
    Brief,
    CommsSetup,
    ManualPlanInput,
    PlanningType,
    SessionMode,
    StepKey,
    StepPayload,
)
from . import optimizer, reference, storage


# ----- Step order per mode -----

_MANUAL_ORDER: List[StepKey] = [
    StepKey.SURVEY_CLIENT,
    StepKey.PROJECT_DATES,
    StepKey.TARGET_AUDIENCE,
    StepKey.PLANNING_TYPE,
    StepKey.COMMS_SETUP,
    StepKey.CHANNELS,
    StepKey.CALIBRATION,
    StepKey.MANUAL_PLAN,
    StepKey.REVIEW,
]

_AUTO_ORDER: List[StepKey] = [
    StepKey.SURVEY_CLIENT,
    StepKey.PROJECT_DATES,
    StepKey.TARGET_AUDIENCE,
    StepKey.PLANNING_TYPE,
    StepKey.COMMS_SETUP,
    StepKey.CHANNELS,
    StepKey.CRITERION,
    StepKey.BUDGET_CHANNELS,
    StepKey.MIN_MAX,
    StepKey.OPTIMIZE,
    StepKey.REVIEW,
]


def step_order(mode: SessionMode) -> List[StepKey]:
    return _MANUAL_ORDER if mode == SessionMode.MANUAL else _AUTO_ORDER


def step_index(session: AgentSession) -> int:
    try:
        return step_order(session.mode).index(session.step)
    except ValueError:
        return 0


class StepError(ValueError):
    """Raised for user-facing validation failures."""


# Input caps — mirrored in docs/PRD.md §NFR-1.3. Enforced in-agent (not in
# Pydantic) so the error messages stay human-readable and localised.
MAX_PROJECT_NAME_LEN = 120
MAX_CHANNEL_IDS = 50
MAX_WEEKLY_BUDGET_TWD = 1_000_000_000_000  # 1e12


# ---------- Step handlers ----------

def _apply_survey_client(session: AgentSession, payload: StepPayload) -> None:
    if not payload.survey_id:
        raise StepError("Survey is mandatory. 例如：tw_2025")
    if payload.survey_id not in {s.id for s in reference.surveys()}:
        raise StepError(f"Unknown survey '{payload.survey_id}'.")
    if not payload.client_id:
        raise StepError("Client is mandatory.")
    if payload.client_id not in {c.id for c in reference.clients()}:
        raise StepError(f"Unknown client '{payload.client_id}'.")
    session.brief.survey_id = payload.survey_id
    session.brief.client_id = payload.client_id


def _apply_project_dates(session: AgentSession, payload: StepPayload) -> None:
    if not payload.project_name or not payload.project_name.strip():
        raise StepError("Project Name 是必填欄位。")
    if len(payload.project_name) > MAX_PROJECT_NAME_LEN:
        raise StepError(
            f"project_name 最多 {MAX_PROJECT_NAME_LEN} 字元，目前 "
            f"{len(payload.project_name)}。"
        )
    weeks = payload.weeks if payload.weeks is not None else session.brief.weeks
    if weeks <= 0 or weeks > 52:
        raise StepError("Weeks must be between 1 and 52.")
    start = payload.start_date or session.brief.start_date
    end = start + timedelta(days=7 * weeks - 1)
    session.brief.project_name = payload.project_name.strip()
    session.brief.start_date = start
    session.brief.weeks = weeks
    session.brief.end_date = end


def _apply_target_audience(session: AgentSession, payload: StepPayload) -> None:
    ids = payload.target_ids or []
    if not ids:
        raise StepError("至少要選一個 Target Audience。")
    known = {t.id for t in reference.targets()}
    unknown = [t for t in ids if t not in known]
    if unknown:
        raise StepError(f"Unknown target(s): {', '.join(unknown)}")
    session.brief.target_ids = ids


def _apply_planning_type(session: AgentSession, payload: StepPayload) -> None:
    if payload.planning_type is None:
        raise StepError("請選擇 Planning type (Reach 或 Comm)。")
    session.brief.planning_type = payload.planning_type


def _apply_comms(session: AgentSession, payload: StepPayload) -> None:
    if session.brief.planning_type != PlanningType.COMM:
        return
    if not payload.comms:
        raise StepError("請完成 Comms setup。")
    if not payload.comms.kpi_ids:
        raise StepError("至少要選 1 個 Brand KPI。")
    session.brief.comms = payload.comms


def _apply_channels(session: AgentSession, payload: StepPayload) -> None:
    ids = payload.channel_ids or []
    if not ids:
        raise StepError("請至少選擇 1 個 channel。")
    # Count check fires BEFORE the whitelist check so an attacker flooding the
    # field with bogus ids triggers the cheap rejection first.
    if len(ids) > MAX_CHANNEL_IDS:
        raise StepError(
            f"channel_ids 太多（{len(ids)} > {MAX_CHANNEL_IDS}）。"
        )
    valid = set(reference.all_channel_ids())
    unknown = [c for c in ids if c not in valid]
    if unknown:
        raise StepError(f"Unknown channel(s): {', '.join(unknown)}")
    session.brief.channel_ids = ids


def _apply_calibration(session: AgentSession, payload: StepPayload) -> None:
    """Manual step — previewing metrics only. Nothing to persist; just advance."""
    return


def _apply_manual_plan(session: AgentSession, payload: StepPayload) -> None:
    if payload.weekly_budgets is None:
        raise StepError("請輸入每週的預算配置。")
    for ch, weekly in payload.weekly_budgets.items():
        if ch not in session.brief.channel_ids:
            raise StepError(f"Channel '{ch}' 不在 Brief 裡。")
        if len(weekly) != session.brief.weeks:
            raise StepError(
                f"Channel '{ch}' 應該有 {session.brief.weeks} 週的預算。"
            )
        if any(b < 0 for b in weekly):
            raise StepError(f"Channel '{ch}' 的預算不可以為負數。")
        if any(b > MAX_WEEKLY_BUDGET_TWD for b in weekly):
            raise StepError(
                f"Channel '{ch}' 的週預算過大 (> {MAX_WEEKLY_BUDGET_TWD:,} TWD "
                "is too large / exceeds limit)."
            )
    session.manual_input = ManualPlanInput(weekly_budgets=payload.weekly_budgets)


def _apply_criterion(session: AgentSession, payload: StepPayload) -> None:
    opts = reference.optimization_options()
    cri = {c.id for c in opts["criteria"]}
    strat = {s.id for s in opts["strategies"]}
    if payload.criterion_id and payload.criterion_id not in cri:
        raise StepError(f"Unknown criterion '{payload.criterion_id}'.")
    if payload.strategy_id and payload.strategy_id not in strat:
        raise StepError(f"Unknown strategy '{payload.strategy_id}'.")
    session.automatic_input.criterion_id = payload.criterion_id or session.automatic_input.criterion_id
    session.automatic_input.strategy_id = payload.strategy_id or session.automatic_input.strategy_id


def _apply_budget_channels(session: AgentSession, payload: StepPayload) -> None:
    if not payload.total_budget_twd or payload.total_budget_twd <= 0:
        raise StepError("請輸入有效的總預算 (TWD)。")
    mandatory = payload.mandatory_channel_ids or []
    optional = payload.optional_channel_ids or []
    in_brief = set(session.brief.channel_ids)
    for ch in mandatory + optional:
        if ch not in in_brief:
            raise StepError(f"Channel '{ch}' 不在 Brief 裡，請先回到 channels 步驟。")
    if not mandatory and not optional:
        raise StepError("請至少將 1 個 channel 設為 Mandatory 或 Optional。")
    session.automatic_input.total_budget_twd = payload.total_budget_twd
    session.automatic_input.mandatory_channel_ids = mandatory
    session.automatic_input.optional_channel_ids = optional


def _apply_min_max(session: AgentSession, payload: StepPayload) -> None:
    session.automatic_input.constraints = payload.constraints or {}


def _apply_optimize(session: AgentSession, payload: StepPayload,
                    *, owner_id: str) -> None:
    """Trigger the optimization. The plan is computed + persisted here so the
    REVIEW step can show it."""
    plan = optimizer.compute_automatic_plan(session.brief, session.automatic_input)
    plan.brief_id = session.brief.id or session.id
    plan.name = "Plan 2"
    saved = storage.save_plan(plan, owner_id=owner_id)
    session.plan_id = saved.id


_STEP_HANDLERS = {
    StepKey.SURVEY_CLIENT:   _apply_survey_client,
    StepKey.PROJECT_DATES:   _apply_project_dates,
    StepKey.TARGET_AUDIENCE: _apply_target_audience,
    StepKey.PLANNING_TYPE:   _apply_planning_type,
    StepKey.COMMS_SETUP:     _apply_comms,
    StepKey.CHANNELS:        _apply_channels,
    StepKey.CALIBRATION:     _apply_calibration,
    StepKey.MANUAL_PLAN:     _apply_manual_plan,
    StepKey.CRITERION:       _apply_criterion,
    StepKey.BUDGET_CHANNELS: _apply_budget_channels,
    StepKey.MIN_MAX:         _apply_min_max,
    StepKey.OPTIMIZE:        _apply_optimize,
    StepKey.REVIEW:          lambda s, p: None,
}


# ---------- Session lifecycle ----------

def create_session(mode: SessionMode, *, owner_id: str,
                   project_id: Optional[str] = None) -> AgentSession:
    s = AgentSession(id="", mode=mode)
    brief = s.brief
    brief.start_date = date(2026, 2, 16)
    brief.weeks = 4
    brief.end_date = brief.start_date + timedelta(days=7 * brief.weeks - 1)
    return storage.save_session(s, owner_id=owner_id, project_id=project_id)


def fork(source: AgentSession, target_mode: SessionMode, *,
         owner_id: str, project_id: Optional[str] = None) -> AgentSession:
    """Create a new session in the opposite mode, carrying the source Brief.

    Used when a PM has just finished Plan 1 (Manual) and wants CCS Planner to
    also generate Plan 2 (Automatic) for the same Brief — no need to re-answer
    survey / client / target / channels.

    The new session jumps straight to the first mode-specific step:

    * -> automatic : ``criterion``
    * -> manual    : ``calibration``
    """
    if source.mode == target_mode:
        raise StepError(
            f"Source session is already in {target_mode.value} mode — "
            "nothing to fork."
        )
    if not source.brief.channel_ids:
        raise StepError(
            "Source session has no channels selected yet; finish at least the "
            "channels step before forking to another mode."
        )

    new = AgentSession(id="", mode=target_mode)
    # Deep-copy the brief so edits on one session don't leak into the other
    new.brief = source.brief.model_copy(deep=True)
    new.brief.id = None  # reset any brief id so each session keeps its own

    if target_mode == SessionMode.AUTOMATIC:
        # Seed the automatic_input with the brief channels so mandatory picker
        # starts populated.
        new.automatic_input.mandatory_channel_ids = list(source.brief.channel_ids)
        new.step = StepKey.CRITERION
    else:
        # Manual path: land on calibration (the first manual-only step after
        # channel selection).
        new.step = StepKey.CALIBRATION

    new.history.append({
        "step": "__forked_from__",
        "payload": {
            "source_session_id": source.id,
            "source_mode": source.mode.value,
            "source_plan_id": source.plan_id,
        },
    })
    return storage.save_session(new, owner_id=owner_id, project_id=project_id)


def advance(session: AgentSession, payload: StepPayload, *,
            owner_id: str, prompt: str = "",
            log_turn: bool = True) -> AgentSession:
    order = step_order(session.mode)
    prev_step = session.step
    if payload.action == "back":
        idx = max(0, step_index(session) - 1)
        session.step = order[idx]
    elif payload.action == "skip":
        idx = min(len(order) - 1, step_index(session) + 1)
        session.step = order[idx]
    else:
        handler = _STEP_HANDLERS.get(session.step)
        if not handler:
            raise StepError(f"Unknown step '{session.step}'")
        # Optimize handler needs owner_id to persist the plan.
        if session.step == StepKey.OPTIMIZE:
            _apply_optimize(session, payload, owner_id=owner_id)
        else:
            handler(session, payload)
        # Auto-skip Comms if user chose Reach planning
        idx = step_index(session) + 1
        if (
            idx < len(order)
            and order[idx] == StepKey.COMMS_SETUP
            and session.brief.planning_type == PlanningType.REACH
        ):
            idx += 1
        session.step = order[min(idx, len(order) - 1)]

    # After manual_plan we also compute & save the plan
    if session.mode == SessionMode.MANUAL and session.step == StepKey.REVIEW and not session.plan_id:
        plan = optimizer.compute_manual_plan(session.brief, session.manual_input)
        plan.brief_id = session.brief.id or session.id
        plan.name = "Plan 1"
        saved = storage.save_plan(plan, owner_id=owner_id)
        session.plan_id = saved.id

    session.history.append({
        "step": session.step.value,
        "payload": payload.model_dump(mode="json", exclude_none=True),
    })
    saved = storage.save_session(session, owner_id=owner_id)

    # Append a ConversationTurn (option C — full brief snapshot).
    if log_turn:
        storage.log_turn(
            session_id=saved.id,
            step=prev_step.value,
            payload=payload.model_dump(mode="json", exclude_none=True),
            prompt=prompt,
            brief_snapshot=saved.brief.model_dump(mode="json"),
        )
    return saved


# ---------- Prompt & options per step ----------

def render_prompt(session: AgentSession) -> Tuple[str, Dict]:
    """Return (prompt_text, available_options) for the current step."""
    step = session.step
    opts: Dict = {}
    if step == StepKey.SURVEY_CLIENT:
        text = "請選擇 Survey 與 Client。"
        opts = {
            "surveys": [s.model_dump() for s in reference.surveys()],
            "clients": [c.model_dump() for c in reference.clients()],
        }
    elif step == StepKey.PROJECT_DATES:
        text = "請設定 Project Name 與投放期間（end_date 會依 weeks 自動計算）。"
        opts = {"week_options": [2, 3, 4, 5, 6, 8, 12]}
    elif step == StepKey.TARGET_AUDIENCE:
        text = "請選擇一個或多個 Target Audience。"
        opts = {"targets": [t.model_dump() for t in reference.targets()]}
    elif step == StepKey.PLANNING_TYPE:
        text = "Reach 模式聚焦觸及；Comm 模式會加入 Brand KPI 校準。"
        opts = {"planning_types": ["Reach", "Comm"]}
    elif step == StepKey.COMMS_SETUP:
        text = "Comms Setup — 調整 Brand/Message 相對重要性 (0–10) 與至少 1 個 KPI。"
        opts = {"brand_kpis": [k.model_dump() for k in reference.brand_kpis()]}
    elif step == StepKey.CHANNELS:
        text = "請從以下群組中選擇要納入 plan 的 channels。"
        opts = {"channel_groups": [g.model_dump() for g in reference.channel_groups()]}
    elif step == StepKey.CALIBRATION:
        text = "預覽所選 channel 的 Cost / Penetration / Attention / Engagement。"
        opts = {
            "metrics": {
                ch: reference.channel_metrics()[ch].model_dump()
                for ch in session.brief.channel_ids
                if ch in reference.channel_metrics()
            }
        }
    elif step == StepKey.MANUAL_PLAN:
        text = "逐週輸入每個 channel 的預算（TWD）。"
        opts = {
            "suggestions": {
                ch: optimizer.suggest_weekly_budget(ch, session.brief.weeks)
                for ch in session.brief.channel_ids
            },
            "weeks": session.brief.weeks,
            "start_date": session.brief.start_date.isoformat(),
        }
    elif step == StepKey.CRITERION:
        text = "選擇 Optimization Criterion 與 Strategy。"
        opts = {
            k: [o.model_dump() for o in v]
            for k, v in reference.optimization_options().items()
        }
    elif step == StepKey.BUDGET_CHANNELS:
        text = "輸入總預算，並把 channels 分為 Mandatory（必上）或 Optional（系統可選）。"
        opts = {
            "channel_ids": session.brief.channel_ids,
            "labels": {ch: reference.channel_label(ch) for ch in session.brief.channel_ids},
        }
    elif step == StepKey.MIN_MAX:
        text = "為每個 channel 設定 Min/Max 限制（可留空不限制）。"
        opts = {
            "channel_ids": session.brief.channel_ids,
            "labels": {ch: reference.channel_label(ch) for ch in session.brief.channel_ids},
        }
    elif step == StepKey.OPTIMIZE:
        text = "依條件執行自動化最佳化。"
        opts = {
            "budget_step_curve": optimizer.budget_step_curve(
                session.brief, session.automatic_input
            )
        }
    elif step == StepKey.REVIEW:
        text = "Brief 已完成，可以 Save plan。"
        opts = {}
    else:
        text = ""
    return text, opts


def warnings_for(session: AgentSession) -> List[str]:
    out: List[str] = []
    if session.brief.survey_id:
        out.extend(
            reference.validate_target_against_survey(
                session.brief.target_ids, session.brief.survey_id
            )
        )
    return out


def is_completed(session: AgentSession) -> bool:
    return session.step == StepKey.REVIEW and session.plan_id is not None
