"""Metric calculations for sprint health reporting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.utils import is_effectively_done_status, issue_weight, parse_jira_datetime


@dataclass(frozen=True)
class SprintMetrics:
    """Derived metric values used by scoring."""

    total_items: int
    completed_items: int
    carried_over_items: int
    committed_scope: float
    completed_scope: float
    carryover_scope: float
    bug_count: int
    new_bug_count: int
    bug_ratio_pct: float
    avg_cycle_time_days: float | None


def load_metrics_config(path: Path) -> dict:
    """Load metrics configuration from disk."""
    if not path.exists():
        raise FileNotFoundError(f"Metrics config not found: {path}")
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def calculate_cycle_time_days(created: str | None, resolved: str | None) -> float | None:
    """Calculate cycle time in days from created and resolved datetimes."""
    created_dt = parse_jira_datetime(created)
    resolved_dt = parse_jira_datetime(resolved)
    if not created_dt or not resolved_dt:
        return None
    duration = resolved_dt - created_dt
    return max(0.0, duration.total_seconds() / 86400)


def calculate_metrics(issues: list[dict], sprint_start: datetime | None) -> SprintMetrics:
    """Compute normalized sprint metrics from Jira issue payload."""
    total_items = len(issues)
    completed_items = 0
    bug_count = 0
    new_bug_count = 0
    committed_scope = 0.0
    completed_scope = 0.0
    cycle_times: list[float] = []

    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        is_done = is_effectively_done_status(status_name, issue_type)
        weight = issue_weight(issue)

        committed_scope += weight
        if is_done:
            completed_items += 1
            completed_scope += weight

        if issue_type.lower() in {"bug", "feature bug"}:
            bug_count += 1
            created_dt = parse_jira_datetime(fields.get("created"))
            if sprint_start and created_dt and created_dt >= sprint_start.astimezone(timezone.utc):
                new_bug_count += 1

        if issue_type.lower() == "story" and is_done:
            cycle_time = calculate_cycle_time_days(fields.get("created"), fields.get("resolutiondate"))
            if cycle_time is not None:
                cycle_times.append(cycle_time)

    carried_over_items = max(0, total_items - completed_items)
    carryover_scope = max(0.0, committed_scope - completed_scope)
    denominator = committed_scope if committed_scope > 0 else 1.0
    bug_ratio_pct = round((new_bug_count / denominator) * 100, 1)
    avg_cycle = round(sum(cycle_times) / len(cycle_times), 2) if cycle_times else None

    return SprintMetrics(
        total_items=total_items,
        completed_items=completed_items,
        carried_over_items=carried_over_items,
        committed_scope=round(committed_scope, 2),
        completed_scope=round(completed_scope, 2),
        carryover_scope=round(carryover_scope, 2),
        bug_count=bug_count,
        new_bug_count=new_bug_count,
        bug_ratio_pct=bug_ratio_pct,
        avg_cycle_time_days=avg_cycle,
    )
