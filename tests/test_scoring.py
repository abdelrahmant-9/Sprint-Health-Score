"""Unit tests for scoring behavior."""

from __future__ import annotations

from app.metrics import SprintMetrics
from app.scoring import calculate_health_score


def test_calculate_health_score_returns_weighted_result() -> None:
    metrics = SprintMetrics(
        total_items=10,
        completed_items=7,
        carried_over_items=3,
        committed_scope=20.0,
        completed_scope=14.0,
        carryover_scope=6.0,
        bug_count=3,
        new_bug_count=2,
        bug_ratio_pct=10.0,
        avg_cycle_time_days=3.0,
    )

    result = calculate_health_score(metrics)

    assert result.commitment == 100
    assert result.carryover == 70
    assert result.bug_ratio == 100
    assert result.cycle_time == 100
    assert result.final_score == 92
