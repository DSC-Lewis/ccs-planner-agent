"""Plan computation — manual allocation roll-up and automatic optimization.

The maths here is intentionally transparent so it matches what CCS Planner
shows on screen (penetration, attention, engagement, CPM) rather than a
black-box solver. A real deployment can swap this for the published
dentsu reach/frequency curves.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Dict, List, Optional

from ..schemas import (
    AutomaticPlanInput,
    Brief,
    ChannelAllocation,
    ManualPlanInput,
    PerformanceSummary,
    Plan,
    PlanKind,
    WeekAllocation,
)
from . import reference


# ---------- v6 · FR-30 — CPM resolution (profile-aware) ----------

def default_channel_cpm(channel_id: str) -> float:
    """Return the static CPM from ``channel_metrics.json``. Zero when the
    channel id is unknown so the caller can branch."""
    m = reference.channel_metrics().get(channel_id)
    return float(m.cpm_twd) if m else 0.0


def resolve_channel_cpm(*, channel_id: str, client_id: Optional[str],
                        target_id: Optional[str], owner_id: str) -> float:
    """Prefer a calibrated CPM from the learning loop; fall back to the
    static default when no profile exists for this triple.

    Lazy-imports :mod:`calibration` to avoid bootstrap cycles — the
    calibration module already depends on ``optimizer.default_channel_cpm``
    during test-driven rematerialisation.
    """
    if client_id and target_id:
        try:
            from . import calibration as _cal  # local import to dodge cycle
            prof = _cal.get_profile(
                client_id=client_id, target_id=target_id,
                channel_id=channel_id, metric="cpm_twd",
                owner_id=owner_id,
            )
        except Exception:
            prof = None
        # Guard against floating-point weight slipping just under 1 for
        # a freshly-observed row. n_raw is exact, and "any meaningful
        # observation" is the semantics we want.
        if prof and prof.n_raw >= 1 and prof.n_effective > 0:
            return float(prof.value_mean_weighted)
    return default_channel_cpm(channel_id)


# ---------- helpers ----------

def _impressions_from_budget(budget: float, cpm: float) -> int:
    if cpm <= 0 or budget <= 0:
        return 0
    return int(round(budget / cpm * 1000))


def _grp_from_impressions(impressions: int, universe: int) -> float:
    """GRP ~ impressions / universe * 100 (simplified)."""
    if universe <= 0:
        return 0.0
    return round(impressions / (universe * 1000) * 100, 2)


def _reach_curve(grp: float, penetration_pct: float) -> float:
    """A bounded reach curve: R = P * (1 - exp(-k * GRP)).

    * `penetration_pct` is the effective ceiling (channel's addressable audience).
    * `k` is tuned so ~300 GRP saturates ~95% of the ceiling.
    """
    if grp <= 0 or penetration_pct <= 0:
        return 0.0
    k = 0.01
    return round(penetration_pct * (1.0 - math.exp(-k * grp)), 2)


def _combine_net_reach(channel_reaches: List[float]) -> float:
    """Assume independence between channels (overestimates duplication but
    good enough for a demo)."""
    prob_missed = 1.0
    for r in channel_reaches:
        prob_missed *= max(0.0, 1.0 - r / 100.0)
    return round((1.0 - prob_missed) * 100.0, 2)


def _target_universe(target_ids: List[str]) -> int:
    """Sum of universe across chosen targets, in thousands."""
    by_id = {t.id: t for t in reference.targets()}
    return sum(by_id[t].universe_000 for t in target_ids if t in by_id) or 19002


# ---------- Manual ----------

def compute_manual_plan(
    brief: Brief,
    manual_input: ManualPlanInput,
) -> Plan:
    metrics = reference.channel_metrics()
    universe_000 = _target_universe(brief.target_ids)
    start = brief.start_date
    allocations: List[ChannelAllocation] = []

    for channel_id in brief.channel_ids:
        weekly_budgets = manual_input.weekly_budgets.get(channel_id, [0.0] * brief.weeks)
        weekly_budgets = (weekly_budgets + [0.0] * brief.weeks)[: brief.weeks]
        m = metrics.get(channel_id)
        if not m:
            continue

        week_rows: List[WeekAllocation] = []
        channel_total = 0.0
        channel_imp = 0
        for i, budget in enumerate(weekly_budgets):
            imp = _impressions_from_budget(budget, m.cpm_twd)
            grp = _grp_from_impressions(imp, universe_000)
            week_rows.append(
                WeekAllocation(
                    week=i + 1,
                    start_date=start + timedelta(days=7 * i),
                    budget_twd=round(budget, 2),
                    impressions=imp,
                    grp=grp,
                    share_pct=0.0,
                )
            )
            channel_total += budget
            channel_imp += imp

        if channel_total:
            for w in week_rows:
                w.share_pct = round(w.budget_twd / channel_total * 100, 2)

        channel_grp = _grp_from_impressions(channel_imp, universe_000)
        channel_reach = _reach_curve(channel_grp, m.penetration_pct)
        freq = round(channel_grp / channel_reach, 2) if channel_reach else 0.0

        allocations.append(
            ChannelAllocation(
                channel_id=channel_id,
                weeks=week_rows,
                total_budget_twd=round(channel_total, 2),
                total_impressions=channel_imp,
                total_grp=channel_grp,
                net_reach_pct=channel_reach,
                frequency=freq,
            )
        )

    summary = _rollup_summary(allocations, brief)
    return Plan(
        brief_id=brief.id or "",
        name="Plan 1",
        kind=PlanKind.MANUAL,
        allocations=allocations,
        summary=summary,
    )


# ---------- Automatic ----------

def _channel_weight(channel_id: str) -> float:
    """Score = penetration * attention / CPM (higher = more efficient)."""
    m = reference.channel_metrics().get(channel_id)
    if not m or m.cpm_twd <= 0:
        return 0.0
    return (m.penetration_pct * m.attention_pct) / m.cpm_twd


def _apply_constraints(
    allocation: Dict[str, float],
    automatic_input: AutomaticPlanInput,
) -> Dict[str, float]:
    adjusted = dict(allocation)
    for ch, budget in list(adjusted.items()):
        c = automatic_input.constraints.get(ch)
        if not c:
            continue
        if c.min_budget is not None:
            budget = max(budget, c.min_budget)
        if c.max_budget is not None:
            budget = min(budget, c.max_budget)
        adjusted[ch] = budget
    return adjusted


def compute_automatic_plan(
    brief: Brief,
    automatic_input: AutomaticPlanInput,
) -> Plan:
    pool = list(dict.fromkeys(
        automatic_input.mandatory_channel_ids + automatic_input.optional_channel_ids
    ))
    if not pool and brief.channel_ids:
        # Fall back to all channels in the brief, treat as mandatory
        pool = list(brief.channel_ids)
        automatic_input.mandatory_channel_ids = pool

    budget = max(automatic_input.total_budget_twd, 0.0)
    if not pool or budget <= 0:
        return Plan(
            brief_id=brief.id or "",
            name="Plan 2",
            kind=PlanKind.AUTOMATIC,
            allocations=[],
            summary=PerformanceSummary(),
        )

    weights = {ch: _channel_weight(ch) for ch in pool}
    total_w = sum(weights.values()) or 1.0
    alloc = {ch: budget * (w / total_w) for ch, w in weights.items()}
    alloc = _apply_constraints(alloc, automatic_input)

    # Re-normalize if constraints pushed the total above/below the budget.
    total_after = sum(alloc.values())
    if total_after > 0 and abs(total_after - budget) / budget > 0.01:
        scale = budget / total_after
        alloc = {ch: round(v * scale, 2) for ch, v in alloc.items()}

    # Distribute each channel's budget evenly across weeks
    universe_000 = _target_universe(brief.target_ids)
    start = brief.start_date
    metrics = reference.channel_metrics()
    allocations: List[ChannelAllocation] = []

    for ch in pool:
        ch_budget = round(alloc.get(ch, 0.0), 2)
        m = metrics.get(ch)
        if not m:
            continue
        per_week = ch_budget / brief.weeks if brief.weeks else 0.0
        week_rows: List[WeekAllocation] = []
        imp_total = 0
        for i in range(brief.weeks):
            imp = _impressions_from_budget(per_week, m.cpm_twd)
            grp = _grp_from_impressions(imp, universe_000)
            week_rows.append(
                WeekAllocation(
                    week=i + 1,
                    start_date=start + timedelta(days=7 * i),
                    budget_twd=round(per_week, 2),
                    impressions=imp,
                    grp=grp,
                    share_pct=round(100.0 / brief.weeks, 2) if brief.weeks else 0.0,
                )
            )
            imp_total += imp

        ch_grp = _grp_from_impressions(imp_total, universe_000)
        ch_reach = _reach_curve(ch_grp, m.penetration_pct)
        freq = round(ch_grp / ch_reach, 2) if ch_reach else 0.0
        allocations.append(
            ChannelAllocation(
                channel_id=ch,
                weeks=week_rows,
                total_budget_twd=ch_budget,
                total_impressions=imp_total,
                total_grp=ch_grp,
                net_reach_pct=ch_reach,
                frequency=freq,
            )
        )

    summary = _rollup_summary(allocations, brief)
    return Plan(
        brief_id=brief.id or "",
        name="Plan 2",
        kind=PlanKind.AUTOMATIC,
        allocations=allocations,
        summary=summary,
        meta={
            "criterion_id": automatic_input.criterion_id,
            "strategy_id": automatic_input.strategy_id,
        },
    )


# ---------- Summary ----------

def _rollup_summary(
    allocations: List[ChannelAllocation],
    brief: Brief,
) -> PerformanceSummary:
    total_budget = sum(a.total_budget_twd for a in allocations)
    total_imp = sum(a.total_impressions for a in allocations)
    total_grp = round(sum(a.total_grp for a in allocations), 2)
    net_reach = _combine_net_reach([a.net_reach_pct for a in allocations])
    freq = round(total_grp / net_reach, 2) if net_reach else 0.0

    # Brand effect proxies: net_reach-weighted by channel attention/engagement,
    # only meaningful when Comm planning is chosen.
    attitude = 0.0
    consideration = 0.0
    knowledge = 0.0
    if allocations and brief.planning_type.value == "Comm":
        metrics = reference.channel_metrics()
        for a in allocations:
            m = metrics.get(a.channel_id)
            if not m:
                continue
            attitude += a.net_reach_pct * m.attention_pct / 1000
            consideration += a.net_reach_pct * m.engagement_pct / 1000
            knowledge += a.net_reach_pct * m.penetration_pct / 2000

    return PerformanceSummary(
        total_budget_twd=round(total_budget, 2),
        total_impressions=total_imp,
        total_grp=total_grp,
        net_reach_pct=net_reach,
        frequency=freq,
        attitude_measures_pct=round(attitude, 2),
        brand_consideration_pct=round(consideration, 2),
        brand_knowledge_scores_pct=round(knowledge, 2),
    )


# ---------- Step-simulation (fills the "missing feature" called out in the video) ----------

def budget_step_curve(
    brief: Brief,
    automatic_input: AutomaticPlanInput,
    step_count: int = 10,
) -> List[Dict]:
    """Simulate how net reach responds to total-budget steps.

    The video narrator mentioned this is not yet available in CCS Planner —
    here we offer it by running ``compute_automatic_plan`` over a budget sweep.
    """
    base_budget = automatic_input.total_budget_twd
    if base_budget <= 0:
        return []
    out: List[Dict] = []
    for i in range(1, step_count + 1):
        pct = i / step_count
        inp = automatic_input.model_copy(deep=True)
        inp.total_budget_twd = round(base_budget * pct, 2)
        plan = compute_automatic_plan(brief, inp)
        out.append({
            "step": i,
            "budget_pct": round(pct * 100, 1),
            "budget_twd": inp.total_budget_twd,
            "net_reach_pct": plan.summary.net_reach_pct,
            "frequency": plan.summary.frequency,
            "total_impressions": plan.summary.total_impressions,
        })
    return out


# ---------- Suggestion helpers ----------

# ---------- Frequency distribution (FR-10) ----------

def frequency_distribution(plan: Plan, thresholds: int = 10) -> List[Dict]:
    """Reach at 1+, 2+, ..., N+ exposures.

    Uses a geometric-decay approximation: for average frequency ``f``, the
    share of the reached audience that saw ≥ n ads decays as ``(1 - 1/f)``
    per extra exposure. It's the simplest model that (a) equals full net
    reach at 1+, (b) stays monotonic, and (c) collapses sensibly to zero
    when reach or frequency are zero.

    The real CCS Planner uses a published beta-binomial curve per channel;
    swapping that in is a drop-in replacement when the curves arrive.
    """
    reach = max(0.0, min(100.0, plan.summary.net_reach_pct))
    freq = max(0.0, plan.summary.frequency)
    out: List[Dict] = []
    if reach <= 0 or freq <= 0:
        return [{"threshold": n, "reach_pct": 0.0} for n in range(1, thresholds + 1)]

    decay = max(0.0, min(0.99, 1.0 - 1.0 / max(freq, 1.01)))
    cur = reach
    for n in range(1, thresholds + 1):
        out.append({"threshold": n, "reach_pct": round(max(0.0, min(100.0, cur)), 2)})
        cur *= decay
    return out


# ---------- Duplication & exclusivity (FR-11) ----------

def duplication_matrix(plan: Plan, overlap_factor: float = 0.2) -> Dict[str, Dict]:
    """Pairwise duplication and remaining exclusivity per channel.

    ``duplication(A,B) = min(rA,rB) / max(rA,rB) * overlap_factor * min(rA,rB)``
    (so duplicated reach is in the same 0..100% units as the channel reaches).
    Exclusivity is the portion of the channel's reach not duplicated with
    anyone else in the plan, clamped to non-negative.

    Numbers are heuristic — matched against video demo patterns rather than
    derived from survey overlap data. Replace with real overlap tables when
    available.
    """
    channels = plan.allocations
    out: Dict[str, Dict] = {}
    for i, a in enumerate(channels):
        pair_dupes: Dict[str, float] = {}
        total_dupe = 0.0
        for j, b in enumerate(channels):
            if i == j:
                continue
            smaller = min(a.net_reach_pct, b.net_reach_pct)
            larger = max(a.net_reach_pct, b.net_reach_pct)
            if larger <= 0:
                dupe = 0.0
            else:
                dupe = smaller / larger * overlap_factor * smaller
            pair_dupes[b.channel_id] = round(max(0.0, min(100.0, dupe)), 2)
            total_dupe += dupe
        exclusivity = max(0.0, a.net_reach_pct - total_dupe)
        out[a.channel_id] = {
            "duplication_pct": round(min(100.0, total_dupe), 2),
            "exclusivity_pct": round(min(100.0, exclusivity), 2),
            "pairwise": pair_dupes,
        }
    return out


# ---------- Weekly roll-up for chart layer ----------

def weekly_grp(plan: Plan) -> List[Dict]:
    """Roll up GRP across channels into a week-by-week series."""
    totals: Dict[int, float] = {}
    for alloc in plan.allocations:
        for w in alloc.weeks:
            totals[w.week] = totals.get(w.week, 0.0) + w.grp
    return [
        {"week": week, "grp": round(grp, 2)}
        for week, grp in sorted(totals.items())
    ]


def suggest_weekly_budget(channel_id: str, weeks: int) -> List[float]:
    """Demo defaults aligned with the screenshot in the training video."""
    demo = {
        "tv_advertising": 2500,
        "youtube_video_ads": 125000,
        "meta_video_ads": 100000,
    }
    if channel_id in demo:
        base = demo[channel_id]
    else:
        m = reference.channel_metrics().get(channel_id)
        base = int((m.penetration_pct if m else 50) * 200)
    return [float(base)] * weeks
