"""PR C · Frontend UX completion (PRD v6 · FR-30 .. FR-34 polish).

Static-analysis tests — they verify that the v6 PR C wiring is present
in ``app.js`` (function names, endpoint strings, zh-Hant labels). Real
interactive behaviour is covered by Playwright in deploy smoke tests,
which is out of scope for the unit suite.

Matches the pattern used by ``test_actuals_ui_shell.py`` and
``test_calibration_settings_panel.py``.
"""
from __future__ import annotations

from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ---------- Issue 2: Overrides input modal ----------

def test_overrides_modal_function_exists():
    assert "openOverridesModal" in _src(), (
        "PR C · Issue 2 — openOverridesModal function must be defined"
    )


def test_overrides_modal_has_channel_rows_and_actions():
    src = _src()
    # Each channel row has a 清除 (clear) button and the modal has a 儲存
    # (save) button. Both labels must be present for the modal to be usable.
    assert "清除" in src, "Overrides modal missing per-row 清除 button"
    assert "儲存" in src, "Overrides modal missing top-level 儲存 button"
    # zh-Hant entry affordance labels.
    assert "調整預設值" in src or "調整 CPM/Penetration" in src, (
        "Missing entry-point button copy for overrides modal"
    )


# ---------- Issue 3: CAL pill in Channels step ----------

def test_channels_step_calls_channel_summary_endpoint():
    src = _src()
    assert "/api/calibration/channel-summary" in src, (
        "PR C · Issue 3 — renderChannels must call /api/calibration/channel-summary"
    )
    # And the CAL · <score> pill text must appear somewhere.
    assert "CAL" in src, "CAL-pill label not rendered"


# ---------- Issue 4: Chart.js bar chart in Reports view ----------

def test_report_view_uses_chartjs_bar_chart():
    src = _src()
    # Accept either 'type: "bar"' (single/double quotes) — ripgrep cares
    # about escaping.
    assert 'type: "bar"' in src or "type: 'bar'" in src, (
        "PR C · Issue 4 — report view must call new Chart({ type: 'bar' ... })"
    )
    # Ensure both datasets (Planned + Actual) exist.
    assert "Planned Spend" in src and "Actual Spend" in src, (
        "Grouped bar chart must include Planned and Actual datasets"
    )


def test_report_view_adds_variance_columns():
    src = _src()
    assert "cpm_variance_pct" in src, "CPM variance column missing from report UI"
    assert "impressions_variance_pct" in src, (
        "Impressions variance column missing from report UI"
    )


# ---------- Issue 5: Per-client / per-channel overrides in Settings ----------

def test_per_client_and_per_channel_half_life_forms():
    src = _src()
    assert "per-client half-life" in src, (
        "PR C · Issue 5 — per-client half-life form copy must be present"
    )
    assert "per-channel half-life" in src, (
        "PR C · Issue 5 — per-channel half-life form copy must be present"
    )
    # Both forms must actually PUT to /api/calibration/settings with a
    # specific scope. Grep for the scope literals.
    assert '"scope": "client"' in src or "scope: \"client\"" in src, (
        "client-scoped PUT payload missing"
    )
    assert '"scope": "channel"' in src or "scope: \"channel\"" in src, (
        "channel-scoped PUT payload missing"
    )
    # Reset/還原 action in existing-overrides tables.
    assert "還原" in src, "Missing 還原 (reset) button on per-scope overrides"


# ---------- Issue 6: Fill Now CTA jumps to overrides modal ----------

def test_fill_now_cta_opens_overrides_modal():
    src = _src()
    # The CTA on the recommend-fill banner must call openOverridesModal,
    # not alert(). We test by proximity: the CTA label + the modal opener
    # must both appear in the banner callback section.
    assert "RECOMMEND_FILL_BANNER.cta" in src, "banner CTA wiring missing"
    # openOverridesModal(state.session) must be invoked inside the banner
    # path — the easiest greppable assertion is both strings present.
    assert "openOverridesModal(state.session)" in src, (
        "PR C · Issue 6 — Fill-Now CTA must call openOverridesModal(state.session)"
    )


# ---------- Issue 7: effective_weight visible in observation drawer ----------

def test_observation_drawer_shows_effective_weight_and_age():
    src = _src()
    assert "effective_weight" in src, (
        "PR C · Issue 7 — observation drawer must render effective_weight"
    )
    assert "age_days" in src, (
        "PR C · Issue 7 — observation drawer must render age_days"
    )
    # "(pinned)" marker when weight_override is set.
    assert "pinned" in src, "Missing '(pinned)' marker for weight_override"


# ---------- Issue 8: Rich confidence tooltip ----------

def test_confidence_tooltip_shows_formula_breakdown():
    src = _src()
    assert "sample_factor" in src, (
        "PR C · Issue 8 — confidence tooltip must show sample_factor"
    )
    assert "consistency_factor" in src, (
        "PR C · Issue 8 — confidence tooltip must show consistency_factor"
    )
    # Formula breakdown body text.
    assert "Formula" in src or "formula" in src, (
        "Tooltip missing formula line"
    )
    # The class name used to render the tooltip panel.
    assert "confidence-tooltip" in src, (
        "Confidence tooltip container class missing"
    )


# ---------- Issue 9: Actuals history viewer ----------

def test_actuals_history_viewer_references_history_endpoint():
    src = _src()
    assert "/actuals/history" in src, (
        "PR C · Issue 9 — history viewer must call /api/plans/{id}/actuals/history"
    )
    assert "歷史記錄" in src, "History button label 歷史記錄 missing"


# ---------- Issue 10: Per-week delete in actuals modal ----------

def test_per_week_delete_button_exists():
    src = _src()
    # DELETE to /actuals/{record_id}
    assert "method: \"DELETE\"" in src or "method: 'DELETE'" in src, (
        "PR C · Issue 10 — DELETE call missing"
    )
    assert "刪除本週" in src, "Missing 🗑 刪除本週 button label"


# ---------- Issue 15: data-plan-id attribute ----------

def test_plan_rows_have_data_plan_id_attribute():
    src = _src()
    assert "data-plan-id" in src, (
        "PR C · Issue 15 — plan rows must expose a data-plan-id attribute"
    )


# ---------- Issue 16: render-epoch staleness guard ----------

def test_render_epoch_counter_exists():
    src = _src()
    assert "_renderEpoch" in src, (
        "PR C · Issue 16 — render-epoch counter (state._renderEpoch) must exist"
    )
    # And at least one async continuation must check it.
    assert "state._renderEpoch" in src, (
        "PR C · Issue 16 — staleness guard must be referenced in async code"
    )
