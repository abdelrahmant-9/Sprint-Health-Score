"""Application service orchestration for sprint health computations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import logging

from app.config import Settings, load_metrics_config
from app.jira_client import JiraClient
from app.metrics import (
    calculate_daily_activity,
    calculate_metrics,
    calculate_weekly_activity,
    get_current_work_week_range,
    local_day_start_utc,
)
from app.report import build_report_payload, render_html_report
from app.scoring import calculate_health_score

logger = logging.getLogger(__name__)


def calculate_health_snapshot(settings: Settings) -> dict:
    """Calculate and return a normalized sprint health snapshot."""
    client = JiraClient(settings=settings)
    issues, sprint = client.fetch_sprint_issues()

    sprint_start = None
    sprint_start_raw = sprint.get("startDate")
    if sprint_start_raw:
        sprint_start = datetime.fromisoformat(str(sprint_start_raw).replace("Z", "+00:00")).astimezone(timezone.utc)

    metrics = calculate_metrics(issues=issues, sprint_start=sprint_start)
    scores = calculate_health_score(metrics)
    report = build_report_payload(sprint=sprint, metrics=metrics, scores=scores)
    completion_rate = round((metrics.completed_scope / metrics.committed_scope) * 100, 1) if metrics.committed_scope else 0.0
    return {
        "report": report,
        "score": scores.final_score,
        "completion_rate": completion_rate,
        "breakdown": asdict(scores),
    }


def render_health_report_html(settings: Settings) -> str:
    """Calculate and return sprint health report HTML."""
    snapshot = calculate_health_snapshot(settings)
    return render_html_report(snapshot["report"])


def get_daily_activity(settings: Settings) -> dict:
    """Calculate and return today's developer/tester activity summary."""
    client = JiraClient(settings=settings)
    config = load_metrics_config()
    people = config.get("activity_people") or {}
    issues = client.fetch_issues_updated_since(local_day_start_utc(tz_name=settings.report_timezone))
    return calculate_daily_activity(
        issues=issues,
        developer_names=people.get("developer_names") or [],
        tester_names=people.get("qa_names") or [],
        activity_thresholds=config.get("activity_thresholds") or {},
        tz_name=settings.report_timezone,
    )


def get_weekly_activity(settings: Settings) -> dict:
    """Calculate and return the current work-week activity summary."""
    client = JiraClient(settings=settings)
    config = load_metrics_config()
    people = config.get("activity_people") or {}
    week_range = get_current_work_week_range(tz_name=settings.report_timezone)
    issues, _sprint = client.fetch_sprint_issues(include_activity_fields=True)
    logger.info("Weekly issues count: %s", len(issues))
    return calculate_weekly_activity(
        issues=issues,
        developer_names=people.get("developer_names") or [],
        tester_names=people.get("qa_names") or [],
        week_range=week_range,
        tz_name=settings.report_timezone,
    )
