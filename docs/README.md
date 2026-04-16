# /docs

Living design artefacts for `ccs-planner-agent`.

| File | Purpose |
|---|---|
| [`PRD.md`](PRD.md) | **Iteration 1** · Brief / Plan / Fork + security hardening (FR-1..6, NFR-1, NFR-2). |
| [`TDD.md`](TDD.md) | Iteration 1 test suites TS-1..TS-10. |
| [`PRD_v2.md`](PRD_v2.md) | **Iteration 2** · Deployment readiness — auth, rate limit, retention (FR-7..9, NFR-3). |
| [`TDD_v2.md`](TDD_v2.md) | Iteration 2 test suites TS-11..TS-13. |
| [`PRD_v3.md`](PRD_v3.md) | **Iteration 3** · Plan Comparison UI with Chart.js + freq distribution + duplication matrix (FR-10..14, NFR-4). |
| [`TDD_v3.md`](TDD_v3.md) | Iteration 3 test suites TS-14..TS-17. |

Both are versioned with the code. When you add a feature:

1. Update `PRD.md` with the new FR/NFR entry.
2. Add a Test Suite in `TDD.md` that references that entry.
3. Follow TDD: write the failing test (RED) → minimum implementation (GREEN)
   → refactor.
4. Cross-link the test file in `README.md`'s Security / Features table.

## Traceability matrix

| PRD entry | TDD suite | Test file |
|---|---|---|
| FR-1, NFR-1.3 | TS-1 | [`tests/test_input_limits.py`](../tests/test_input_limits.py) |
| FR-2 | TS-2 | [`tests/test_agent_manual.py`](../tests/test_agent_manual.py) |
| FR-3 | TS-3 | [`tests/test_agent_automatic.py`](../tests/test_agent_automatic.py) |
| FR-4 | TS-4 | [`tests/test_fork_handoff.py`](../tests/test_fork_handoff.py) |
| FR-5 | TS-5 | [`tests/test_api_http.py`](../tests/test_api_http.py) |
| FR-6 | TS-6 | [`tests/test_survey_validation.py`](../tests/test_survey_validation.py) |
| NFR-1.1 | TS-7 | [`tests/test_static_safety.py`](../tests/test_static_safety.py) |
| NFR-1.2 | TS-8 | [`tests/test_cors_guard.py`](../tests/test_cors_guard.py) |
| NFR-1.4 | TS-9 | [`tests/test_frontend_escape.py`](../tests/test_frontend_escape.py) |
| NFR-2 | TS-10 | [`tests/test_storage_locking.py`](../tests/test_storage_locking.py) |
| FR-7, NFR-3.2 | TS-11 | [`tests/test_auth_guard.py`](../tests/test_auth_guard.py) |
| FR-8 | TS-12 | [`tests/test_rate_limit.py`](../tests/test_rate_limit.py) |
| FR-9, NFR-3.4 | TS-13 | [`tests/test_retention.py`](../tests/test_retention.py) |
| FR-10 | TS-14 | [`tests/test_frequency_distribution.py`](../tests/test_frequency_distribution.py) |
| FR-11 | TS-15 | [`tests/test_duplication_matrix.py`](../tests/test_duplication_matrix.py) |
| FR-12 | TS-16 | [`tests/test_compare_endpoint.py`](../tests/test_compare_endpoint.py) |
| FR-13, FR-14, NFR-4 | TS-17 | [`tests/test_compare_ui.py`](../tests/test_compare_ui.py) |
