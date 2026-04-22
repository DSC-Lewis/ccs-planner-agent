# PRD v6 — Actuals tracking, learning loop & planner-override inputs

> Date: 2026-04-22 · Scope: additive on top of v1..v5. No breaking changes.
> Owner: Dentsu PM team (ivy pilot feedback).

## 1. Problem

Post-pilot Ivy feedback, three gaps block the agent from being the team's
default planning tool:

1. **No post-launch loop.** Once a plan is saved, there's no way to record
   what actually ran — real spend, real impressions, real net reach — so
   the agent never learns from a project and cannot produce a
   post-campaign report.
2. **No learning from history.** Because we don't capture actuals, the
   next plan on the same client / target still uses generic CCS Taiwan
   2025 survey defaults. Planned-vs-actual variance compounds instead of
   shrinking.
3. **System values are opaque and unchallengeable.** Channel, Buying
   Audience, Media Budget, Impressions, CPM, Net Reach, Penetration are
   today auto-derived. Senior planners have better per-client numbers in
   their heads (or in spreadsheets) but there's nowhere to type them in,
   so the system's estimate is the only estimate — even when it's wrong.

## 2. Goals

1. **Actuals capture (weekly + final)** — a planner can open a saved
   Plan and record per-channel actuals either week-by-week, as a single
   final end-of-campaign snapshot, or both. Their choice.
2. **Post-campaign report** — one-click report summarising planned vs
   actual, variance %, and channel-level diagnostics, exportable as JSON
   and printable HTML. Each cell shows a confidence badge.
3. **Calibration feedback loop with tunable decay** — recorded actuals
   append to a per-client, per-target, per-channel observation store
   that materialises into a **CalibrationProfile** overriding the global
   survey defaults. Exponential time decay (default half-life 180 days)
   is **exposed as a slider** so planners can fit the curve to their
   market's volatility; individual outlier observations can be
   weight-pinned.
4. **Confidence score on every calibrated value** — a 0–100 interpretable
   score driven by decayed sample size + coefficient of variation, with
   traffic-light thresholds planners can trust at a glance.
5. **Planner-override inputs** — the Brief / Plan steps accept manual
   overrides for Buying Audience size, Media Budget, Impressions, CPM,
   Net Reach, and Penetration per channel. Overrides are stored
   alongside the calibrated default so we can always show both.
6. **Affordance to nudge quality** — when Channel Calibration or
   Penetration Adjustment is empty for the selected client × target, the
   agent prints a banner **strongly recommending** the planner fill them,
   because those two knobs have the largest impact on estimate accuracy.

Out of scope (next iteration): full Bayesian posterior with prior
variance, multi-market rollups, automated actuals ingestion from
ad-server APIs, seasonality priors.

## 3. Functional requirements

### FR-27 · Actuals schema (weekly + final, planner's choice)

- New entity `PlanActuals` — a **list** of records per plan, each tagged
  with a scope:
  ```
  PlanActuals(
      id, plan_id, recorded_by, recorded_at,
      scope: ActualsScope,            # WEEKLY | FINAL
      period_week: Optional[int],     # 1..weeks when scope=WEEKLY, else None
      per_channel: Dict[channel_id, ChannelActual],
      notes: Optional[str],
  )
  ChannelActual(
      spend_twd, impressions, cpm_twd,
      net_reach_pct, frequency, penetration_pct,
      buying_audience_000,
  )
  ```
- Uniqueness: at most one `(plan_id, scope=FINAL)` row, and at most one
  `(plan_id, scope=WEEKLY, period_week=k)` per week `k`. Re-submitting
  replaces; previous rows kept in `plan_actuals_history` for audit.
- Cadence is planner's choice — a plan can have only-FINAL, only-WEEKLY,
  or both. When both exist, FINAL wins for the report headline, WEEKLY
  rows still feed the calibration rollup with their own timestamps.
- API: `PUT /api/plans/{id}/actuals` takes an array of records so a
  planner can batch-upload weekly numbers in one call.

### FR-28 · Actuals endpoints (owner-scoped)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/plans/{id}/actuals` | All actuals rows for a plan (weekly + final), sorted by `period_week` then `recorded_at`. 200 with `[]` if none. |
| `PUT` | `/api/plans/{id}/actuals` | Upsert one or many records. Body: `{records: [ActualsRecord, ...]}`. Validates scope/week uniqueness server-side. |
| `DELETE` | `/api/plans/{id}/actuals/{record_id}` | Remove a single weekly or final record (soft-delete to history). |
| `GET` | `/api/plans/{id}/actuals/history` | Revision history across all records. |
| `GET` | `/api/plans/{id}/report` | Planned-vs-actual report JSON (prefers FINAL; falls back to sum-of-WEEKLY when FINAL absent). |

All endpoints require the plan's owner — cross-tenant access returns 403.

### FR-29 · Planned-vs-actual report

- Per-channel: planned budget / impressions / CPM / net-reach vs actual,
  absolute delta, percentage variance.
- Aggregate: total spend variance, net-reach delta (pp), frequency delta.
- Free-text notes surface at the top.
- Printable HTML view served at `/api/plans/{id}/report.html` (uses same
  server-side template — no JS chart dependency required to print).

### FR-30 · CalibrationProfile (learning loop with decay + confidence)

Rather than collapsing history into a running mean, we keep the raw
observations and compute derived metrics on read — that's what makes
decay tuning and confidence scoring possible.

- **Observation store** (append-only):
  ```
  CalibrationObservation(
      id, client_id, target_id, channel_id,
      metric: MetricKey,              # cpm | penetration | net_reach | frequency | cpm | ...
      value: float,
      observed_at: datetime,          # plan end-date (FINAL) or week end (WEEKLY)
      source_plan_id, source_actuals_id,
      weight_override: Optional[float],  # planner-pinned weight (0..1); else derived
  )
  ```
- **Profile** (derived — materialised view, refreshed on every
  actuals write and on demand):
  ```
  CalibrationProfile(
      client_id, target_id, channel_id, metric,
      value_mean_weighted,    # decayed weighted mean
      value_stdev,            # weighted std
      n_raw,                  # raw observation count
      n_effective,            # sum of weights (Kish-style)
      confidence_score,       # 0..100, see §below
      last_updated,
  )
  ```
- **Decay** — exponential by default:
  `weight = exp(-ln(2) * age_days / half_life_days)`
  where `half_life_days` is a per-scope knob:
    1. global default `180` (≈ 6 months),
    2. overrideable per client (`CalibrationSettings.half_life_days`),
    3. overrideable per `(client × target × channel)` for seasonal channels.
- **Fit-to-actuals knob** — UI exposes a slider for `half_life_days`
  (30 / 90 / 180 / 365 / ∞) plus a "Custom" numeric entry. Changing it
  re-materialises the profile live so the planner can SEE how much the
  weighted mean shifts. A "Reset to global default" button is always
  present.
- **Per-observation weight override** — planners can inspect the list of
  observations behind a profile and pin one (e.g. mark an outlier as
  `weight_override=0` to exclude it, or `weight_override=1` to force
  "always count this"). Overrides survive decay recalculation.
- **Read path** — when `optimizer.compute_*_plan()` pulls channel
  metrics, it uses the profile's `value_mean_weighted` iff
  `n_effective ≥ 1`. Otherwise falls back to `channel_metrics.json`.
- **UI** — calibrated cells get a "CAL" pill. Tap/hover surfaces the
  **confidence score** (FR-30b), `n_raw`, `n_effective`, and a mini
  "last 5 observations" list with their decay weights shown.

### FR-30b · Confidence score

A 0–100 integer shown next to every calibrated cell and on the
Reports page:

- **Inputs**: `n_effective` (decayed sample size) and coefficient of
  variation `cv = stdev / mean`.
- **Formula** (interpretable, easy to QA):
  ```
  sample_factor = 1 - exp(-n_effective / 5)    # saturates around n_eff≈15
  consistency_factor = max(0, 1 - min(cv, 1))  # 0 when cv≥100%, 1 when identical
  confidence = round(100 * (0.6 * sample_factor + 0.4 * consistency_factor))
  ```
  Rationale: sample-size dominates (60% weight) because a very consistent
  single observation is still a single observation.
- **Traffic-light thresholds** (user-adjustable in `CalibrationSettings`):
  - `≥ 70` green "高信心"
  - `40–69` amber "中等，建議再跑一檔"
  - `< 40` red "資料不足，建議用 system default"
- **Display**: badge next to CAL pill, tooltip explains the math in
  plain Chinese so planners trust it rather than treating it as a black
  box.

### FR-31 · Planner-override inputs in the Brief flow

- `Brief` gains an optional `overrides` map:
  ```
  overrides: Dict[str, ChannelOverride] = {}
  ChannelOverride(
      cpm_twd, penetration_pct, net_reach_pct,
      buying_audience_000, impressions,
  )
  ```
- New optional step `CHANNEL_OVERRIDES` surfaced after `CHANNELS`, skippable.
- Per-channel inline-editable table: show the default value (from
  CalibrationProfile if present, else `channel_metrics.json`) in grey,
  planner's override in bold. Clear button restores default.
- `/advance` accepts `overrides` on the payload; changes are persisted in
  the `ConversationTurn.brief_snapshot` so the audit trail is intact.

### FR-32 · "Recommend-fill" affordance

- After the `CHANNELS` step, if for the selected `(client_id, target_id)`
  we have **zero** `CalibrationProfile` rows, the agent emits a
  **sticky yellow banner** (not just a chat bubble — survives re-render)
  that reads:
  > 精準的成效推估，建議一定要填：**Channel Calibration** 與 **Penetration Adjustment**。
  > 填完後系統會記住你這家 client × target 的實際表現，之後預估會越來越準。
- Banner has a "Fill now" CTA that jumps to the new override step, and a
  dismiss-for-session action (not persisted — next session the banner
  returns).

### FR-33 · UI — Actuals tab in Project Detail

- In the Project Detail screen, each saved plan row gets a new action
  "📊 Record actuals".
- The modal has two tabs:
  - **週週補 (Weekly)** — one row per week × channel, planner ticks the
    weeks they want to report, fills numbers, saves. Previously saved
    weeks re-open for edit.
  - **最終結算 (Final)** — single row per channel, same schema.
  A small header toggle flips between them; a planner can use either or
  both. "Aggregate weekly → suggest final" button pre-fills Final from
  the sum/mean of saved weeklies so nobody types twice.
- A new **"Reports"** sub-tab shows the planned-vs-actual bar chart
  (reuses the Chart.js pattern already loaded for Compare) plus a link to
  the printable HTML report. Each chart cell carries the confidence
  badge (FR-30b) pulled from the matching CalibrationProfile.

### FR-34 · Calibration Settings panel

A new "⚙️ Calibration" section inside Project Detail lets the planner
inspect and tune the learning loop without dropping to an admin shell:

- Global defaults (read-only unless admin): `half_life_days`,
  confidence thresholds.
- Per-client overrides: half-life slider + numeric input.
- Per `(client × target × channel)` overrides: expandable list, each
  row shows n_raw / n_effective / confidence / last_updated and a
  "View observations" drawer where individual observations can be
  weight-pinned (FR-30).
- "Reset this row to global" and "Reset all" buttons.

## 4. Non-functional requirements

| Ref | Requirement |
|---|---|
| NFR-7.1 | Schema migration idempotent. New tables: `plan_actuals`, `plan_actuals_history`, `calibration_observations`, `calibration_profiles` (materialised view), `calibration_settings`. Guarded by `schema_version` bump to v3. |
| NFR-7.2 | Parametrised SQL only; `owner_id` filter enforced at the service layer (re-use v4 pattern). |
| NFR-7.3 | Backwards-compatible: existing 139 tests stay green. Absence of actuals must not affect any current flow — the optimizer falls back to survey defaults exactly as today. |
| NFR-7.4 | `CalibrationProfile` writes never block plan saves — use a single transaction, but failures log a warning and do not 500 the PUT /actuals call. |
| NFR-7.5 | No PII in actuals beyond what planners type into `notes`. Same retention rules as `ConversationTurn`. |
| NFR-7.6 | The override UI uses `document.createTextNode` for numeric echo (reuse v5 XSS-safe pattern). |
| NFR-7.7 | Profile materialisation is O(n_obs) per `(client × target × channel × metric)` on write; a full rebuild (triggered when half-life changes) must complete in ≤ 2 s for the ivy dataset (≤ 1k observations). Cache the result, invalidate on settings change. |
| NFR-7.8 | Confidence-score formula + thresholds live in one module so they can be unit-tested and swapped without UI edits. |

## 5. User-visible copy (zh-Hant)

- Banner headline: **"填好這兩項，之後預估會越來越準"**
- Override step intro: **"如果你手上有比 Survey 更新的 CPM / Penetration，填進來可以蓋過系統預設。"**
- Actuals modal tab labels: **"週週補"** / **"最終結算"**
- Aggregate button: **"用週數據試算最終結算"**
- Report title: **"Plan vs Actual · 成效回顧"**
- Variance badge colour: ≤ ±10% green, ±10–25% amber, > ±25% red.
- Confidence badge copy:
  - 高信心 (≥70): **"高信心 · 已累積 N 檔"**
  - 中等 (40–69): **"中等信心 · 建議再跑一檔"**
  - 低 (<40): **"資料不足 · 建議先用 system default"**
- Half-life slider label: **"近期權重（半衰期）"** · tooltip: "數字越小，越早的資料影響越少。"

## 6. TDD suite map

| PRD | TDD suite |
|---|---|
| FR-27 (weekly+final), FR-28, NFR-7.1 | TS-27 actuals CRUD — weekly uniqueness, final uniqueness, history, batch PUT |
| FR-29 | TS-28 report math (variance %, aggregate deltas, FINAL-prefers-over-WEEKLY fallback) |
| FR-30 | TS-29a CalibrationProfile observation→materialisation + optimizer fallback when `n_effective ≥ 1` |
| FR-30 (decay) | TS-29b Exponential decay — mean shifts as half-life changes; planner override survives recompute |
| FR-30b | TS-29c Confidence formula: sample_factor + consistency_factor, threshold bucketing |
| FR-31 | TS-30 Brief override round-trip + persistence in ConversationTurn |
| FR-32 | TS-31 Banner appears iff profile is empty for `(client × target)`; Fill-now jumps to step |
| FR-33 | TS-32 Frontend: record-actuals modal (both tabs, aggregate button) + reports tab with confidence badge |
| FR-34 | TS-33 Calibration Settings panel: half-life slider persists, per-row overrides scope correctly, observation-level weight-pin |

## 7. Acceptance

- `pytest -q` ≥ 175 green (≈36 new tests across TS-27..TS-33).
- Manual walkthrough:
  1. Finish a Manual plan for client X, target A. Save. Banner recommends
     filling Calibration + Penetration (no profile yet).
  2. Open the plan → Record actuals → **週週補** tab → fill week 1+2 →
     save. Profile row created with `n_raw=2`, low-to-mid confidence.
  3. Return, record week 3+4, then click **用週數據試算最終結算** →
     Final tab auto-populates → tweak → save. Profile now has more
     observations, confidence crosses into amber.
  4. Start a NEW session for client X, target A. On `CHANNELS` step,
     banner does **not** appear. Channels table shows "CAL" pill + the
     confidence badge.
  5. Open Calibration Settings → drag half-life from 180 → 30 days →
     observe `value_mean_weighted` in the table update live, confidence
     drops (because effective n shrank). Reset restores.
  6. Open the first plan's Reports tab → planned-vs-actual chart with
     confidence badges + printable HTML render identically in Chrome
     and Safari.
- Delete every observation for `(client X, target A)` → banner reappears
  on next session start. Reversibility confirmed.

## 8. Open questions — resolved

All three v6 open questions are now decided and baked into the FRs above:

| # | Question | Resolution |
|---|---|---|
| 1 | Actuals cadence — one-shot, weekly, or both? | **Both**, planner's choice. FR-27 scope=WEEKLY/FINAL, `period_week` for weekly rows, FR-33 dual-tab modal with aggregate helper. |
| 2 | Time decay on CalibrationProfile? | **Yes**, exponential decay with planner-tunable `half_life_days` + per-observation weight override (FR-30). Slider lets the planner visually fit the curve to actuals before committing. |
| 3 | Confidence score on the UI? | **Yes**, 0–100 score (FR-30b) with interpretable formula, traffic-light thresholds, tooltip that explains the math in plain Chinese. |

New open questions for v7 triage (not blocking v6):

- Automated actuals ingestion from DV360 / Meta Ads Manager — worth it
  at 2-person pilot scale?
- Seasonality priors (CNY / Double-11) layered on top of exponential
  decay?
- Cross-client calibration transfer — can a CPG learning bleed into an
  adjacent CPG client's initial estimate?
