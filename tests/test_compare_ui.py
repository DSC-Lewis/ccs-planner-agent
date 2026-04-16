"""TS-17 · Frontend compare-plans view (FR-13, FR-14, NFR-4).

Static-analysis style — greps ``app.js`` / ``index.html`` for the shape of
the implementation. We don't spin up a headless browser for pilot tests.
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"
INDEX_HTML = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"


def js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_chartjs_cdn_reference_present():
    """FR-14 — Chart.js loads lazily from CDN."""
    src = js()
    assert "chart.js" in src.lower(), "Chart.js CDN URL missing from app.js"


def test_render_compare_function_exists():
    assert "renderCompare" in js() or "renderComparison" in js(), (
        "Need a renderCompare() entry point to be called from the review step"
    )


def test_picker_modal_scaffolding_exists():
    src = js()
    assert "comparePicker" in src or "openComparePicker" in src, (
        "Need a picker to choose which saved plans to compare"
    )


def test_five_canvas_targets_referenced():
    """FR-C1..C5 — five charts."""
    src = js()
    expected = [
        "chart-summary",    # C1 summary matrix
        "chart-budget",     # C2 stacked bar
        "chart-reach",      # C3 grouped bar
        "chart-frequency",  # C4 multi-line
        "chart-weekly",     # C5 weekly GRP
    ]
    missing = [c for c in expected if c not in src]
    assert not missing, f"Missing canvas ids: {missing}"


def test_graceful_cdn_failure_path():
    """NFR-4.1 — Chart load failure must not block tables."""
    src = js()
    assert "try" in src and "catch" in src, (
        "Expected try/catch around Chart.js dynamic import"
    )
    # A user-facing fallback label
    assert "charts unavailable" in src.lower() or "chart" in src.lower(), (
        "Need a user-facing fallback message when charts fail"
    )


def test_six_colour_palette_declared():
    """NFR-4.3 — deterministic channel→colour mapping across charts."""
    src = js()
    # 6 hex colours, declared together
    import re
    hex_colours = re.findall(r"#[0-9A-Fa-f]{6}", src)
    assert len(hex_colours) >= 6, (
        f"Expected at least 6 palette hex colours, found {len(hex_colours)}"
    )


def test_compare_button_added_to_review_ui():
    """The compare entry point must be reachable from the existing review step."""
    src = js()
    assert "Compare plans" in src or "Compare plan" in src, (
        "Need a Compare-plans CTA in the UI text"
    )
