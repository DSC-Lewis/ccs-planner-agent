"""TS-32 · Frontend shell for actuals modal + reports tab (PRD v6 · FR-33).

Static analysis only — asserts the expected names, zh-Hant labels, and
endpoint calls appear in ``app.js``. Real UI behaviour is covered by
Playwright in the deploy smoke tests (out of scope here)."""
from __future__ import annotations

from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ---------- Entry point (Project Detail) ----------

def test_record_actuals_button_present():
    src = _src()
    # 📊 alone is too loose — it's used elsewhere in app.js already.
    # Require a specific "Record actuals" / "記錄實際" / "記錄成效" label.
    assert any(s in src for s in ("Record actuals", "記錄實際", "記錄成效")), (
        "Project Detail plan rows must have a specific 'Record actuals' label."
    )


def test_record_actuals_wires_to_modal_opener():
    src = _src()
    markers = ["openActualsModal", "renderActualsModal", "showActualsModal"]
    assert any(m in src for m in markers), (
        f"Expected a modal opener function (one of {markers})"
    )


# ---------- Dual-tab modal ----------

def test_modal_has_weekly_and_final_tabs():
    src = _src()
    assert "週週補" in src, "Weekly tab label 週週補 missing"
    assert "最終結算" in src, "Final tab label 最終結算 missing"


def test_aggregate_weekly_to_final_button_exists():
    src = _src()
    # PRD §5 copy — user-visible trigger for auto-fill.
    assert "用週數據試算最終結算" in src or "aggregate" in src.lower(), (
        "Missing 'aggregate weekly → final' helper button"
    )


def test_modal_calls_actuals_endpoints():
    src = _src()
    assert "/api/plans/" in src and "/actuals" in src, (
        "Modal must hit the /api/plans/{id}/actuals endpoint"
    )


# ---------- Reports tab ----------

def test_reports_tab_present():
    src = _src()
    # Either localised "成效回顧" or "Reports" trigger.
    assert "成效回顧" in src or "Reports" in src or "renderReport" in src, (
        "Reports sub-tab must exist on Project Detail"
    )


def test_reports_uses_report_endpoint():
    src = _src()
    assert "/report" in src, (
        "Reports view must call GET /api/plans/{id}/report"
    )


def test_printable_html_report_link():
    """FR-29 — the printable HTML report is a separate endpoint so planners
    can Cmd-P without our JS dep."""
    src = _src()
    assert "report.html" in src, (
        "Reports view must expose the printable HTML report link"
    )


# ---------- XSS safety rail for numeric echo ----------

def test_override_step_uses_text_node_for_user_input_echo():
    """NFR-7.6 reuses the v5 XSS pattern — numeric echo must go through
    createTextNode, never innerHTML."""
    src = _src()
    assert "createTextNode" in src, (
        "Keep using document.createTextNode for user-input echo (NFR-7.6)."
    )
