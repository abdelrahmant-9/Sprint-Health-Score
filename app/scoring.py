"""Scoring functions that map metrics to health score."""

from __future__ import annotations

from dataclasses import dataclass

from app.metrics import SprintMetrics


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


def calculate_health_score(metrics: SprintMetrics) -> ScoreBreakdown:
    """Build weighted final score from sprint metrics."""
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
