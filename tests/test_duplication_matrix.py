"""TS-15 · optimizer.duplication_matrix (FR-11)."""
from __future__ import annotations

from app.schemas import ChannelAllocation, PerformanceSummary, Plan, PlanKind
from app.services import optimizer


def _alloc(channel_id: str, reach: float) -> ChannelAllocation:
    return ChannelAllocation(channel_id=channel_id, net_reach_pct=reach)


def _plan(*allocs: ChannelAllocation) -> Plan:
    summary = PerformanceSummary(
        net_reach_pct=sum(a.net_reach_pct for a in allocs),
    )
    return Plan(brief_id="b", name="T", kind=PlanKind.MANUAL,
                allocations=list(allocs), summary=summary)


def test_covers_every_channel():
    p = _plan(_alloc("tv", 60), _alloc("yt", 50), _alloc("meta", 40))
    m = optimizer.duplication_matrix(p)
    assert set(m.keys()) == {"tv", "yt", "meta"}


def test_values_within_0_100():
    p = _plan(_alloc("tv", 90), _alloc("yt", 85), _alloc("meta", 80))
    m = optimizer.duplication_matrix(p)
    for cid, row in m.items():
        assert 0 <= row["duplication_pct"] <= 100
        assert 0 <= row["exclusivity_pct"] <= 100
        for partner, dupe in row["pairwise"].items():
            assert 0 <= dupe <= 100


def test_pairwise_is_symmetric():
    p = _plan(_alloc("tv", 70), _alloc("yt", 40))
    m = optimizer.duplication_matrix(p)
    assert m["tv"]["pairwise"]["yt"] == m["yt"]["pairwise"]["tv"]


def test_single_channel_plan_has_no_duplication():
    p = _plan(_alloc("tv", 55))
    m = optimizer.duplication_matrix(p)
    assert m["tv"]["duplication_pct"] == 0.0
    assert m["tv"]["exclusivity_pct"] == 55.0


def test_exclusivity_never_exceeds_channel_reach():
    p = _plan(_alloc("tv", 60), _alloc("yt", 50), _alloc("meta", 40))
    m = optimizer.duplication_matrix(p)
    assert m["tv"]["exclusivity_pct"] <= 60
    assert m["yt"]["exclusivity_pct"] <= 50
    assert m["meta"]["exclusivity_pct"] <= 40
