# TDD v3 Test Specification
> From `PRD_v3.md` · 2026-04-17 · Python 3.11 · pytest + FastAPI TestClient

## Overview
Four test suites: TS-14/15/16 drive backend helpers + the enriched compare
endpoint; TS-17 drives the frontend (static-analysis style — we don't spin
up headless Chrome for this pilot).

Existing 55 tests must stay green.

---

## Test Suite 14 (TS-14) — frequency_distribution (FR-10)
File: `tests/test_frequency_distribution.py`

### TC-14.1: returns 10 thresholds (1..10)
- Input: plan with any allocations
- Expected: `len(result) == 10`, `result[i].threshold == i+1`

### TC-14.2: threshold 1+ equals plan net reach
- Input: plan with `summary.net_reach_pct = 45.0`
- Expected: `result[0].reach_pct == 45.0`

### TC-14.3: reach is monotonically non-increasing
- Expected: for all `i < j`, `result[i].reach_pct >= result[j].reach_pct`

### TC-14.4: all values within [0, 100]

### TC-14.5: empty / zero-reach plan returns 10 zeros

---

## Test Suite 15 (TS-15) — duplication_matrix (FR-11)
File: `tests/test_duplication_matrix.py`

### TC-15.1: matrix covers every channel pair
### TC-15.2: `exclusivity_pct + sum(duplications_with_others) <= net_reach_pct`
### TC-15.3: symmetry — dupe(A,B) == dupe(B,A)
### TC-15.4: single-channel plan → exclusivity = reach, no duplication
### TC-15.5: all values within [0, 100]

---

## Test Suite 16 (TS-16) — enriched /api/plans/compare (FR-12)
File: `tests/test_compare_endpoint.py`

### TC-16.1: response includes frequency_distribution per plan
### TC-16.2: response includes duplication matrix per plan
### TC-16.3: response includes weekly_grp list per plan
### TC-16.4: backward-compat — old fields (plans, delta) still present
### TC-16.5: 400 when fewer than 2 plan ids supplied

---

## Test Suite 17 (TS-17) — frontend compare view (FR-13, FR-14, NFR-4)
File: `tests/test_compare_ui.py`

Static-analysis style — we grep app.js / index.html.

### TC-17.1: Chart.js CDN URL present in frontend code
### TC-17.2: a renderCompare() function exists
### TC-17.3: picker modal scaffolding exists (checkbox list + confirm CTA)
### TC-17.4: five canvas target IDs referenced
### TC-17.5: graceful-degradation path (try/catch around Chart load)
### TC-17.6: palette of 6 colours declared

---

## Exit criteria
- 65+ tests pass.
- Server running + happy-path manual test: visit /, finish 2 plans, click
  Compare, see 5 charts + duplication table.
- `curl /api/plans/compare` response includes `frequency_distribution` and
  `duplication` under each plan.
