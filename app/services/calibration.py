"""v6 · FR-30 / FR-30b / FR-34 — calibration learning loop.

Architecture
------------
* ``calibration_observations`` — append-only raw observations keyed by
  ``(owner × client × target × channel × metric)``. Every PUT of
  plan actuals appends rows here.
* ``calibration_profiles`` — materialised view of the weighted mean,
  sample size, confidence score, etc. Re-computed whenever
  observations OR settings change.
* ``calibration_settings`` — per-owner overrides for decay half-life
  and confidence thresholds, scoped at global / client / channel
  granularity.

Design principles
-----------------
* ``decay_weight`` and ``compute_confidence`` live here and are pure
  functions so the thresholds / formula can be swapped with a unit-test
  change rather than a UI one (NFR-7.8).
* Storage module stays dumb — schema + SQL only. Domain logic
  (decay maths, scope resolution) lives in this module.
* Observation writes on actuals PUT happen via :func:`record_from_actuals`
  called by the route handler, NOT as a trigger — keeps the learning
  loop auditable and easy to disable in tests.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional

from ..schemas import ActualsScope, CalibrationObservation, CalibrationProfile
from . import storage


_DEFAULT_HALF_LIFE_DAYS = 180.0
_DEFAULT_THRESHOLDS = {"high": 70, "mid": 40}

# Metrics we persist per channel×actuals row. Mirrors the seven fields
# the PRD wants planners to be able to override.
_TRACKED_METRICS = (
    "cpm_twd", "penetration_pct", "net_reach_pct", "frequency",
)


# ---------- Pure maths ----------

def decay_weight(age_days: float, half_life_days: float) -> float:
    """Exponential half-life decay: w = 2 ** (-age/half_life)."""
    if half_life_days <= 0:
        # Guard against divide-by-zero — infinite half-life → weight 1.
        return 1.0
    return 2.0 ** (-age_days / half_life_days)


def compute_confidence(n_effective: float, cv: float) -> int:
    """0..100 integer score. See PRD §FR-30b."""
    if n_effective <= 0:
        return 0
    sample_factor = 1.0 - math.exp(-n_effective / 5.0)
    consistency_factor = max(0.0, 1.0 - min(max(cv, 0.0), 1.0))
    score = 100.0 * (0.6 * sample_factor + 0.4 * consistency_factor)
    return max(0, min(100, int(round(score))))


# ---------- Observation CRUD ----------

def record_observation(
    *,
    client_id: str,
    target_id: str,
    channel_id: str,
    metric: str,
    value: float,
    owner_id: str,
    observed_at: Optional[datetime] = None,
    source_plan_id: Optional[str] = None,
    source_actuals_id: Optional[str] = None,
    weight_override: Optional[float] = None,
) -> CalibrationObservation:
    """Append one observation and rematerialise the profile row."""
    oid = f"cobs_{uuid.uuid4().hex[:10]}"
    ts = observed_at.timestamp() if observed_at else time.time()
    c = storage._conn()
    c.execute(
        "INSERT INTO calibration_observations(id, owner_id, client_id, "
        "target_id, channel_id, metric, value, observed_at, source_plan_id, "
        "source_actuals_id, weight_override) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, owner_id, client_id, target_id, channel_id, metric,
         float(value), ts, source_plan_id, source_actuals_id,
         weight_override),
    )
    obs = CalibrationObservation(
        id=oid, owner_id=owner_id, client_id=client_id, target_id=target_id,
        channel_id=channel_id, metric=metric, value=float(value),
        observed_at=ts, source_plan_id=source_plan_id,
        source_actuals_id=source_actuals_id, weight_override=weight_override,
    )
    _rematerialise_profile(owner_id, client_id, target_id, channel_id, metric)
    return obs


def list_observations(
    *,
    client_id: str,
    target_id: str,
    channel_id: str,
    owner_id: str,
    metric: Optional[str] = None,
) -> List[CalibrationObservation]:
    sql = (
        "SELECT * FROM calibration_observations "
        "WHERE owner_id=? AND client_id=? AND target_id=? AND channel_id=?"
    )
    args: list = [owner_id, client_id, target_id, channel_id]
    if metric is not None:
        sql += " AND metric=?"
        args.append(metric)
    sql += " ORDER BY observed_at ASC"
    rows = storage._conn().execute(sql, tuple(args)).fetchall()
    return [CalibrationObservation(
        id=r["id"], owner_id=r["owner_id"], client_id=r["client_id"],
        target_id=r["target_id"], channel_id=r["channel_id"],
        metric=r["metric"], value=r["value"], observed_at=r["observed_at"],
        source_plan_id=r["source_plan_id"],
        source_actuals_id=r["source_actuals_id"],
        weight_override=r["weight_override"],
    ) for r in rows]


def set_observation_weight(*, owner_id: str, observation_id: str,
                            weight_override: Optional[float]) -> bool:
    cur = storage._conn().execute(
        "UPDATE calibration_observations SET weight_override=? "
        "WHERE id=? AND owner_id=?",
        (weight_override, observation_id, owner_id),
    )
    if cur.rowcount == 0:
        return False
    # Rematerialise the affected profile.
    row = storage._conn().execute(
        "SELECT client_id, target_id, channel_id, metric "
        "FROM calibration_observations WHERE id=?",
        (observation_id,),
    ).fetchone()
    if row:
        _rematerialise_profile(owner_id, row["client_id"], row["target_id"],
                               row["channel_id"], row["metric"])
    return True


# ---------- Settings ----------

def get_settings(owner_id: str) -> dict:
    rows = storage._conn().execute(
        "SELECT * FROM calibration_settings WHERE owner_id=?",
        (owner_id,),
    ).fetchall()
    global_hl = _DEFAULT_HALF_LIFE_DAYS
    global_thresholds = dict(_DEFAULT_THRESHOLDS)
    per_client: List[dict] = []
    per_channel: List[dict] = []
    for r in rows:
        if r["scope"] == "global":
            if r["half_life_days"] is not None:
                global_hl = r["half_life_days"]
            if r["thresholds"]:
                try:
                    global_thresholds.update(json.loads(r["thresholds"]))
                except json.JSONDecodeError:
                    pass
        elif r["scope"] == "client":
            per_client.append({
                "client_id": r["client_id"],
                "target_id": r["target_id"],
                "half_life_days": r["half_life_days"],
            })
        elif r["scope"] == "channel":
            per_channel.append({
                "client_id": r["client_id"],
                "target_id": r["target_id"],
                "channel_id": r["channel_id"],
                "half_life_days": r["half_life_days"],
            })
    return {
        "global": {"half_life_days": global_hl, "thresholds": global_thresholds},
        "per_client": per_client,
        "per_channel": per_channel,
    }


def _upsert_setting(c: sqlite3.Connection, *, owner_id: str, scope: str,
                    client_id: Optional[str], target_id: Optional[str],
                    channel_id: Optional[str], half_life_days: Optional[float],
                    thresholds: Optional[dict]) -> None:
    """Upsert a calibration_settings row. Uniqueness is enforced by the
    partial-expression UNIQUE INDEX over the COALESCE-normalised key
    tuple (SQLite doesn't accept expressions in PRIMARY KEY)."""
    existing = c.execute(
        "SELECT id FROM calibration_settings "
        "WHERE owner_id=? AND scope=? "
        "AND COALESCE(client_id,'')=COALESCE(?, '') "
        "AND COALESCE(target_id,'')=COALESCE(?, '') "
        "AND COALESCE(channel_id,'')=COALESCE(?, '')",
        (owner_id, scope, client_id, target_id, channel_id),
    ).fetchone()
    thresholds_json = json.dumps(thresholds) if thresholds is not None else None
    if existing:
        sets, args = [], []
        if half_life_days is not None:
            sets.append("half_life_days=?"); args.append(half_life_days)
        if thresholds_json is not None:
            sets.append("thresholds=?"); args.append(thresholds_json)
        if sets:
            args.append(existing["id"])
            c.execute(
                f"UPDATE calibration_settings SET {', '.join(sets)} WHERE id=?",
                tuple(args),
            )
    else:
        c.execute(
            "INSERT INTO calibration_settings(owner_id, scope, client_id, "
            "target_id, channel_id, half_life_days, thresholds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (owner_id, scope, client_id, target_id, channel_id,
             half_life_days, thresholds_json),
        )


def set_half_life(*, owner_id: str,
                  client_id: Optional[str] = None,
                  target_id: Optional[str] = None,
                  channel_id: Optional[str] = None,
                  half_life_days: float) -> None:
    """Upsert a half-life override. Scope is inferred from which ids
    are non-None."""
    if channel_id is not None:
        scope = "channel"
    elif client_id is not None:
        scope = "client"
    else:
        scope = "global"
    c = storage._conn()
    _upsert_setting(c, owner_id=owner_id, scope=scope,
                    client_id=client_id, target_id=target_id,
                    channel_id=channel_id, half_life_days=half_life_days,
                    thresholds=None)
    _rematerialise_all_for_owner(owner_id)


def set_confidence_thresholds(*, owner_id: str,
                               high: int, mid: int) -> None:
    c = storage._conn()
    _upsert_setting(c, owner_id=owner_id, scope="global",
                    client_id=None, target_id=None, channel_id=None,
                    half_life_days=None,
                    thresholds={"high": int(high), "mid": int(mid)})
    _rematerialise_all_for_owner(owner_id)


def reset_scope(*, owner_id: str, scope: str,
                client_id: Optional[str] = None,
                target_id: Optional[str] = None,
                channel_id: Optional[str] = None) -> bool:
    cur = storage._conn().execute(
        "DELETE FROM calibration_settings WHERE owner_id=? AND scope=? "
        "AND COALESCE(client_id,'')=COALESCE(?, '') "
        "AND COALESCE(target_id,'')=COALESCE(?, '') "
        "AND COALESCE(channel_id,'')=COALESCE(?, '')",
        (owner_id, scope, client_id, target_id, channel_id),
    )
    _rematerialise_all_for_owner(owner_id)
    return cur.rowcount > 0


def effective_half_life(*, owner_id: str, client_id: str, target_id: str,
                        channel_id: str) -> float:
    settings = get_settings(owner_id)
    for row in settings["per_channel"]:
        if (row["client_id"] == client_id
                and row["target_id"] == target_id
                and row["channel_id"] == channel_id
                and row.get("half_life_days") is not None):
            return float(row["half_life_days"])
    for row in settings["per_client"]:
        if row["client_id"] == client_id and row.get("half_life_days") is not None:
            return float(row["half_life_days"])
    return float(settings["global"]["half_life_days"])


def confidence_bucket(score: int, *, owner_id: Optional[str] = None) -> str:
    if owner_id is not None:
        thresholds = get_settings(owner_id)["global"]["thresholds"]
    else:
        thresholds = _DEFAULT_THRESHOLDS
    hi = int(thresholds.get("high", 70))
    mid = int(thresholds.get("mid", 40))
    if score >= hi:
        return "high"
    if score >= mid:
        return "mid"
    return "low"


# ---------- Profile materialisation ----------

def _profile_from_row(row) -> CalibrationProfile:
    keys = row.keys() if hasattr(row, "keys") else []
    return CalibrationProfile(
        client_id=row["client_id"], target_id=row["target_id"],
        channel_id=row["channel_id"], metric=row["metric"],
        value_mean_weighted=row["value_mean_weighted"],
        value_stdev=row["value_stdev"],
        n_raw=row["n_raw"], n_effective=row["n_effective"],
        confidence_score=row["confidence_score"],
        last_updated=row["last_updated"],
        sample_factor=row["sample_factor"] if "sample_factor" in keys else 0.0,
        consistency_factor=row["consistency_factor"] if "consistency_factor" in keys else 0.0,
        cv=row["cv"] if "cv" in keys else 0.0,
    )


def get_profile(*, client_id: str, target_id: str, channel_id: str,
                metric: str, owner_id: str) -> Optional[CalibrationProfile]:
    row = storage._conn().execute(
        "SELECT * FROM calibration_profiles WHERE owner_id=? AND client_id=? "
        "AND target_id=? AND channel_id=? AND metric=?",
        (owner_id, client_id, target_id, channel_id, metric),
    ).fetchone()
    if not row:
        return None
    return _profile_from_row(row)


def list_profiles(owner_id: str) -> List[CalibrationProfile]:
    rows = storage._conn().execute(
        "SELECT * FROM calibration_profiles WHERE owner_id=?",
        (owner_id,),
    ).fetchall()
    return [_profile_from_row(r) for r in rows]


def _rematerialise_profile(owner_id: str, client_id: str, target_id: str,
                           channel_id: str, metric: str) -> None:
    obs = list_observations(client_id=client_id, target_id=target_id,
                            channel_id=channel_id, metric=metric,
                            owner_id=owner_id)
    now = time.time()
    if not obs:
        storage._conn().execute(
            "DELETE FROM calibration_profiles WHERE owner_id=? AND client_id=? "
            "AND target_id=? AND channel_id=? AND metric=?",
            (owner_id, client_id, target_id, channel_id, metric),
        )
        return

    half_life = effective_half_life(owner_id=owner_id, client_id=client_id,
                                    target_id=target_id, channel_id=channel_id)

    weights: List[float] = []
    values: List[float] = []
    for o in obs:
        if o.weight_override is not None:
            w = max(0.0, min(1.0, float(o.weight_override)))
        else:
            age_days = max(0.0, (now - o.observed_at) / 86400.0)
            w = decay_weight(age_days, half_life)
        weights.append(w)
        values.append(o.value)

    total_w = sum(weights)
    if total_w <= 0:
        storage._conn().execute(
            "DELETE FROM calibration_profiles WHERE owner_id=? AND client_id=? "
            "AND target_id=? AND channel_id=? AND metric=?",
            (owner_id, client_id, target_id, channel_id, metric),
        )
        return
    mean = sum(w * v for w, v in zip(weights, values)) / total_w
    if total_w > 1e-12:
        variance = sum(w * (v - mean) ** 2 for w, v in zip(weights, values)) / total_w
        stdev = math.sqrt(max(0.0, variance))
    else:
        stdev = 0.0
    cv = (stdev / mean) if mean else 0.0
    confidence = compute_confidence(total_w, cv)
    # v6 · FR-30b — persist the formula breakdown so the frontend tooltip
    # doesn't have to redo the maths.
    sample_factor = 1.0 - math.exp(-total_w / 5.0) if total_w > 0 else 0.0
    consistency_factor = max(0.0, 1.0 - min(max(cv, 0.0), 1.0))

    storage._conn().execute(
        "INSERT INTO calibration_profiles(owner_id, client_id, target_id, "
        "channel_id, metric, value_mean_weighted, value_stdev, n_raw, "
        "n_effective, confidence_score, last_updated, "
        "sample_factor, consistency_factor, cv) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(owner_id, client_id, target_id, channel_id, metric) "
        "DO UPDATE SET value_mean_weighted=excluded.value_mean_weighted, "
        "value_stdev=excluded.value_stdev, n_raw=excluded.n_raw, "
        "n_effective=excluded.n_effective, "
        "confidence_score=excluded.confidence_score, "
        "last_updated=excluded.last_updated, "
        "sample_factor=excluded.sample_factor, "
        "consistency_factor=excluded.consistency_factor, "
        "cv=excluded.cv",
        (owner_id, client_id, target_id, channel_id, metric, mean, stdev,
         len(obs), total_w, confidence, now,
         sample_factor, consistency_factor, cv),
    )


def _rematerialise_all_for_owner(owner_id: str) -> None:
    rows = storage._conn().execute(
        "SELECT DISTINCT client_id, target_id, channel_id, metric "
        "FROM calibration_observations WHERE owner_id=?",
        (owner_id,),
    ).fetchall()
    for r in rows:
        _rematerialise_profile(owner_id, r["client_id"], r["target_id"],
                               r["channel_id"], r["metric"])


# ---------- Actuals → observations helper ----------

def _observed_at_for_record(plan_brief, actuals_record) -> datetime:
    """Pick the semantically-correct ``observed_at`` for calibration decay.

    * ``FINAL`` — the event represents end-of-campaign performance, so use
      ``brief.end_date`` (23:59:59 UTC of that date is overkill; midnight
      is fine — decay is measured in days).
    * ``WEEKLY`` — the event represents week ``period_week`` of the plan;
      use the END of that week (``start_date + 7 * (k-1) + 6 days``).
    * Anything else (or missing dates) falls back to ``recorded_at`` to
      preserve pre-v6 behaviour.
    """
    scope = actuals_record.scope
    scope_value = scope.value if isinstance(scope, ActualsScope) else str(scope)

    def _date_to_dt(d) -> Optional[datetime]:
        if d is None:
            return None
        return datetime.combine(d, dtime.min, tzinfo=timezone.utc)

    if scope_value == "FINAL":
        dt = _date_to_dt(getattr(plan_brief, "end_date", None))
        if dt is not None:
            return dt
    elif scope_value == "WEEKLY":
        start = getattr(plan_brief, "start_date", None)
        wk = actuals_record.period_week
        if start is not None and wk is not None and wk >= 1:
            week_end = start + timedelta(days=7 * (wk - 1) + 6)
            return datetime.combine(week_end, dtime.min, tzinfo=timezone.utc)

    return datetime.fromtimestamp(actuals_record.recorded_at, tz=timezone.utc)


def record_from_actuals(*, plan_brief, actuals_record, owner_id: str) -> None:
    """Convert one :class:`PlanActualsRecord` into one observation per
    channel × tracked metric. Called by the PUT /actuals route AFTER the
    record is persisted so we know its id + source_plan_id.

    ``plan_brief`` is the Brief (carrying client_id + target_ids); we use
    the FIRST target_id as the key — multi-target audiences are modelled
    as separate campaigns in the CCS pipeline, so fan-out would mix
    clearly-different populations.

    v6 · Issue 12 — ``observed_at`` maps to the event window the data
    actually represents (plan end-date for FINAL, week-end for WEEKLY),
    NOT when the planner typed it in. This is the clock decay maths
    runs against.
    """
    client_id = plan_brief.client_id
    target_ids = plan_brief.target_ids or []
    if not client_id or not target_ids:
        return
    target_id = target_ids[0]

    observed_at = _observed_at_for_record(plan_brief, actuals_record)
    for ch, ca in actuals_record.per_channel.items():
        for metric in _TRACKED_METRICS:
            value = getattr(ca, metric, None)
            if value is None or value == 0:
                # 0 is almost certainly "not filled in" rather than
                # genuinely zero — skip to avoid poisoning the mean.
                continue
            record_observation(
                client_id=client_id, target_id=target_id,
                channel_id=ch, metric=metric, value=float(value),
                owner_id=owner_id, observed_at=observed_at,
                source_plan_id=actuals_record.plan_id,
                source_actuals_id=actuals_record.id,
            )


# ---------- Effective weight (issue 7 · v6 · FR-30) ----------

def compute_effective_weight(obs: CalibrationObservation, half_life_days: float,
                              *, at: Optional[datetime] = None) -> float:
    """The weight this observation currently contributes to its profile mean.

    If ``weight_override`` is set it overrides decay; otherwise compute from
    age. Returns the same value the profile materialiser would apply to this
    observation — letting the frontend render "what's this row worth right
    now?" without mirroring the maths.
    """
    if obs.weight_override is not None:
        return max(0.0, min(1.0, float(obs.weight_override)))
    now = at or datetime.now(tz=timezone.utc)
    age_days = max(0.0, (now.timestamp() - float(obs.observed_at)) / 86400.0)
    return decay_weight(age_days, half_life_days)
