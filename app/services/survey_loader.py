"""Parse a CCS survey CSV export and turn it into channel penetration overrides.

Every row in the export looks like::

    variable, header, item, item_level, item_sub, measure, agreement_level, indexno

The columns we care about are ``variable`` (the survey code, e.g. ``Z5_R1_1``),
``agreement_level`` (penetration, 0.0-1.0) and ``measure`` (weighted universe in
persons).  We map those survey codes onto our internal channel IDs using the
mapping file in ``samples/channel_survey_mapping.json``.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..config import DATA_DIR

SAMPLES_DIR = DATA_DIR / "samples"
DEFAULT_CSV = SAMPLES_DIR / "ccs_taiwan_2025_export.csv"
DEFAULT_MAPPING = SAMPLES_DIR / "channel_survey_mapping.json"


@dataclass
class SurveyRow:
    variable: str
    header: str
    item: str
    item_sub: str
    measure: float
    agreement_level: float
    index_no: float


def load_rows(path: Path | str = DEFAULT_CSV) -> List[SurveyRow]:
    p = Path(path)
    rows: List[SurveyRow] = []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            try:
                rows.append(SurveyRow(
                    variable=raw.get("variable", "").strip(),
                    header=raw.get("header", "").strip(),
                    item=raw.get("item", "").strip(),
                    item_sub=raw.get("item_sub", "").strip(),
                    measure=float(raw.get("measure") or 0.0),
                    agreement_level=float(raw.get("agreement_level") or 0.0),
                    index_no=float(raw.get("indexno") or 0.0),
                ))
            except ValueError:
                # malformed rows are skipped but not silent during tests
                continue
    return rows


def load_mapping(path: Path | str = DEFAULT_MAPPING) -> Dict[str, Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)["mappings"]


def _index_by_variable(rows: List[SurveyRow]) -> Dict[str, SurveyRow]:
    """Last-wins — the CSV has Z5_R1_* and Z5_R6_* duplicates, same number."""
    out: Dict[str, SurveyRow] = {}
    for r in rows:
        if r.variable:
            out[r.variable] = r
    return out


def channel_penetration_overrides(
    csv_path: Path | str = DEFAULT_CSV,
    mapping_path: Path | str = DEFAULT_MAPPING,
) -> Dict[str, float]:
    """Return ``{channel_id: penetration_pct}`` from the CSV + mapping."""
    rows = load_rows(csv_path)
    mapping = load_mapping(mapping_path)
    by_var = _index_by_variable(rows)

    out: Dict[str, float] = {}
    for channel_id, cfg in mapping.items():
        row = by_var.get(cfg["variable"])
        if not row:
            continue
        out[channel_id] = round(row.agreement_level * 100, 2)
    return out


def estimated_universe(csv_path: Path | str = DEFAULT_CSV) -> Optional[int]:
    """Total universe is ``measure / agreement_level``; sample a row with a big
    base so the estimate is stable."""
    rows = load_rows(csv_path)
    best: Optional[SurveyRow] = None
    for r in rows:
        if r.agreement_level >= 0.5 and r.measure > 0:
            if best is None or r.measure > best.measure:
                best = r
    if not best:
        return None
    return int(round(best.measure / best.agreement_level / 1000))  # in thousands
