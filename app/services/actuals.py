"""v6 · FR-27..29 — actuals validation + report math.

Kept thin — the storage module handles persistence, this module handles
user-facing validation (scope/week invariants) and the planned-vs-actual
diff used by the report endpoint.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..schemas import (
    ActualsScope,
    ChannelActual,
    Plan,
    PlanActualsRecord,
)


class ActualsError(ValueError):
    """Raised for user-facing actuals validation failures."""


# ---------- Validation ----------

def validate_record(record: PlanActualsRecord, *, plan_weeks: int) -> None:
    """Enforce PRD v6 · FR-27 invariants.

    Raises :class:`ActualsError` on violations. Caller maps to 400/422.
    """
    scope = record.scope.value if isinstance(record.scope, ActualsScope) else str(record.scope)
    if scope == "WEEKLY":
        if record.period_week is None:
            raise ActualsError("WEEKLY records must specify period_week (1..weeks).")
        if record.period_week < 1 or record.period_week > plan_weeks:
            raise ActualsError(
                f"period_week must be between 1 and {plan_weeks}; "
                f"got {record.period_week}."
            )
    elif scope == "FINAL":
        if record.period_week is not None:
            raise ActualsError("FINAL records must not carry period_week.")
    else:
        raise ActualsError(f"Unknown scope '{scope}'. Expected WEEKLY or FINAL.")


# ---------- Report math ----------

def _sum_weeklies(records: List[PlanActualsRecord]) -> Dict[str, ChannelActual]:
    """Aggregate every WEEKLY record into a per-channel pseudo-final."""
    agg: Dict[str, ChannelActual] = {}
    counts: Dict[str, int] = {}
    for r in records:
        scope = r.scope.value if isinstance(r.scope, ActualsScope) else str(r.scope)
        if scope != "WEEKLY":
            continue
        for ch, ca in r.per_channel.items():
            cur = agg.setdefault(ch, ChannelActual())
            cur.spend_twd += ca.spend_twd
            cur.impressions += ca.impressions
            # Ratios (CPM/reach/freq/penetration) averaged across weeks;
            # we'd rather show a misleading-ish blended view than 0.
            cur.cpm_twd += ca.cpm_twd
            cur.net_reach_pct += ca.net_reach_pct
            cur.frequency += ca.frequency
            cur.penetration_pct += ca.penetration_pct
            cur.buying_audience_000 = max(cur.buying_audience_000,
                                          ca.buying_audience_000)
            counts[ch] = counts.get(ch, 0) + 1
    for ch, n in counts.items():
        if n <= 1:
            continue
        cur = agg[ch]
        cur.cpm_twd /= n
        cur.net_reach_pct /= n
        cur.frequency /= n
        cur.penetration_pct /= n
    return agg


def choose_actuals_source(records: List[PlanActualsRecord]):
    """FR-29 — FINAL wins; otherwise sum of WEEKLY.
    Returns ``(source_label, per_channel_dict)`` or ``("none", {})``."""
    final = next(
        (r for r in records
         if (r.scope.value if isinstance(r.scope, ActualsScope) else r.scope) == "FINAL"),
        None,
    )
    if final:
        return "final", final.per_channel
    weeklies = [r for r in records
                if (r.scope.value if isinstance(r.scope, ActualsScope) else r.scope) == "WEEKLY"]
    if weeklies:
        return "weekly", _sum_weeklies(weeklies)
    return "none", {}


def _bucket_variance(pct: float) -> str:
    """PRD §5 — ≤ ±10 green, ±10–25 amber, > ±25 red."""
    a = abs(pct)
    if a <= 10:
        return "green"
    if a <= 25:
        return "amber"
    return "red"


def _pct(actual: float, planned: float) -> float:
    if planned == 0:
        return 0.0 if actual == 0 else 100.0
    return (actual - planned) / planned * 100.0


def build_report(plan: Plan, records: List[PlanActualsRecord]) -> Dict:
    """Planned-vs-actual diff, per-channel + aggregate.

    Emits a sentinel ``status == "no_actuals"`` envelope when nothing has
    been recorded yet — lets the frontend render an empty-state without
    branching on HTTP status.
    """
    source, actual_per_ch = choose_actuals_source(records)

    planned_envelope = {
        "total_budget_twd": plan.summary.total_budget_twd,
        "total_impressions": plan.summary.total_impressions,
        "net_reach_pct": plan.summary.net_reach_pct,
        "frequency": plan.summary.frequency,
    }
    if source == "none":
        return {
            "status": "no_actuals",
            "plan_id": plan.id,
            "planned": planned_envelope,
            "per_channel": [],
            "aggregate": None,
            "source": "none",
        }

    # Per-channel diff.
    per_channel: List[Dict] = []
    total_actual_spend = 0.0
    total_actual_impr = 0
    for alloc in plan.allocations:
        ch = alloc.channel_id
        actual = actual_per_ch.get(ch)
        row = {
            "channel_id": ch,
            "planned_spend_twd": alloc.total_budget_twd,
            "planned_impressions": alloc.total_impressions,
            "planned_net_reach_pct": alloc.net_reach_pct,
            "planned_cpm_twd": (alloc.total_budget_twd / alloc.total_impressions * 1000)
                                if alloc.total_impressions else 0.0,
            "actual_spend_twd": actual.spend_twd if actual else 0.0,
            "actual_impressions": actual.impressions if actual else 0,
            "actual_net_reach_pct": actual.net_reach_pct if actual else 0.0,
            "actual_cpm_twd": actual.cpm_twd if actual else 0.0,
        }
        row["spend_variance_pct"] = _pct(row["actual_spend_twd"], row["planned_spend_twd"])
        row["impressions_variance_pct"] = _pct(row["actual_impressions"],
                                               row["planned_impressions"])
        row["cpm_variance_pct"] = _pct(row["actual_cpm_twd"], row["planned_cpm_twd"])
        row["net_reach_delta_pp"] = row["actual_net_reach_pct"] - row["planned_net_reach_pct"]
        row["spend_badge"] = _bucket_variance(row["spend_variance_pct"])
        per_channel.append(row)
        total_actual_spend += row["actual_spend_twd"]
        total_actual_impr += row["actual_impressions"]

    # Aggregate row. For net_reach_pct we take the actuals source's
    # headline where possible (sum-of-weekly gives a blended average,
    # FINAL gives the reported figure).
    first_channel = next(iter(actual_per_ch.values()), None)
    actual_reach = (first_channel.net_reach_pct if first_channel else 0.0)
    agg = {
        "actual_spend_twd": total_actual_spend,
        "actual_impressions": total_actual_impr,
        "spend_variance_pct": _pct(total_actual_spend, plan.summary.total_budget_twd),
        "impressions_variance_pct": _pct(total_actual_impr, plan.summary.total_impressions),
        "net_reach_delta_pp": actual_reach - plan.summary.net_reach_pct,
    }
    agg["spend_badge"] = _bucket_variance(agg["spend_variance_pct"])

    return {
        "status": "ok",
        "plan_id": plan.id,
        "planned": planned_envelope,
        "per_channel": per_channel,
        "aggregate": agg,
        "source": source,
    }


# ---------- Printable HTML ----------

def render_report_html(plan: Plan, report: Dict) -> str:
    """Minimal server-rendered report view. Deliberately dependency-free
    so a planner can Cmd-P without loading app.js or Chart.js."""
    from html import escape

    if report.get("status") == "no_actuals":
        return (
            "<!doctype html><html lang='zh-Hant'><meta charset='utf-8'>"
            f"<title>Plan vs Actual · {escape(plan.name)}</title>"
            "<body style='font-family: system-ui; padding: 2rem'>"
            f"<h1>Plan vs Actual · 成效回顧</h1>"
            f"<p><b>Plan:</b> {escape(plan.name)}</p>"
            "<p>尚未記錄 actuals。回到 CCS Planner 點 📊 記錄成效後再開啟此頁。</p>"
            "</body></html>"
        )

    def _row(r: Dict) -> str:
        badge = r.get("spend_badge", "")
        return (
            f"<tr>"
            f"<td>{escape(r['channel_id'])}</td>"
            f"<td>{r['planned_spend_twd']:,.0f}</td>"
            f"<td>{r['actual_spend_twd']:,.0f}</td>"
            f"<td class='badge-{badge}'>{r['spend_variance_pct']:+.1f}%</td>"
            f"<td>{r['planned_net_reach_pct']:.1f}%</td>"
            f"<td>{r['actual_net_reach_pct']:.1f}%</td>"
            f"<td>{r['net_reach_delta_pp']:+.1f}pp</td>"
            f"</tr>"
        )

    rows = "".join(_row(r) for r in report["per_channel"])
    agg = report["aggregate"] or {}
    agg_badge = agg.get("spend_badge", "")
    return (
        "<!doctype html><html lang='zh-Hant'><meta charset='utf-8'>"
        f"<title>Plan vs Actual · {escape(plan.name)}</title>"
        "<style>"
        "body{font-family:system-ui;padding:2rem}"
        "table{border-collapse:collapse;width:100%;margin-top:1rem}"
        "th,td{border:1px solid #ddd;padding:6px 10px;text-align:right;font-size:14px}"
        "th:first-child,td:first-child{text-align:left}"
        ".badge-green{background:#d4edda;color:#155724}"
        ".badge-amber{background:#fff3cd;color:#856404}"
        ".badge-red{background:#f8d7da;color:#721c24}"
        ".agg{font-weight:600;background:#f6f8fa}"
        "</style>"
        "<body>"
        "<h1>Plan vs Actual · 成效回顧</h1>"
        f"<p><b>Plan:</b> {escape(plan.name)} · <b>Source:</b> {escape(report['source'])}</p>"
        "<table>"
        "<thead><tr>"
        "<th>Channel</th><th>Planned Spend</th><th>Actual Spend</th>"
        "<th>Variance %</th><th>Planned Reach</th><th>Actual Reach</th>"
        "<th>Δ Reach</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "<tfoot><tr class='agg'>"
        f"<td>TOTAL</td>"
        f"<td>{report['planned']['total_budget_twd']:,.0f}</td>"
        f"<td>{agg.get('actual_spend_twd', 0):,.0f}</td>"
        f"<td class='badge-{agg_badge}'>{agg.get('spend_variance_pct', 0):+.1f}%</td>"
        f"<td>{report['planned']['net_reach_pct']:.1f}%</td>"
        f"<td>—</td>"
        f"<td>{agg.get('net_reach_delta_pp', 0):+.1f}pp</td>"
        "</tr></tfoot>"
        "</table>"
        "</body></html>"
    )
