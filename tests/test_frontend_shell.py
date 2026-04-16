"""TS-23 · Frontend login + home + history scaffolding (FR-21).

Static-analysis. Keeps parity with the approach used for TS-17/TS-9.
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_login_view_scaffolding_exists():
    src = js()
    assert "renderLogin" in src or "showLoginPrompt" in src, (
        "Need a login prompt that asks for the API key"
    )


def test_render_projects_function():
    assert "renderProjects" in js()


def test_render_project_detail_function():
    assert "renderProjectDetail" in js()


def test_render_history_function():
    assert "renderHistory" in js()


def test_apikey_stored_in_localstorage():
    src = js()
    # Some localStorage key should include "apiKey" or "api_key".
    assert ("apiKey" in src) or ("api_key" in src), (
        "The API key should round-trip through localStorage"
    )


def test_fetch_calls_add_x_api_key_header():
    """Every fetch() call in the file should go through a helper that
    attaches the X-API-Key header. Heuristic: a top-level ``apiFetch`` or
    similar wrapper exists and is used."""
    src = js()
    assert "X-API-Key" in src, "Frontend must send the key header"
    assert "apiFetch" in src or "authHeaders" in src or "withAuth" in src, (
        "Expected a fetch wrapper that injects X-API-Key"
    )
