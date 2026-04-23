"""Regression — the Chart.js capture pattern must NOT use `window.Chart`.

Context: Chart.js is loaded via ESM `import("...+esm")` (see loadChartLib)
which stores the Chart constructor in module-local `_chartLib`. It does
NOT assign `window.Chart`. Code that does:

    const OriginalChart = window.Chart;          // undefined
    window.Chart = class extends OriginalChart;  // TypeError at runtime

throws TypeError: "Class extends value undefined is not a constructor"
or silently ends up with an empty captures dict, depending on whether
the monkey-patch is guarded.

This test locks down that the buggy pattern is gone and the capture
shim receives the actually-loaded Chart reference (a local variable
named `Chart` from `await loadChartLib()`).
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_no_window_chart_extends_pattern():
    """The classic buggy shape:
        window.Chart = class extends <something> { ... }
    must be completely absent. Chart.js ESM doesn't set window.Chart,
    so extending it throws TypeError."""
    src = _src()
    bad_patterns = [
        "window.Chart = class extends",   # direct monkey-patch of window.Chart
        "class extends window.Chart",     # inverse formulation
    ]
    for p in bad_patterns:
        assert p not in src, (
            f"Buggy capture pattern `{p}` must not appear — Chart.js ESM "
            f"doesn't set window.Chart, so this throws at runtime."
        )


def test_capture_uses_loaded_chart_reference():
    """The capture subclass must extend the LOADED Chart class. We expect
    source to reference a subclass pattern that uses the local `Chart`
    variable coming back from `await loadChartLib()`.

    Acceptable patterns:
      * `class extends Chart {` (direct)
      * `class CapturingChart extends Chart` (named)
    """
    src = _src()
    accepted = ["class extends Chart {", "class extends Chart\n",
                "CapturingChart extends Chart"]
    assert any(p in src for p in accepted), (
        f"Capture subclass must extend the locally-loaded Chart ref — "
        f"expected one of {accepted} in source."
    )


def test_compare_bundle_renders_charts_offscreen_on_demand():
    """The Compare bundle path must build its chart instances offscreen
    at click time rather than trying to monkey-patch the base renderer.
    This avoids the empty-captures failure mode that makes the 📦 button
    show "圖表還在載入" instead of actually downloading."""
    src = _src()
    # Look for an absolute-positioned offscreen host inside the
    # compare-bundle flow. `left:-9999px` is the conventional marker.
    assert "left:-9999px" in src or "left: -9999px" in src, (
        "Compare bundle must render charts offscreen (position:absolute; "
        "left:-9999px) on demand so the captured instances are guaranteed "
        "non-empty at download time."
    )


def test_bundle_compare_called_with_non_empty_charts():
    """Visual audit that the on-click bundle handler populates a dict of
    Chart instances before calling bundleCompareZip."""
    src = _src()
    # At least one of the draw helpers must be invoked from within the
    # compare-bundle handler, with a capturing subclass as the first arg.
    invocation_markers = [
        "drawSummaryChart(", "drawBudgetChart(", "drawReachChart(",
        "drawFrequencyChart(", "drawWeeklyChart(",
    ]
    found = [m for m in invocation_markers if m in src]
    assert len(found) >= 5, (
        f"Compare bundle must re-invoke all 5 draw helpers offscreen "
        f"to capture instances; found only {found}"
    )
