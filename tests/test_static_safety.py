"""TS-7 · Static-file safety (NFR-1.1).

Reproduces the live-probe finding: ``GET /..%2Fconfig.py`` must not leak
``app/config.py``. Legitimate asset and SPA-fallback routes must keep working.
"""
from __future__ import annotations

import pytest


CONFIG_MARKER = "Runtime configuration loaded from env vars"


@pytest.mark.parametrize("path", ["/", "/assets/styles.css", "/assets/app.js"])
def test_legitimate_paths_return_200(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_traversal_via_percent_encoded_dotdot_does_not_leak_config(client):
    """TC-7.3 / TC-7.5 — reproduces the live-probe leak of app/config.py."""
    r = client.get("/..%2Fconfig.py")
    # Either the server rejects (4xx) or it falls through to index.html; the
    # only unacceptable outcome is returning the config.py source code.
    assert CONFIG_MARKER not in r.text, (
        f"config.py contents leaked via path traversal! status={r.status_code}"
    )


def test_traversal_via_raw_dotdot_does_not_leak_config(client):
    """Same probe without percent-encoding."""
    r = client.get("/../config.py")
    assert CONFIG_MARKER not in r.text


def test_absolute_path_does_not_serve_host_files(client):
    r = client.get("//etc/hosts")
    # accept anything except a successful 200 with /etc/hosts content
    body = r.text.lower()
    assert "localhost" not in body or "html" in body, (
        "Response looks like it leaked /etc/hosts"
    )


def test_unknown_path_falls_back_to_index(client):
    """SPA-style fallback: an unknown in-bounds path returns index.html."""
    r = client.get("/some/client-side/route")
    assert r.status_code == 200
    assert "<title>CCS Planner" in r.text
