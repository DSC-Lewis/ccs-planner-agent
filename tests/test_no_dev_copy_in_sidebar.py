"""User feedback: 側邊的 'Manual Mode · 00:00–18:09 對應' 是開發期內部 copy，
對真實 PM 毫無意義，反而讓介面看起來很粗糙。拔掉。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "app" / "static" / "app.js"
INDEX_HTML = ROOT / "app" / "static" / "index.html"


BAD_STRINGS = [
    "00:00–18:09",
    "18:10 後對應",
    "18:10 後",
    "以對話協助 PM / Planner 修改 Brief",
    "所有狀態同步到後端，重新整理不遺失",
]


def test_dev_copy_removed_from_index_html():
    html = INDEX_HTML.read_text(encoding="utf-8")
    leftovers = [s for s in BAD_STRINGS if s in html]
    assert not leftovers, (
        f"index.html still contains dev-facing copy: {leftovers}"
    )


def test_dev_copy_removed_from_app_js():
    """renderSidebar used to set #modeLabel textContent to the
    00:00-18:09 timecode string. That should be gone or replaced with
    a user-friendly label."""
    js = APP_JS.read_text(encoding="utf-8")
    leftovers = [s for s in BAD_STRINGS if s in js]
    assert not leftovers, (
        f"app.js still contains dev-facing copy: {leftovers}"
    )
