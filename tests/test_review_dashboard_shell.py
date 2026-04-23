"""v7 · Review dashboard + one-click export — frontend shell.

After PR merges, the Review step must render the same family of charts
as Compare (Summary / Budget / Reach / Frequency / Weekly GRP) PLUS
download affordances: per-chart PNG download and one master button
that exports the whole dashboard as a self-contained HTML file.

Static-analysis tests — matches the pattern in
`test_actuals_ui_shell.py` and `test_pr_c_frontend_shell.py`.
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ---------- Dashboard helper exists + wired into Review ----------

def test_render_plan_dashboard_helper_exists():
    """A dedicated helper that renders the chart cluster — so the same
    code can power Review and the reports view without duplication."""
    src = _src()
    markers = ["renderPlanDashboard", "_renderPlanDashboard",
               "buildPlanDashboard"]
    assert any(m in src for m in markers), (
        f"Expected a helper for the single-plan chart dashboard "
        f"(one of {markers})"
    )


def test_review_calls_augmented_endpoint():
    src = _src()
    assert "/augmented" in src, (
        "renderReview must call GET /api/plans/{id}/augmented for the "
        "single-plan enriched payload"
    )


def test_review_uses_chart_lib_for_dashboard():
    """The dashboard reuses the Compare lazy-loader so we don't double-bundle."""
    src = _src()
    assert "loadChartLib" in src  # established helper, already used by Compare


# ---------- Per-chart download ----------

def test_per_chart_png_download_wired():
    """Each chart canvas must have a 📥 PNG button that calls
    Chart.js's toBase64Image() (or canvas.toDataURL) to produce a
    download link."""
    src = _src()
    assert "toBase64Image" in src or "toDataURL" in src, (
        "Per-chart PNG download must use Chart.toBase64Image() or "
        "canvas.toDataURL() to produce a data URL"
    )
    download_markers = ["📥", "下載 PNG", "下載圖表", "download", "匯出"]
    assert any(m in src for m in download_markers), (
        f"Per-chart download button missing zh-Hant / visual affordance "
        f"(one of {download_markers})"
    )


# ---------- Master one-click export ----------

def test_full_report_export_button_exists():
    src = _src()
    markers = ["匯出完整報告", "匯出整份報告", "下載完整報告",
               "exportPlanReport", "downloadPlanReport"]
    assert any(m in src for m in markers), (
        f"Master 'export whole dashboard' button missing (one of {markers})"
    )


def test_export_uses_blob_download():
    """The export bundles chart PNGs + plan table into a self-contained
    HTML Blob and triggers a download. Look for the Blob URL pattern."""
    src = _src()
    assert "createObjectURL" in src, (
        "Blob download must go through URL.createObjectURL(new Blob([...]))"
    )
    assert "download=" in src or 'download"' in src or "a.download" in src \
        or ".setAttribute(\"download\"" in src, (
            "Download trigger must set the anchor's `download` attribute"
        )


def test_exported_html_is_self_contained():
    """Exported HTML should embed images as data: URIs (not remote hrefs)."""
    src = _src()
    # The export function embeds Chart.js canvases as base64 into <img src="data:...">
    assert "data:image/png;base64" in src or "image/png;base64," in src, (
        "Exported HTML must embed chart PNGs as data URIs so the file is "
        "self-contained and shareable offline."
    )
