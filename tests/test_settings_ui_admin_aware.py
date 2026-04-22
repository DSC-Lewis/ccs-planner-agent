"""v6 gap-audit · Issue 19 (frontend) — settings modal is admin-aware.

The Calibration Settings modal renders the global half-life slider for
everyone. After the backend guard lands, non-admin clicks on "套用 global"
would now 403. The UI should instead:

* Read the current user's role from state / /api/me.
* If not admin: hide or disable the 套用 + 還原預設 buttons for the
  global scope, and render a small "由 admin 管理" note.
* Still show the CURRENT value (read-only) so the user can see what's in
  effect.

Static-analysis tests — match the pattern in test_actuals_ui_shell.py.
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_settings_modal_reads_admin_flag():
    """The settings renderer must branch on is_admin somewhere."""
    src = _src()
    # Look for an explicit is_admin check in the calibration settings code
    # path. We accept any of the common shapes.
    markers = ["me.is_admin", "user.is_admin", "state.me", "isAdmin", "is_admin"]
    assert any(m in src for m in markers), (
        f"Settings modal must check admin status (one of {markers})"
    )


def test_settings_modal_has_admin_only_note():
    """When the user can't edit global, we need a visible note explaining
    why the slider is read-only."""
    src = _src()
    note_markers = ["由 admin 管理", "admin 管理", "admin-only", "需要 admin",
                    "只有 admin"]
    assert any(m in src for m in note_markers), (
        f"Non-admin path must render an explanatory note (one of {note_markers})"
    )
