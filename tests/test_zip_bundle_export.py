"""v7.1 · One-click ZIP bundle — single-plan AND compare.

Follow-up to PR #13. The HTML-only export was a start; the user's real
ask is "一鍵打包下載結果，不管是單一個或是需要比較都要產出" — a single
button that produces a complete bundle (HTML + individual PNGs + raw
JSON data + README) whether the current view is a single Review or a
multi-plan Compare.

Static-analysis tests — follows the shape of test_review_dashboard_shell.py.
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ---------- JSZip lazy loader ----------

def test_load_zip_lib_helper_exists():
    """Lazy-load pattern — match loadChartLib's style."""
    src = _src()
    markers = ["loadZipLib", "_loadZipLib", "JSZip"]
    assert any(m in src for m in markers), (
        f"Expected a lazy JSZip loader / reference (one of {markers})"
    )


def test_jszip_is_lazy_loaded_from_cdn():
    """Don't bundle JSZip — pull it from a CDN only when export runs."""
    src = _src()
    # Either jsdelivr or unpkg is acceptable; the key invariant is
    # "no bundled <script src=... jszip>" and "fetched inside a helper".
    assert "jszip" in src.lower(), "JSZip must be referenced for the loader"
    assert "cdn" in src.lower() or "jsdelivr" in src.lower() or "unpkg" in src.lower(), (
        "JSZip must be pulled from a CDN, not bundled, to keep the shell lean."
    )


# ---------- Single-plan bundle ----------

def test_bundle_single_plan_helper_exists():
    src = _src()
    markers = ["bundlePlanZip", "bundleSinglePlan", "downloadPlanBundle"]
    assert any(m in src for m in markers), (
        f"Expected a single-plan ZIP bundle function (one of {markers})"
    )


def test_single_plan_bundle_button_labelled_zip():
    """Review + report views must show a 📦 button, not the old 📄 HTML one."""
    src = _src()
    zip_markers = ["一鍵打包下載", "一鍵打包", "打包下載", "下載壓縮檔",
                   "📦"]
    assert any(m in src for m in zip_markers), (
        f"ZIP bundle button missing zh-Hant label / icon (one of {zip_markers})"
    )


# ---------- Compare bundle ----------

def test_compare_view_has_bundle_button():
    """Previously Compare had no export; the same 📦 bundle flow must be
    reachable from the Compare view."""
    src = _src()
    markers = ["bundleCompareZip", "bundleCompare", "downloadCompareBundle",
               "renderCompareExport"]
    assert any(m in src for m in markers), (
        f"Expected a Compare-view bundle function (one of {markers})"
    )


def test_compare_bundle_references_multiple_plans():
    """The ZIP for Compare must carry both plans' data, not just one —
    look for the plural file name in the source."""
    src = _src()
    # We write plans.json (plural) for compare vs plan.json (singular)
    # for single-plan mode. Test that both exist in source.
    assert "plans.json" in src, (
        "Compare bundle must write a plans.json file with all plans"
    )
    assert "plan.json" in src, (
        "Single-plan bundle must write a plan.json file"
    )


# ---------- ZIP content structure ----------

def test_bundle_includes_chart_pngs_folder():
    src = _src()
    assert 'charts/' in src or '"charts"' in src or "'charts'" in src, (
        "Bundle must include a charts/ folder with PNGs"
    )


def test_bundle_includes_readme():
    """A short README helps the recipient understand the file layout."""
    src = _src()
    assert "README" in src, (
        "Bundle should include a README.txt explaining the file layout"
    )


def test_bundle_uses_jszip_generate_blob():
    """JSZip's generateAsync({type:'blob'}) is the standard call."""
    src = _src()
    assert "generateAsync" in src, (
        "ZIP creation must use JSZip.generateAsync()"
    )
    # Blob-based download uses the existing _triggerDownload helper
    # from PR #13.
    assert "_triggerDownload" in src or "triggerDownload" in src


def test_bundle_filename_reflects_mode():
    """Single bundle: ccs-bundle-<plan-name>.zip; compare: ccs-compare-<names>.zip."""
    src = _src()
    assert "ccs-bundle-" in src or "ccs-report-" in src, (
        "Single-plan ZIP filename should start with ccs-bundle- or ccs-report-"
    )
    assert "ccs-compare-" in src or "compare" in src.lower(), (
        "Compare ZIP filename should reference compare mode"
    )


# ---------- Graceful fallback ----------

def test_fallback_when_zip_lib_unavailable():
    """If JSZip fails to load (offline, blocked CDN), fall back to the
    existing single-file HTML export or show a useful error — don't leave
    the planner staring at a frozen modal."""
    src = _src()
    # Either the old exportPlanReport is still available as fallback
    # OR there's an explicit null-check path with a user-friendly message.
    assert "exportPlanReport" in src or "alert(" in src, (
        "When loadZipLib() returns null, either fall back to exportPlanReport "
        "or show an alert() explaining why the download can't run."
    )
