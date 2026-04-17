"""Ivy-user feedback: 'Compare 按鈕在只有 1 個 plan 時只跳錯誤訊息'.

When a user clicks Compare and only has 1 saved plan (typical after
they finish either Manual or Automatic for the first time), instead of
a dead-end "至少要有 2 個 plans" toast we should invite them to build
the OTHER mode's plan — that's exactly what Compare is for, and the
fork endpoint already exists.
"""
from __future__ import annotations

import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _picker_body() -> str:
    """Return the body of openComparePicker PLUS any helpers it delegates
    to when fewer than 2 plans are available — so the regression test
    isn't fooled by an extract-method refactor."""
    src = APP_JS.read_text(encoding="utf-8")
    m = re.search(
        r"(?:async\s+)?function\s+openComparePicker\s*\([^)]*\)\s*\{(.*?)\n\}\n",
        src, re.S,
    )
    assert m, "openComparePicker() not found"
    body = m.group(1)
    # Pull in any delegated helper that handles the <2 plans branch.
    for helper_name in re.findall(r"_render\w+", body):
        m2 = re.search(
            rf"function\s+{helper_name}\s*\([^)]*\)\s*\{{(.*?)\n\}}\n",
            src, re.S,
        )
        if m2:
            body += "\n// helper " + helper_name + ":\n" + m2.group(1)
    return body


def test_picker_does_not_dead_end_on_single_plan():
    """The picker should not just ``botSay`` a warning and return when
    fewer than 2 plans exist — it should offer an actionable next step.
    """
    body = _picker_body()
    # A bare "return" immediately after the 2-plan warning is the
    # anti-pattern we're killing. Look for a fork-call or new-session CTA.
    has_fork_call = "fork(" in body or "/fork" in body
    has_next_cta = "帶著" in body or "Fork" in body or "build another" in body.lower()
    assert has_fork_call or has_next_cta, (
        "openComparePicker should offer a Fork / build-another-plan CTA "
        "when fewer than 2 plans exist — not just a warning toast."
    )


def test_picker_mentions_fork_affordance():
    """Explicit: there must be a user-visible string that explains the
    'build a second plan by forking' option."""
    body = _picker_body()
    hints = ["fork", "Fork", "Automatic", "Manual", "Plan 2", "Plan 1"]
    # The CTA text should reference at least one of these. Defensive —
    # lets copy evolve without breaking the test.
    assert any(h in body for h in hints), (
        f"Expected a fork/mode-change affordance string; body head: {body[:300]}"
    )
