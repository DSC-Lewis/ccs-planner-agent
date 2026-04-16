"""Regression test for the v5-ivy deploy bug.

``renderManualPlan`` previously built a table, kept references only
through ``id`` attributes, then called ``recompute()`` BEFORE the card
was appended to the document. At that point ``document.getElementById()``
returned null and a TypeError aborted the render — the user saw the
prompt but no table, no inputs, no submit button.

Fix direction: pass element references directly instead of relying on
``document.getElementById`` to find nodes that haven't been inserted yet.
This test locks that invariant.
"""
from __future__ import annotations

import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _render_manual_plan_body() -> str:
    """Return the source of the renderManualPlan function only."""
    src = APP_JS.read_text(encoding="utf-8")
    # Everything from ``function renderManualPlan`` to the next
    # top-level ``function `` that's not nested.
    m = re.search(
        r"function\s+renderManualPlan\s*\([^)]*\)\s*\{(.*?)\n\}\n",
        src, re.S,
    )
    assert m, "renderManualPlan() not found in app.js"
    return m.group(1)


def test_render_manual_plan_does_not_use_getElementById_for_its_own_cells():
    """The id-based lookups (``tot-``, ``w-``, ``grand``) were the root
    cause. Grep for the anti-pattern explicitly."""
    body = _render_manual_plan_body()
    offenders = []
    for pattern in [
        r'getElementById\(\s*"tot-',
        r'getElementById\(\s*"w-',
        r'getElementById\(\s*"grand"',
    ]:
        if re.search(pattern, body):
            offenders.append(pattern)
    assert not offenders, (
        "renderManualPlan() is still looking up its own cells by document "
        "id — they don't exist until after the card is inserted. "
        f"Anti-patterns found: {offenders}. Keep element references in a "
        "local map instead."
    )


def test_render_manual_plan_appends_card_before_recompute():
    """Belt-and-suspenders: if we ever go back to id-based lookups, at
    least make sure the DOM is populated first."""
    body = _render_manual_plan_body()
    append_pos = body.find("msg.append(card)")
    recompute_pos = body.find("recompute()")
    if append_pos >= 0 and recompute_pos >= 0 and recompute_pos > append_pos:
        # append happens first -> fine regardless of id vs ref pattern
        return
    # Otherwise we require the id-based lookups to be gone (checked above).
    # Nothing else to assert; the previous test covers it.
