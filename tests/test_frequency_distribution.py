"""TS-14 · optimizer.frequency_distribution (FR-10)."""
from __future__ import annotations

from app.schemas import ChannelAllocation, PerformanceSummary, Plan, PlanKind, WeekAllocation
from app.services import optimizer


def _plan_with(reach: float, freq: float) -> Plan:
    """Tiny plan harness with the two summary fields FR-10 actually reads."""
    return Plan(
        brief_id="b",
        name="T",
        kind=PlanKind.MANUAL,
        allocations=[],
        summary=PerformanceSummary(net_reach_pct=reach, frequency=freq),
    )


def test_returns_ten_thresholds():
    result = optimizer.frequency_distribution(_plan_with(60, 3))
    assert len(result) == 10
    assert [r["threshold"] for r in result] == list(range(1, 11))


def test_threshold_1_equals_net_reach():
    result = optimizer.frequency_distribution(_plan_with(45.0, 2.5))
    assert result[0]["reach_pct"] == 45.0


def test_reach_is_monotonically_non_increasing():
    result = optimizer.frequency_distribution(_plan_with(80, 4))
    values = [r["reach_pct"] for r in result]
    for a, b in zip(values, values[1:]):
        assert b <= a + 1e-6, f"regression: {a} → {b}"


def test_values_clamped_within_0_100():
    for reach in (0, 25, 50, 90, 100):
        for freq in (0.1, 1, 2, 5, 20):
            result = optimizer.frequency_distribution(_plan_with(reach, freq))
            for r in result:
                assert 0 <= r["reach_pct"] <= 100


def test_zero_reach_returns_zeros():
    result = optimizer.frequency_distribution(_plan_with(0, 0))
    assert all(r["reach_pct"] == 0 for r in result)
