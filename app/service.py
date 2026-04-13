"""Application service orchestration for sprint health computations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from app.config import Settings
from app.jira_client import JiraClient
from app.metrics import calculate_metrics
from app.report import build_report_payload, render_html_report
from app.scoring import calculate_health_score


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
