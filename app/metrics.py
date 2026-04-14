"""Metric calculations for sprint health reporting."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from app.utils import (
    get_timezone,
    is_effectively_done_status,
    is_tester_verified_status,
    issue_weight,
    parse_jira_datetime,
)

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class DeveloperActivity:
    """Daily developer activity summary."""

    name: str
    tasks: int
    completed: int


@dataclass(frozen=True)
class TesterActivity:
    """Daily tester activity summary."""

    name: str
    bugs_logged: int
    bugs_closed: int


BUGS_TODAY_WARNING_THRESHOLD = 5
LOW_COMPLETED_TASKS_THRESHOLD = 2
WORK_WEEK_DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]


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


def _display_name(user_payload: dict | None) -> str:
    """Extract a human-friendly user name from a Jira user payload."""
    if not isinstance(user_payload, dict):
        return ""
    return str(
        user_payload.get("displayName")
        or user_payload.get("name")
        or user_payload.get("emailAddress")
        or ""
    ).strip()


def _normalize_people(names: list[str] | tuple[str, ...] | set[str] | None) -> dict[str, str]:
    """Map lowercase names to their preferred display form."""
    normalized: dict[str, str] = {}
    for name in names or []:
        clean = str(name).strip()
        if clean:
            normalized[clean.casefold()] = clean
    return normalized


def _derive_people_from_issues(issues: list[dict], field_name: str) -> dict[str, str]:
    """Build a people map from Jira issue user fields when config lists are empty."""
    derived: dict[str, str] = {}
    for issue in issues:
        fields = issue.get("fields", {})
        display_name = _display_name(fields.get(field_name))
        if display_name:
            derived[display_name.casefold()] = display_name
    return derived


def _safe_local_date(value: datetime | None, tz_name: str) -> date | None:
    """Convert a datetime to the configured local date safely."""
    if value is None:
        return None
    tz = get_timezone(tz_name)
    return value.astimezone(tz).date()


def _to_utc(value: datetime | None) -> datetime | None:
    """Normalize a datetime to UTC safely."""
    if value is None:
        return None
    return value.astimezone(timezone.utc)


def _is_same_local_day(value: datetime | None, today: date, tz_name: str) -> bool:
    """Return True when a datetime falls on the requested local day."""
    return _safe_local_date(value, tz_name) == today


def _normalize_issue_type(issue_type: str) -> str:
    """Return normalized issue type name."""
    return issue_type.strip().lower()


def _iter_meaningful_changes_for_day(issue: dict, today: date, tz_name: str) -> list[dict]:
    """Return status and assignment transitions that happened on the requested local day."""
    changes: list[dict] = []
    changelog = issue.get("changelog") or {}
    histories = changelog.get("histories") or []
    for history in histories:
        history_dt = parse_jira_datetime(history.get("created"))
        if not _is_same_local_day(history_dt, today, tz_name):
            continue
        actor = _display_name(history.get("author"))
        for item in history.get("items") or []:
            field_name = (item.get("field") or "").strip().lower()
            if field_name not in {"status", "assignee"}:
                continue
            changes.append(
                {
                    "field": field_name,
                    "from": str(item.get("fromString") or ""),
                    "to": str(item.get("toString") or ""),
                    "datetime": history_dt,
                    "actor": actor,
                }
            )
    return changes


def _iter_meaningful_changes_between(issue: dict, start_utc: datetime, end_utc: datetime) -> list[dict]:
    """Return status and assignment transitions that happened within a UTC range."""
    changes: list[dict] = []
    changelog = issue.get("changelog") or {}
    histories = changelog.get("histories") or []
    for history in histories:
        history_dt = _to_utc(parse_jira_datetime(history.get("created")))
        if history_dt is None or not (start_utc <= history_dt < end_utc):
            continue
        actor = _display_name(history.get("author"))
        for item in history.get("items") or []:
            field_name = (item.get("field") or "").strip().lower()
            if field_name not in {"status", "assignee"}:
                continue
            changes.append(
                {
                    "field": field_name,
                    "from": str(item.get("fromString") or ""),
                    "to": str(item.get("toString") or ""),
                    "datetime": history_dt,
                    "actor": actor,
                }
            )
    return changes


def _is_work_issue(issue_type: str) -> bool:
    """Return True when the issue type should count toward developer task activity."""
    return _normalize_issue_type(issue_type) in {"task", "story"}


def _pick_known_person(*candidates: str, people: dict[str, str]) -> str | None:
    """Return the first mapped person name from the provided candidates."""
    for candidate in candidates:
        clean = str(candidate or "").strip()
        if not clean:
            continue
        mapped = people.get(clean.casefold())
        if mapped:
            return mapped
    return None


def _build_top_performer(rows: list[dict], value_key: str, output_key: str) -> dict:
    """Return top performer payload or an empty placeholder."""
    if not rows:
        return {"name": "", output_key: 0}
    best = max(rows, key=lambda row: (int(row.get(value_key, 0)), -rows.index(row)))
    return {"name": str(best.get("name") or ""), output_key: int(best.get(value_key, 0))}


def _build_activity_insights(
    developers: list[dict],
    testers: list[dict],
    bugs_today: int,
    top_developer: dict,
    top_tester: dict,
    *,
    bug_warning_threshold: int = BUGS_TODAY_WARNING_THRESHOLD,
    low_completed_threshold: int = LOW_COMPLETED_TASKS_THRESHOLD,
) -> list[str]:
    """Generate lightweight activity insights and warnings."""
    insights: list[str] = []
    total_completed = sum(int(row.get("completed", 0)) for row in developers)
    total_worked = sum(int(row.get("tasks", 0)) for row in developers)
    total_bugs_closed = sum(int(row.get("bugs_closed", 0)) for row in testers)
    total_bugs_logged = sum(int(row.get("bugs_logged", 0)) for row in testers)

    if bugs_today > bug_warning_threshold:
        insights.append(f"High bug creation detected today ({bugs_today} bugs).")
    elif bugs_today > 0:
        insights.append(f"{bugs_today} new bug{'s' if bugs_today != 1 else ''} created today.")

    if total_completed < low_completed_threshold:
        insights.append("Low completed task volume detected today.")

    if total_bugs_closed == 0:
        insights.append("No tester verification activity detected today.")
    elif total_bugs_closed < max(1, total_bugs_logged):
        insights.append("Testing throughput is trailing bug intake today.")

    if top_developer.get("name") and int(top_developer.get("completed", 0)) > 0:
        insights.append(
            f"Top performer today: {top_developer['name']} ({int(top_developer['completed'])} tasks completed)."
        )

    if top_tester.get("name") and int(top_tester.get("bugs_closed", 0)) > 0:
        insights.append(
            f"Top tester today: {top_tester['name']} ({int(top_tester['bugs_closed'])} bugs closed)."
        )

    incomplete_work = max(0, total_worked - total_completed)
    if incomplete_work >= 3:
        insights.append("High carryover risk based on incomplete work still in progress today.")

    if not insights:
        insights.append("Balanced activity detected today with no immediate risks flagged.")

    return insights


def calculate_daily_activity(
    issues: list[dict],
    developer_names: list[str] | None = None,
    tester_names: list[str] | None = None,
    activity_thresholds: dict | None = None,
    *,
    today: date | None = None,
    tz_name: str = "UTC",
) -> dict:
    """Compute daily developer/tester activity from Jira issues updated today."""
    thresholds = activity_thresholds or {}
    local_today = today or datetime.now(get_timezone(tz_name)).date()
    developers = _normalize_people(developer_names)
    testers = _normalize_people(tester_names)

    developer_worked: dict[str, set[str]] = {name: set() for name in developers.values()}
    developer_completed: dict[str, set[str]] = {name: set() for name in developers.values()}
    tester_logged: dict[str, set[str]] = {name: set() for name in testers.values()}
    tester_closed: dict[str, set[str]] = {name: set() for name in testers.values()}
    bugs_today_keys: set[str] = set()

    for issue in issues:
        fields = issue.get("fields", {})
        issue_key = str(issue.get("key") or "")
        if not issue_key:
            continue
        issue_type = str(((fields.get("issuetype") or {}).get("name")) or "").strip()
        assignee_name = _display_name(fields.get("assignee"))
        reporter_name = _display_name(fields.get("reporter"))
        created_dt = parse_jira_datetime(fields.get("created"))
        resolution_dt = parse_jira_datetime(fields.get("resolutiondate"))
        current_status = str(((fields.get("status") or {}).get("name")) or "").strip()
        created_today = _is_same_local_day(created_dt, local_today, tz_name)

        is_bug = _normalize_issue_type(issue_type) == "bug"
        if is_bug and created_today:
            bugs_today_keys.add(issue_key)
            mapped_reporter = _pick_known_person(reporter_name, assignee_name, people=testers)
            if mapped_reporter:
                tester_logged[mapped_reporter].add(issue_key)

        changes = _iter_meaningful_changes_for_day(issue, local_today, tz_name)
        assignee_dev = _pick_known_person(assignee_name, people=developers)
        assignee_tester = _pick_known_person(assignee_name, people=testers)

        if created_today and _is_work_issue(issue_type) and assignee_dev:
            developer_worked[assignee_dev].add(issue_key)

        for change in changes:
            actor_name = str(change["actor"]).strip()
            to_value = str(change["to"]).strip()

            if str(change["field"]) == "assignee":
                mapped_dev = _pick_known_person(to_value, actor_name, assignee_name, people=developers)
                if _is_work_issue(issue_type) and mapped_dev:
                    developer_worked[mapped_dev].add(issue_key)
                continue

            mapped_dev = _pick_known_person(actor_name, assignee_name, people=developers)
            mapped_tester = _pick_known_person(actor_name, assignee_name, people=testers)

            if _is_work_issue(issue_type) and mapped_dev:
                developer_worked[mapped_dev].add(issue_key)

            if mapped_dev and is_effectively_done_status(to_value, issue_type):
                developer_completed[mapped_dev].add(issue_key)

            if is_bug and mapped_tester and is_tester_verified_status(to_value):
                tester_closed[mapped_tester].add(issue_key)

        if assignee_dev and _is_same_local_day(resolution_dt, local_today, tz_name) and is_effectively_done_status(current_status, issue_type):
            developer_completed[assignee_dev].add(issue_key)

        if is_bug and assignee_tester and _is_same_local_day(resolution_dt, local_today, tz_name) and is_tester_verified_status(current_status):
            tester_closed[assignee_tester].add(issue_key)

    developer_rows = [
        {"name": name, "tasks": len(developer_worked[name]), "completed": len(developer_completed[name])}
        for name in developers.values()
        if developer_worked[name] or developer_completed[name]
    ]
    tester_rows = [
        {"name": name, "bugs_logged": len(tester_logged[name]), "bugs_closed": len(tester_closed[name])}
        for name in testers.values()
        if tester_logged[name] or tester_closed[name]
    ]

    developer_rows.sort(key=lambda row: (-row["tasks"], -row["completed"], row["name"]))
    tester_rows.sort(key=lambda row: (-row["bugs_logged"], -row["bugs_closed"], row["name"]))
    top_developer = _build_top_performer(developer_rows, "completed", "completed")
    top_tester = _build_top_performer(tester_rows, "bugs_closed", "bugs_closed")
    insights = _build_activity_insights(
        developers=developer_rows,
        testers=tester_rows,
        bugs_today=len(bugs_today_keys),
        top_developer=top_developer,
        top_tester=top_tester,
        bug_warning_threshold=int(thresholds.get("bugs_today_warning", BUGS_TODAY_WARNING_THRESHOLD)),
        low_completed_threshold=int(thresholds.get("low_completed_tasks", LOW_COMPLETED_TASKS_THRESHOLD)),
    )

    return {
        "developers": developer_rows,
        "testers": tester_rows,
        "bugs_today": len(bugs_today_keys),
        "top_developer": top_developer,
        "top_tester": top_tester,
        "insights": insights,
    }


def local_day_start_utc(today: date | None = None, tz_name: str = "UTC") -> datetime:
    """Return the UTC datetime corresponding to local midnight for the configured day."""
    tz = get_timezone(tz_name)
    local_today = today or datetime.now(tz).date()
    return datetime.combine(local_today, time.min, tzinfo=tz).astimezone(timezone.utc)


def _work_week_anchor(today: date) -> date:
    """Return the Sunday for the local week containing today."""
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def get_current_work_week_range(today: date | None = None, tz_name: str = "UTC") -> dict:
    """Return current Sunday-Thursday local work-week bounds."""
    tz = get_timezone(tz_name)
    local_today = today or datetime.now(tz).date()
    week_start_day = _work_week_anchor(local_today)
    week_end_day = week_start_day + timedelta(days=4)
    effective_end_day = min(local_today, week_end_day)
    if local_today.weekday() in {4, 5}:  # Friday, Saturday
        effective_end_day = week_end_day
    effective_end_day = min(effective_end_day, week_end_day)
    if effective_end_day < week_start_day:
        effective_end_day = week_start_day

    total_days = (effective_end_day - week_start_day).days + 1
    day_names = WORK_WEEK_DAY_NAMES[:max(0, min(total_days, len(WORK_WEEK_DAY_NAMES)))]
    start_dt = datetime.combine(week_start_day, time.min, tzinfo=tz)
    end_dt = datetime.combine(effective_end_day + timedelta(days=1), time.min, tzinfo=tz)
    logger.info(
        "Computed work week range tz=%s today=%s start_of_week=%s end_of_week_exclusive=%s included_days=%s",
        tz_name,
        local_today.isoformat(),
        start_dt.isoformat(),
        end_dt.isoformat(),
        ",".join(day_names),
    )
    return {"start": start_dt, "end": end_dt, "days": day_names}


def _build_daily_breakdown(days: list[str]) -> dict[str, dict]:
    """Return initialized daily breakdown structure."""
    return {
        day: {
            "bugs_created": 0,
            "tasks_worked": 0,
            "tasks_completed": 0,
            "bugs_logged": 0,
            "bugs_closed": 0,
        }
        for day in days
    }


def _build_daily_sets(days: list[str]) -> dict[str, dict[str, set[str]]]:
    """Return per-day unique issue sets for weekly aggregation."""
    return {
        day: {
            "bugs_created": set(),
            "tasks_worked": set(),
            "tasks_completed": set(),
            "bugs_logged": set(),
            "bugs_closed": set(),
        }
        for day in days
    }


def calculate_weekly_activity(
    issues: list[dict],
    developer_names: list[str] | None = None,
    tester_names: list[str] | None = None,
    *,
    week_range: dict,
    tz_name: str = "UTC",
) -> dict:
    """Compute Sunday-Thursday weekly activity summary and per-day breakdown."""
    developers = _normalize_people(developer_names)
    testers = _normalize_people(tester_names)
    if not developers:
        developers = _derive_people_from_issues(issues, "assignee")
    if not testers:
        testers = _derive_people_from_issues(issues, "reporter")
    day_names = list(week_range.get("days") or [])
    daily_sets = _build_daily_sets(day_names)
    start_local = week_range["start"]
    end_local = week_range["end"]
    start_utc = _to_utc(start_local)
    end_utc = _to_utc(end_local)
    start_day = start_local.date()
    end_day_inclusive = (end_local - timedelta(days=1)).date()
    logger.info(
        "Weekly activity aggregation start_local=%s end_local=%s start_utc=%s end_utc=%s end_inclusive=%s issue_candidates=%s",
        start_local.isoformat(),
        end_local.isoformat(),
        start_utc.isoformat(),
        end_utc.isoformat(),
        end_day_inclusive.isoformat(),
        len(issues),
    )

    developer_worked: dict[str, set[str]] = {name: set() for name in developers.values()}
    developer_completed: dict[str, set[str]] = {name: set() for name in developers.values()}
    tester_logged: dict[str, set[str]] = {name: set() for name in testers.values()}
    tester_closed: dict[str, set[str]] = {name: set() for name in testers.values()}
    bugs_this_week: set[str] = set()
    issues_created_in_range = 0
    issues_updated_in_range = 0
    issues_with_meaningful_changes = 0
    issues_with_resolution_in_range = 0
    included_issue_keys: set[str] = set()

    for issue in issues:
        fields = issue.get("fields", {})
        issue_key = str(issue.get("key") or "")
        if not issue_key:
            continue

        issue_type = str(((fields.get("issuetype") or {}).get("name")) or "").strip()
        normalized_type = _normalize_issue_type(issue_type)
        assignee_name = _display_name(fields.get("assignee"))
        reporter_name = _display_name(fields.get("reporter"))
        created_dt = _to_utc(parse_jira_datetime(fields.get("created")))
        updated_dt = _to_utc(parse_jira_datetime(fields.get("updated")))
        resolution_dt = _to_utc(parse_jira_datetime(fields.get("resolutiondate")))
        current_status = str(((fields.get("status") or {}).get("name")) or "").strip()
        assignee_dev = _pick_known_person(assignee_name, people=developers)
        assignee_tester = _pick_known_person(assignee_name, people=testers)

        logger.info(
            "Weekly issue %s timestamps created_utc=%s updated_utc=%s resolution_utc=%s",
            issue_key,
            created_dt.isoformat() if created_dt else "None",
            updated_dt.isoformat() if updated_dt else "None",
            resolution_dt.isoformat() if resolution_dt else "None",
        )

        created_in_range = created_dt is not None and start_utc <= created_dt < end_utc
        updated_in_range = updated_dt is not None and start_utc <= updated_dt < end_utc
        resolution_in_range = resolution_dt is not None and start_utc <= resolution_dt < end_utc
        created_day = _safe_local_date(created_dt, tz_name)
        if created_in_range:
            issues_created_in_range += 1
        if updated_in_range:
            issues_updated_in_range += 1
        if normalized_type == "bug" and created_in_range and created_day:
            bugs_this_week.add(issue_key)
            day_name = created_day.strftime("%A")
            if day_name in daily_sets:
                daily_sets[day_name]["bugs_created"].add(issue_key)
            mapped_reporter = _pick_known_person(reporter_name, assignee_name, people=testers)
            if mapped_reporter:
                tester_logged[mapped_reporter].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["bugs_logged"].add(issue_key)

        if created_in_range and created_day and _is_work_issue(issue_type) and assignee_dev:
            developer_worked[assignee_dev].add(issue_key)
            day_name = created_day.strftime("%A")
            if day_name in daily_sets:
                daily_sets[day_name]["tasks_worked"].add(issue_key)
            included_issue_keys.add(issue_key)

        changes = _iter_meaningful_changes_between(issue, start_utc, end_utc)
        if changes:
            issues_with_meaningful_changes += 1
            included_issue_keys.add(issue_key)
        for change in changes:
            actor_name = str(change["actor"]).strip()
            to_value = str(change["to"]).strip()
            change_local_day = _safe_local_date(change["datetime"], tz_name)
            day_name = change_local_day.strftime("%A") if change_local_day else ""

            if str(change["field"]) == "assignee":
                mapped_dev = _pick_known_person(to_value, actor_name, assignee_name, people=developers)
                if _is_work_issue(issue_type) and mapped_dev:
                    developer_worked[mapped_dev].add(issue_key)
                    if day_name in daily_sets:
                        daily_sets[day_name]["tasks_worked"].add(issue_key)
                continue

            mapped_dev = _pick_known_person(actor_name, assignee_name, people=developers)
            mapped_tester = _pick_known_person(actor_name, assignee_name, people=testers)
            if _is_work_issue(issue_type) and mapped_dev:
                developer_worked[mapped_dev].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["tasks_worked"].add(issue_key)
            if mapped_dev and is_effectively_done_status(to_value, issue_type):
                developer_completed[mapped_dev].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["tasks_completed"].add(issue_key)
            if normalized_type == "bug" and mapped_tester and is_tester_verified_status(to_value):
                tester_closed[mapped_tester].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["bugs_closed"].add(issue_key)

        resolution_day = _safe_local_date(resolution_dt, tz_name)
        if resolution_in_range and resolution_day:
            issues_with_resolution_in_range += 1
            included_issue_keys.add(issue_key)
            day_name = resolution_day.strftime("%A")
            if assignee_dev and is_effectively_done_status(current_status, issue_type):
                if issue_key not in developer_completed[assignee_dev]:
                    developer_completed[assignee_dev].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["tasks_completed"].add(issue_key)
            if normalized_type == "bug" and assignee_tester and is_tester_verified_status(current_status):
                if issue_key not in tester_closed[assignee_tester]:
                    tester_closed[assignee_tester].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["bugs_closed"].add(issue_key)

        # Fallback path when changelog is missing or incomplete.
        if not changes and updated_in_range:
            included_issue_keys.add(issue_key)
            updated_day = _safe_local_date(updated_dt, tz_name)
            day_name = updated_day.strftime("%A") if updated_day else ""
            if _is_work_issue(issue_type) and assignee_dev:
                developer_worked[assignee_dev].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["tasks_worked"].add(issue_key)
            if normalized_type == "bug" and assignee_tester:
                tester_logged[assignee_tester].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["bugs_logged"].add(issue_key)
            if _is_work_issue(issue_type) and assignee_dev and is_effectively_done_status(current_status, issue_type):
                developer_completed[assignee_dev].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["tasks_completed"].add(issue_key)
            if normalized_type == "bug" and assignee_tester and is_tester_verified_status(current_status):
                tester_closed[assignee_tester].add(issue_key)
                if day_name in daily_sets:
                    daily_sets[day_name]["bugs_closed"].add(issue_key)

    developers_rows = [
        {"name": name, "tasks": len(developer_worked[name]), "completed": len(developer_completed[name])}
        for name in developers.values()
        if developer_worked[name] or developer_completed[name]
    ]
    testers_rows = [
        {"name": name, "bugs_logged": len(tester_logged[name]), "bugs_closed": len(tester_closed[name])}
        for name in testers.values()
        if tester_logged[name] or tester_closed[name]
    ]
    developers_rows.sort(key=lambda row: (-row["tasks"], -row["completed"], row["name"]))
    testers_rows.sort(key=lambda row: (-row["bugs_logged"], -row["bugs_closed"], row["name"]))

    daily_breakdown = _build_daily_breakdown(day_names)
    for day_name, counters in daily_sets.items():
        for key, values in counters.items():
            daily_breakdown[day_name][key] = len(values)

    logger.info(
        "Weekly activity filtered results created_in_range=%s updated_in_range=%s meaningful_change_days=%s resolutions_in_range=%s included_issues=%s bugs_this_week=%s developer_rows=%s tester_rows=%s",
        issues_created_in_range,
        issues_updated_in_range,
        issues_with_meaningful_changes,
        issues_with_resolution_in_range,
        len(included_issue_keys),
        len(bugs_this_week),
        len(developers_rows),
        len(testers_rows),
    )
    logger.info(
        "Weekly activity matched counts developer_tasks=%s developer_completed=%s tester_logged=%s tester_closed=%s",
        sum(len(values) for values in developer_worked.values()),
        sum(len(values) for values in developer_completed.values()),
        sum(len(values) for values in tester_logged.values()),
        sum(len(values) for values in tester_closed.values()),
    )

    return {
        "range": {
            "start": start_local.isoformat(),
            "end": datetime.combine(end_day_inclusive, time.max, tzinfo=end_local.tzinfo).isoformat(),
        },
        "bugs_this_week": len(bugs_this_week),
        "developers": developers_rows,
        "testers": testers_rows,
        "daily_breakdown": daily_breakdown,
    }
