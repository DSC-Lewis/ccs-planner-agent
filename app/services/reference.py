"""Loads and exposes the reference mock data (surveys, channels, etc.)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict, List

from ..config import DATA_DIR
from ..schemas import (
    BrandKPI,
    ChannelGroup,
    ChannelMetric,
    Client,
    OptimizationOption,
    Survey,
    TargetAudience,
)


def _read(name: str):
    with (DATA_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def surveys() -> List[Survey]:
    return [Survey(**row) for row in _read("surveys.json")]


@lru_cache(maxsize=1)
def clients() -> List[Client]:
    return [Client(**row) for row in _read("clients.json")]


@lru_cache(maxsize=1)
def targets() -> List[TargetAudience]:
    return [TargetAudience(**row) for row in _read("targets.json")]


@lru_cache(maxsize=1)
def brand_kpis() -> List[BrandKPI]:
    return [BrandKPI(**row) for row in _read("brand_kpis.json")]


@lru_cache(maxsize=1)
def channel_groups() -> List[ChannelGroup]:
    raw = _read("channels.json")
    return [ChannelGroup(**g) for g in raw["groups"]]


@lru_cache(maxsize=1)
def channel_metrics() -> Dict[str, ChannelMetric]:
    return {k: ChannelMetric(**v) for k, v in _read("channel_metrics.json").items()}


@lru_cache(maxsize=1)
def optimization_options() -> Dict[str, List[OptimizationOption]]:
    raw = _read("optimization_options.json")
    return {
        "criteria":   [OptimizationOption(**o) for o in raw["criteria"]],
        "strategies": [OptimizationOption(**o) for o in raw["strategies"]],
    }


@lru_cache(maxsize=1)
def frequency_thresholds() -> List[int]:
    return _read("optimization_options.json")["frequency_thresholds"]


def channel_label(channel_id: str) -> str:
    for group in channel_groups():
        for leaf in group.children:
            if leaf.id == channel_id:
                return leaf.label
    return channel_id


def all_channel_ids() -> List[str]:
    out: List[str] = []
    for group in channel_groups():
        out.extend(c.id for c in group.children)
    return out


def validate_target_against_survey(target_ids: List[str], survey_id: str) -> List[str]:
    """Return warnings if any target's survey year does not match the brief survey."""
    warnings: List[str] = []
    by_id = {t.id: t for t in targets()}
    for tid in target_ids:
        t = by_id.get(tid)
        if not t:
            warnings.append(f"Target '{tid}' not found in mock data.")
            continue
        if t.survey_id != survey_id:
            warnings.append(
                f"Target '{t.name}' is sourced from {t.source}, "
                f"which does not match the selected survey."
            )
        if t.warning:
            warnings.append(f"{t.name}: {t.warning}")
    return warnings
