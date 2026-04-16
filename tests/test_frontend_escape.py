"""TS-9 · Frontend rendering escape (NFR-1.4).

Static-analysis-style guard on ``app/static/app.js``: any path that echoes
user input (e.g. the ``userSay`` function used to display "Project：<input>"
back in a bubble) must NOT go through ``innerHTML``.
"""
from __future__ import annotations

import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_has_escape_helper():
    """A dedicated escape helper should exist and be used by userSay()."""
    src = _source()
    assert "escapeHTML" in src, (
        "app.js should expose an escapeHTML() helper for user-echoed strings"
    )


def test_user_say_does_not_assign_innerHTML_directly():
    """The path that writes USER strings into a bubble must use textContent."""
    src = _source()
    # find the userSay function body
    m = re.search(r"function\s+userSay\s*\([^)]*\)\s*\{([^}]*)\}", src)
    assert m, "userSay() not found in app.js"
    body = m.group(1)
    assert "innerHTML" not in body, (
        "userSay() should use textContent (or the bubble helper's escape path), "
        f"not innerHTML. Body was:\n{body}"
    )


def test_bubble_user_path_does_not_use_innerHTML():
    """The user-branch of bubble() should not assign innerHTML either."""
    src = _source()
    # Grab the bubble() function body
    m = re.search(r"function\s+bubble\s*\([^)]*\)\s*\{(.*?)\n\}", src, re.S)
    assert m, "bubble() not found"
    body = m.group(1)
    # The user branch is reached when `who === 'user'`. The test accepts
    # innerHTML being used for the bot path, but not the user path.
    # Heuristic: a line like `if (who === "user") ... innerHTML` is a red flag.
    suspicious = re.findall(
        r'who\s*===?\s*[\'"]user[\'"][^\n]*innerHTML', body
    )
    assert not suspicious, (
        "bubble() should not set innerHTML on the user branch"
    )


def test_show_command_does_not_concatenate_user_data_into_innerHTML():
    """TS-9.4 · /show historically injected user data directly into an
    innerHTML string:

        botSay("<pre>" + JSON.stringify(state.session.brief) + "</pre>")

    A project_name of ``x</pre><script>alert(1)</script><pre>`` survives
    JSON.stringify and reaches innerHTML as runnable HTML. Guard against
    the regression by asserting the exact anti-pattern is gone.
    """
    src = _source()
    # Match the entire if-block for "/show" — the handler is a single-line
    # if {} statement; grab from the opening "/show" to the matching return
    # or closing brace.
    m = re.search(r'"/show"[^\n]*', src)
    assert m, "/show command handler not found"
    handler = m.group(0)
    # The anti-pattern: concatenating JSON.stringify into a botSay() string
    # that ends up in innerHTML.
    anti = [
        'botSay("<',
        "botSay('<",
        'botSay(`<',
    ]
    for pat in anti:
        assert pat not in handler, (
            f"/show should not call botSay() with a raw HTML string "
            f"containing user data (found pattern {pat!r} in: {handler})"
        )


def test_escape_helper_is_actually_used():
    """TS-9.5 · the escapeHTML() helper should not be dead ceremony; if it
    exists, at least one call site must use it."""
    src = _source()
    if "function escapeHTML" not in src and "escapeHTML =" not in src:
        return  # not defined, skip

    # Total occurrences of ``escapeHTML(`` minus the declaration = usages.
    # Declarations look like ``function escapeHTML(`` or ``escapeHTML = (``.
    all_calls = len(re.findall(r"escapeHTML\s*\(", src))
    decls = len(re.findall(r"function\s+escapeHTML\s*\(", src))
    # Note: if someone defines it as ``const escapeHTML = (s) => …`` the
    # pattern ``escapeHTML\(`` would NOT match the declaration (no paren
    # directly after), so we don't subtract for that form.
    usages = all_calls - decls
    assert usages >= 1, (
        "escapeHTML() is declared but never called — remove it or wire it up."
    )
