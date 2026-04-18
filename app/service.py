"""Application service orchestration for sprint health computations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import logging

from app.config import Settings, load_metrics_config
from app.jira_client import JiraClient
from app.metrics import (
    apply_metric_overrides,
    build_sprint_health_payload,
    build_sprint_summary,
    calculate_advanced_sprint_metrics,
    calculate_daily_activity,
    calculate_metrics,
    calculate_weekly_activity,
    generate_sprint_insights,
    get_current_work_week_range,
    list_metric_rows,
    predict_next_sprint_health,
    set_override_in_db,
    local_day_start_utc,
)
from app.report import build_report_payload, render_html_report
from app.scoring import calculate_health_score
from app.storage import list_recent_reports

logger = logging.getLogger(__name__)


def _build_sprint_metrics(settings: Settings, *, apply_overrides_to_result: bool = True):
    """Fetch Jira issues and compute sprint metrics with optional DB overrides."""
    client = JiraClient(settings=settings)
    issues, sprint = client.fetch_sprint_issues(include_activity_fields=True)

    sprint_start = _parse_sprint_datetime(sprint.get("startDate"))
    sprint_end = _parse_sprint_datetime(sprint.get("endDate"))

    base_metrics = calculate_metrics(issues=issues, sprint_start=sprint_start)
    metrics = apply_metric_overrides(base_metrics, settings.sqlite_path) if apply_overrides_to_result else base_metrics
    return metrics, base_metrics, issues, sprint, sprint_start, sprint_end


def _parse_sprint_datetime(raw_value: str | None) -> datetime | None:
    """Parse an ISO sprint datetime into UTC."""
    if not raw_value:
        return None
    return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _build_historical_snapshots(settings: Settings, current_sprint_id: int | None) -> list[dict]:
    """Return recent unique sprint snapshots excluding the active sprint."""
    rows = list_recent_reports(settings.sqlite_path, limit=20)
    history: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        report = row.get("report") or {}
        sprint = report.get("sprint") or {}
        sprint_id = row.get("sprint_id") or sprint.get("id")
        sprint_name = row.get("sprint_name") or sprint.get("name") or f"sprint-{row.get('id')}"
        dedupe_key = str(sprint_id or sprint_name)
        if current_sprint_id is not None and sprint_id == current_sprint_id:
            continue
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        analytics = report.get("analytics") or {}
        scores = report.get("scores") or {}
        history.append(
            {
                "created_at": row.get("created_at"),
                "sprint_id": sprint_id,
                "sprint_name": sprint_name,
                "health_score": int(analytics.get("health_score", scores.get("final_score", row.get("score", 0))) or 0),
                "commitment_score": int(analytics.get("commitment_score", scores.get("commitment", 0)) or 0),
                "carryover_score": int(analytics.get("carryover_score", scores.get("carryover", 0)) or 0),
                "cycle_time_score": int(analytics.get("cycle_time_score", scores.get("cycle_time", 0)) or 0),
                "bug_score": int(analytics.get("bug_score", scores.get("bug_ratio", 0)) or 0),
                "metrics": report.get("metrics") or {},
            }
        )
    return history


def _build_history_series(current_sprint: dict, current_payload: dict, history: list[dict]) -> list[dict]:
    """Build a chronological history series for dashboard trend charts."""
    series = [
        {
            "created_at": current_payload.get("generated_at"),
            "sprint_id": current_sprint.get("id"),
            "sprint_name": current_sprint.get("name"),
            "health_score": int(current_payload.get("health_score", 0) or 0),
            "commitment_score": int(current_payload.get("commitment_score", 0) or 0),
            "carryover_score": int(current_payload.get("carryover_score", 0) or 0),
            "cycle_time_score": int(current_payload.get("cycle_time_score", 0) or 0),
            "bug_score": int(current_payload.get("bug_score", 0) or 0),
        }
    ]
    series.extend(history[:5])
    return list(reversed(series))


def calculate_health_snapshot(settings: Settings) -> dict:
    """Calculate and return a normalized sprint health snapshot."""
    config = load_metrics_config()
    metrics, _base_metrics, issues, sprint, sprint_start, sprint_end = _build_sprint_metrics(
        settings,
        apply_overrides_to_result=True,
    )
    historical_snapshots = _build_historical_snapshots(settings, sprint.get("id"))
    advanced_metrics = calculate_advanced_sprint_metrics(
        issues,
        sprint_start=sprint_start,
        sprint_end=sprint_end,
    )
    health_payload = build_sprint_health_payload(
        metrics,
        advanced_metrics=advanced_metrics,
        historical_snapshots=historical_snapshots,
        config=config,
    )
    scores = calculate_health_score(
        metrics,
        config=config,
        previous_cycle_time_days=[
            float(item["metrics"].get("avg_cycle_time_days"))
            for item in historical_snapshots
            if isinstance(item.get("metrics"), dict) and item["metrics"].get("avg_cycle_time_days") is not None
        ],
        current_avg_cycle_time_days=health_payload["cycle_time"].get("current_avg"),
        completed_story_count=int(advanced_metrics.get("completed_story_count", 0) or 0),
    )
    insights = generate_sprint_insights(health_payload)
    health_payload["insights"] = insights
    summary = build_sprint_summary(health_payload)
    prediction_input = [
        {
            "health_score": int(health_payload["health_score"]),
            "commitment_score": int(health_payload["commitment_score"]),
            "carryover_score": int(health_payload["carryover_score"]),
            "cycle_time_score": int(health_payload["cycle_time_score"]),
            "bug_score": int(health_payload["bug_score"]),
        },
        *historical_snapshots,
    ]
    prediction = predict_next_sprint_health(prediction_input[:5])
    history = _build_history_series(sprint, health_payload, historical_snapshots)
    analytics_payload = {
        **health_payload,
        "summary": summary,
        "prediction": prediction,
        "history": history,
    }
    report = build_report_payload(sprint=sprint, metrics=metrics, scores=scores, analytics=analytics_payload)
    completion_rate = round((metrics.completed_scope / metrics.committed_scope) * 100, 1) if metrics.committed_scope else 0.0
    return {
        "report": report,
        "score": scores.final_score,
        "health_score": int(health_payload["health_score"]),
        "health_status": health_payload["health_status"],
        "completion_rate": completion_rate,
        "breakdown": asdict(scores),
        "commitment_score": int(health_payload["commitment_score"]),
        "carryover_score": int(health_payload["carryover_score"]),
        "cycle_time_score": int(health_payload["cycle_time_score"]),
        "bug_score": int(health_payload["bug_score"]),
        "inputs": health_payload["inputs"],
        "cycle_time": health_payload["cycle_time"],
        "blocked_ratio": float(health_payload["blocked_ratio"]),
        "bugs": health_payload["bugs"],
        "insights": insights,
        "summary": summary,
        "prediction": prediction,
        "history": history,
    }


def render_health_report_html(settings: Settings) -> str:
    """Calculate and return sprint health report HTML."""
    snapshot = calculate_health_snapshot(settings)
    return render_html_report(snapshot["report"])


def get_metrics_catalog(settings: Settings) -> list[dict]:
    """Return editable sprint metrics with their base and override values."""
    _metrics, base_metrics, _issues, _sprint, _start, _end = _build_sprint_metrics(
        settings,
        apply_overrides_to_result=False,
    )
    return list_metric_rows(base_metrics, settings.sqlite_path)


def update_metric_override(settings: Settings, metric_name: str, value: float) -> dict:
    """Persist a metric override and return the updated metric row."""
    _metrics, base_metrics, _issues, _sprint, _start, _end = _build_sprint_metrics(
        settings,
        apply_overrides_to_result=False,
    )
    metric_names = {row["metric_name"] for row in list_metric_rows(base_metrics)}
    if metric_name not in metric_names:
        raise ValueError(f"Unknown metric: {metric_name}")
    set_override_in_db(settings.sqlite_path, metric_name, value)
    refreshed_rows = list_metric_rows(base_metrics, settings.sqlite_path)
    for row in refreshed_rows:
        if row["metric_name"] == metric_name:
            return row
    raise ValueError(f"Unknown metric: {metric_name}")


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
