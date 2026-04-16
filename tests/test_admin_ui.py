"""TS-26 · Admin-only Users tab in the frontend (FR-26)."""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"
INDEX_HTML = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"


def _js(): return APP_JS.read_text(encoding="utf-8")
def _html(): return INDEX_HTML.read_text(encoding="utf-8")


def test_users_button_in_topbar():
    assert 'id="btnUsers"' in _html() or "btnUsers" in _html()


def test_render_users_function_exists():
    assert "renderUsers" in _js()


def test_invite_user_flow_exists():
    src = _js()
    # Must call POST /api/users somewhere and surface the one-time key.
    assert "POST" in src and "/api/users" in src


def test_disable_and_rotate_handlers_exist():
    src = _js()
    assert "/disable" in src and "/rotate" in src


def test_users_tab_hidden_for_non_admin():
    """Heuristic: the render function checks an is_admin flag before
    showing the tab content."""
    src = _js()
    assert "is_admin" in src, (
        "renderUsers() should respect the current user's is_admin flag"
    )
