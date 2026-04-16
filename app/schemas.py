"""Pydantic schemas mirroring the CCS Planner brief / plan structures."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------- Reference ----------

class Survey(BaseModel):
    id: str
    label: str
    wave: str
    year: int
    deprecated: bool = False
    is_default: bool = False


class Client(BaseModel):
    id: str
    label: str
    type: str = "external"


class TargetAudience(BaseModel):
    id: str
    name: str
    source: str
    survey_id: str
    sample: int
    universe_000: int
    base_pct: float
    is_default: bool = False
    warning: Optional[str] = None


class BrandKPI(BaseModel):
    id: str
    label: str
    category: str


class ChannelLeaf(BaseModel):
    id: str
    label: str


class ChannelGroup(BaseModel):
    id: str
    label: str
    children: List[ChannelLeaf]


class ChannelMetric(BaseModel):
    category: str
    cpm_twd: float
    penetration_pct: float
    attention_pct: float
    engagement_pct: float
    buying_type: str = "Bought"


class OptimizationOption(BaseModel):
    id: str
    label: str
    unit: Optional[str] = None
    description: Optional[str] = None
    default: bool = False


# ---------- Brief ----------

class PlanningType(str, Enum):
    REACH = "Reach"
    COMM = "Comm"


class CommsSetup(BaseModel):
    brand_strength: int = Field(default=6, ge=0, le=10)
    parent_brand: int = Field(default=5, ge=0, le=10)
    competitor_clutter: int = Field(default=5, ge=0, le=10)
    new_creative: int = Field(default=5, ge=0, le=10)
    message_complexity: int = Field(default=5, ge=0, le=10)
    kpi_ids: List[str] = Field(default_factory=list)


class Brief(BaseModel):
    id: Optional[str] = None
    survey_id: Optional[str] = None
    client_id: Optional[str] = None
    project_name: Optional[str] = None
    start_date: date = date(2026, 2, 16)
    weeks: int = 4
    end_date: date = date(2026, 3, 15)
    target_ids: List[str] = Field(default_factory=lambda: ["all_adults"])
    planning_type: PlanningType = PlanningType.REACH
    comms: CommsSetup = Field(default_factory=CommsSetup)
    channel_ids: List[str] = Field(default_factory=list)

    @field_validator("weeks")
    @classmethod
    def _weeks_positive(cls, v: int) -> int:
        if v <= 0 or v > 52:
            raise ValueError("weeks must be between 1 and 52")
        return v


# ---------- Plans ----------

class WeekAllocation(BaseModel):
    week: int
    start_date: date
    budget_twd: float = 0.0
    impressions: int = 0
    grp: float = 0.0
    share_pct: float = 0.0


class ChannelAllocation(BaseModel):
    channel_id: str
    weeks: List[WeekAllocation] = Field(default_factory=list)
    total_budget_twd: float = 0.0
    total_impressions: int = 0
    total_grp: float = 0.0
    net_reach_pct: float = 0.0
    frequency: float = 0.0


class PerformanceSummary(BaseModel):
    total_budget_twd: float = 0.0
    total_impressions: int = 0
    total_grp: float = 0.0
    net_reach_pct: float = 0.0
    frequency: float = 0.0
    attitude_measures_pct: float = 0.0
    brand_consideration_pct: float = 0.0
    brand_knowledge_scores_pct: float = 0.0


class ManualPlanInput(BaseModel):
    """User-specified weekly budget per channel."""
    weekly_budgets: Dict[str, List[float]] = Field(default_factory=dict)


class AutoChannelConstraint(BaseModel):
    min_budget: Optional[float] = None
    max_budget: Optional[float] = None
    min_reach_pct: Optional[float] = None
    max_frequency: Optional[float] = None


class AutomaticPlanInput(BaseModel):
    criterion_id: str = "net_reach"
    strategy_id: str = "global_plan"
    total_budget_twd: float = 0.0
    mandatory_channel_ids: List[str] = Field(default_factory=list)
    optional_channel_ids: List[str] = Field(default_factory=list)
    constraints: Dict[str, AutoChannelConstraint] = Field(default_factory=dict)


class PlanKind(str, Enum):
    MANUAL = "Manual"
    AUTOMATIC = "Automatic"


class Plan(BaseModel):
    id: Optional[str] = None
    brief_id: str
    name: str
    kind: PlanKind
    created_at: date = Field(default_factory=date.today)
    allocations: List[ChannelAllocation] = Field(default_factory=list)
    summary: PerformanceSummary = Field(default_factory=PerformanceSummary)
    meta: Dict[str, str] = Field(default_factory=dict)


# ---------- Agent session ----------

class SessionMode(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class StepKey(str, Enum):
    SURVEY_CLIENT = "survey_client"
    PROJECT_DATES = "project_dates"
    TARGET_AUDIENCE = "target_audience"
    PLANNING_TYPE = "planning_type"
    COMMS_SETUP = "comms_setup"
    CHANNELS = "channels"
    CALIBRATION = "calibration"        # manual only
    MANUAL_PLAN = "manual_plan"        # manual only
    CRITERION = "criterion"            # auto only
    BUDGET_CHANNELS = "budget_channels"  # auto only
    MIN_MAX = "min_max"                # auto only
    OPTIMIZE = "optimize"              # auto only
    REVIEW = "review"


class AgentSession(BaseModel):
    id: str
    mode: SessionMode
    step: StepKey = StepKey.SURVEY_CLIENT
    brief: Brief = Field(default_factory=Brief)
    manual_input: ManualPlanInput = Field(default_factory=ManualPlanInput)
    automatic_input: AutomaticPlanInput = Field(default_factory=AutomaticPlanInput)
    plan_id: Optional[str] = None
    history: List[Dict] = Field(default_factory=list)


# ---------- API envelopes ----------

# ---------- v4 entities: User / Project / Conversation ----------

class User(BaseModel):
    id: str
    name: str
    is_admin: bool = False
    created_at: float = 0.0


class Project(BaseModel):
    id: str
    name: str
    owner_id: str
    created_at: float = 0.0
    archived: bool = False
    session_count: int = 0
    plan_count: int = 0


class ConversationTurn(BaseModel):
    """One step of a session's history (option C — full brief snapshot).

    ``brief_snapshot`` holds the entire brief state after the step. Diffing
    two turns reconstructs what the user changed; replaying the list
    reproduces the full conversation.
    """
    id: str
    session_id: str
    turn_index: int
    step: str
    payload: Dict
    prompt: str
    brief_snapshot: Dict
    ts: float


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class CreateUserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    is_admin: bool = False


class CreateSessionRequest(BaseModel):
    mode: SessionMode = SessionMode.MANUAL
    project_id: Optional[str] = None


class SessionStepResponse(BaseModel):
    session: AgentSession
    prompt: str
    available_options: Dict = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    completed: bool = False
    plan: Optional[Plan] = None


class StepPayload(BaseModel):
    """Generic payload for advancing the agent — only the subset relevant to the
    current step will be read."""
    survey_id: Optional[str] = None
    client_id: Optional[str] = None
    project_name: Optional[str] = None
    start_date: Optional[date] = None
    weeks: Optional[int] = None
    target_ids: Optional[List[str]] = None
    planning_type: Optional[PlanningType] = None
    comms: Optional[CommsSetup] = None
    channel_ids: Optional[List[str]] = None
    weekly_budgets: Optional[Dict[str, List[float]]] = None
    criterion_id: Optional[str] = None
    strategy_id: Optional[str] = None
    total_budget_twd: Optional[float] = None
    mandatory_channel_ids: Optional[List[str]] = None
    optional_channel_ids: Optional[List[str]] = None
    constraints: Optional[Dict[str, AutoChannelConstraint]] = None
    action: Optional[str] = None  # e.g. "skip" / "back" / "apply_demo"
