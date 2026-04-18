"""Scoring functions that map metrics to health score."""

from __future__ import annotations

from dataclasses import dataclass

from app.metrics import (
    SprintMetrics,
    aggregate_health_score,
    calculate_bug_ratio,
    calculate_commitment_reliability,
    calculate_cycle_time_stability,
    calculate_carryover_rate,
    score_bug_ratio,
    score_commitment_reliability,
    score_carryover_rate,
)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Per-signal score values and final health score."""

    commitment: int
    carryover: int
    bug_ratio: int
    cycle_time: int
    final_score: int


def _score_commitment(completed_scope: float, committed_scope: float) -> int:
    """Score commitment based on completed/committed scope percentage."""
    if committed_scope <= 0:
        return 70
    pct = (completed_scope / committed_scope) * 100
    if 60 <= pct <= 85:
        return 100
    if pct >= 45:
        return 70
    if pct >= 30:
        return 40
    return 0


def _score_carryover(carryover_scope: float, committed_scope: float) -> int:
    """Score carryover where lower carryover produces higher points."""
    if committed_scope <= 0:
        return 70
    pct = (carryover_scope / committed_scope) * 100
    if pct < 15:
        return 100
    if pct <= 30:
        return 70
    if pct <= 45:
        return 40
    return 0


def _score_bug_ratio(new_bug_ratio_pct: float) -> int:
    """Score bug ratio where lower bug ratio produces higher points."""
    if new_bug_ratio_pct < 15:
        return 100
    if new_bug_ratio_pct <= 25:
        return 70
    if new_bug_ratio_pct <= 35:
        return 40
    return 0


def _score_cycle_time(avg_cycle_time_days: float | None) -> int:
    """Score cycle-time signal with neutral fallback when unavailable."""
    if avg_cycle_time_days is None:
        return 70
    if avg_cycle_time_days <= 3:
        return 100
    if avg_cycle_time_days <= 5:
        return 70
    if avg_cycle_time_days <= 8:
        return 40
    return 0


def calculate_health_score(
    metrics: SprintMetrics,
    *,
    config: dict | None = None,
    previous_cycle_time_days: list[float] | None = None,
    current_avg_cycle_time_days: float | None = None,
    completed_story_count: int | None = None,
) -> ScoreBreakdown:
    """Build weighted final score from sprint metrics with normalized scoring when configured."""
    if config is None:
        commitment = _score_commitment(metrics.completed_scope, metrics.committed_scope)
        carryover = _score_carryover(metrics.carryover_scope, metrics.committed_scope)
        bug_ratio = _score_bug_ratio(metrics.bug_ratio_pct)
        cycle_time = _score_cycle_time(metrics.avg_cycle_time_days)
        final_score = round(
            (commitment * 0.35)
            + (carryover * 0.25)
            + (cycle_time * 0.20)
            + (bug_ratio * 0.20)
        )
        return ScoreBreakdown(
            commitment=commitment,
            carryover=carryover,
            bug_ratio=bug_ratio,
            cycle_time=cycle_time,
            final_score=final_score,
        )

    commitment_pct = calculate_commitment_reliability(metrics.completed_items, metrics.total_items)
    carryover_pct = calculate_carryover_rate(metrics.carryover_scope, metrics.committed_scope)
    bug_ratio_pct = calculate_bug_ratio(
        metrics.new_bug_count,
        completed_story_count if completed_story_count is not None else metrics.completed_items,
    )
    cycle_time_payload = calculate_cycle_time_stability(
        current_avg_cycle_time_days if current_avg_cycle_time_days is not None else metrics.avg_cycle_time_days,
        previous_cycle_time_days,
        config=config,
    )
    commitment = score_commitment_reliability(commitment_pct, config=config)
    carryover = score_carryover_rate(carryover_pct, config=config)
    bug_ratio = score_bug_ratio(bug_ratio_pct, config=config)
    cycle_time = int(cycle_time_payload["score"])
    final_score = aggregate_health_score(
        commitment_score=commitment,
        carryover_score=carryover,
        cycle_time_score=cycle_time,
        bug_score=bug_ratio,
        config=config,
    )
    return ScoreBreakdown(
        commitment=commitment,
        carryover=carryover,
        bug_ratio=bug_ratio,
        cycle_time=cycle_time,
        final_score=final_score,
    )
