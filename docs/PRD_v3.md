# CCS Planner Agent · PRD v3 — Plan Comparison & Visualization

> Adds the visualisation layer deferred in v1/v2.
> Date: 2026-04-17 · Owner: Dentsu PM team.

## 1. Problem
PMs can now produce Plan 1 (Manual) and Plan 2 (Automatic) against the
same Brief (v1/v2), but the only way to see a comparison is to read the
raw `/api/plans/compare` JSON. The video shows CCS Planner offers a rich
Compare-plans view with budget splits, reach curves, frequency
distribution, and brand-effect bars. v3 reproduces that view inside our
agent.

## 2. Goals
1. A **Compare plans** entry point reachable from the Review step and
   from the right-hand sidebar whenever ≥ 2 plans exist for the current
   brief.
2. A plan picker that lets the user choose 2+ plans (not just Plan 1 vs
   Plan 2).
3. Five charts rendered with **Chart.js** (loaded from CDN — no build
   step, no npm dependency):
   - C1 **Performance Summary** matrix (budget, impressions, GRP, net
     reach, frequency, brand KPIs) side-by-side.
   - C2 **Budget per channel** — stacked horizontal bars, one row per
     plan, segments per channel.
   - C3 **Reach / Attentive / Engagement** grouped bar chart.
   - C4 **Frequency distribution** — reach at 1+/2+/…/10+ multi-line
     chart (FR-10 backend helper).
   - C5 **Weekly GRP trend** — multi-line, one line per plan.
4. A **Duplication & Exclusivity** table derived from the backend helper
   (FR-11).

## 3. Functional requirements

### FR-10 · Frequency distribution helper
`optimizer.frequency_distribution(plan)` returns a list of
`{threshold: int, reach_pct: float}` for thresholds 1..10. Uses the
Beta-binomial approximation `reach_n+ = R × (1 - Beta_cdf(n/freq))`
simplified to the geometric decay `R × decay^(n-1)` where `decay = 1 -
1/freq`. Values clamped to [0, 100].

### FR-11 · Duplication & exclusivity helper
`optimizer.duplication_matrix(plan)` returns a dict keyed by channel id
with `{duplication_pct, exclusivity_pct}` against every other channel in
the plan. Formulas:
- `duplication = min(r_i, r_j) / max(r_i, r_j) * overlap_factor` with
  overlap_factor=0.2 (demo heuristic)
- `exclusivity = reach_i - duplication` clamped to [0, reach_i]

### FR-12 · Enriched `/api/plans/compare`
Response now includes, per plan:
- `frequency_distribution` — output of FR-10
- `duplication` — output of FR-11
- `weekly_grp` — `[{"week": n, "grp": x}]` rolled up across channels
All existing fields stay for backward compatibility.

### FR-13 · Plan picker
`GET /api/plans?brief_id=<id>` (already exists) feeds the picker.
Frontend: modal with checkbox list, CTA disabled until ≥ 2 selected.

### FR-14 · Chart rendering
Five `<canvas>` elements are created on demand when the user clicks
"Compare plans". Chart.js is loaded lazily (only when needed) from
`https://cdn.jsdelivr.net/npm/chart.js@4.4.1/+esm`.

## 4. Non-functional requirements

| Ref | Requirement |
|---|---|
| NFR-4.1 | Chart.js CDN load failure MUST degrade gracefully — show a "charts unavailable" notice but still render the tables. |
| NFR-4.2 | Compare view MUST be keyboard-accessible (modal dismissable with Esc, buttons focusable). |
| NFR-4.3 | All chart colours MUST come from a 6-colour palette repeated modularly so deterministic channel→colour mapping across charts. |
| NFR-4.4 | Rendering MUST handle 10-channel plans without browser freeze (<100 ms per chart). |

## 5. Tracking ID map

| PRD | TDD suite |
|---|---|
| FR-10 | TS-14 frequency_distribution |
| FR-11 | TS-15 duplication_matrix |
| FR-12 | TS-16 enriched compare endpoint |
| FR-13, FR-14, NFR-4.* | TS-17 frontend compare view |

## 6. Acceptance
- `pytest -q` ≥ 65 tests green.
- Starting the server + opening the UI: after finishing Plan 1 and Plan 2
  for the same Brief, the "Compare plans" button renders five charts
  populated with real data from the API.
- Mocking a CDN failure (blocking the Chart.js URL) still renders the
  tables.
