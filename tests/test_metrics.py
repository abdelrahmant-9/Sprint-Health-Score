"""Unit tests for metrics calculations."""

from __future__ import annotations

from datetime import datetime, timezone

from app.metrics import calculate_metrics


def _issue(
    key: str,
    issue_type: str,
    status: str,
    created: str,
    resolutiondate: str | None,
    story_points: float | None,
) -> dict:
    return {
        "key": key,
        "fields": {
            "issuetype": {"name": issue_type},
            "status": {"name": status},
            "created": created,
            "resolutiondate": resolutiondate,
            "customfield_10016": story_points,
        },
    }


def test_calculate_metrics_counts_scope_and_cycle_time() -> None:
    sprint_start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    issues = [
        _issue("PM-1", "Story", "Done", "2026-04-02T10:00:00.000+0000", "2026-04-04T10:00:00.000+0000", 5),
        _issue("PM-2", "Story", "In Progress", "2026-04-03T10:00:00.000+0000", None, 3),
        _issue("PM-3", "Bug", "To Do", "2026-04-05T10:00:00.000+0000", None, None),
    ]

    metrics = calculate_metrics(issues=issues, sprint_start=sprint_start)

    assert metrics.total_items == 3
    assert metrics.completed_items == 1
    assert metrics.carried_over_items == 2
    assert metrics.committed_scope == 9.0
    assert metrics.completed_scope == 5.0
    assert metrics.carryover_scope == 4.0
    assert metrics.new_bug_count == 1
    assert metrics.bug_count == 1
    assert metrics.bug_ratio_pct == 11.1
    assert metrics.avg_cycle_time_days == 2.0
