"""User feedback from the ivy deployment: 'Manual 跟 Automatic 感覺一樣'.

When the Agent enters either mode's unique first step (``calibration`` for
Manual, ``criterion`` for Automatic) it should print a one-line intro that
says what this mode actually does differently — so the user isn't left
thinking the two modes are cosmetic clones.
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_manual_mode_intro_present():
    src = _src()
    # The Manual intro belongs with the calibration step (first manual-only).
    assert "MANUAL_MODE_INTRO" in src or "manualModeIntro" in src, (
        "Expected a dedicated Manual-mode intro constant/helper"
    )


def test_automatic_mode_intro_present():
    src = _src()
    assert "AUTO_MODE_INTRO" in src or "autoModeIntro" in src, (
        "Expected a dedicated Automatic-mode intro constant/helper"
    )


def test_intros_mention_what_each_mode_does():
    """Not just a different label — the copy must explain the value prop."""
    src = _src()
    manual_markers = ["逐週", "Plan 1", "每週", "Manual"]
    auto_markers = ["最佳化", "總預算", "Mandatory", "Optimization"]
    assert any(m in src for m in manual_markers), (
        f"Manual intro should reference weekly/Plan 1/逐週. Src head: {src[:200]}"
    )
    assert any(m in src for m in auto_markers), (
        f"Automatic intro should reference optimization/budget/mandatory"
    )


def test_intro_is_shown_only_once_per_mode_change():
    """The intro should fire on entry to the mode-specific step, not on
    every re-render. Implementation detail: guard with a flag in state."""
    src = _src()
    assert "introShown" in src or "introSeen" in src, (
        "Need a sentinel (e.g. state.introShown) so the intro only fires "
        "once per session rather than on every re-render."
    )
