"""Metric calculations for sprint health reporting."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, fields, replace
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
METRIC_FIELD_NAMES = tuple(field.name for field in fields(SprintMetrics))
ACTIVE_WORK_STATUSES = {
    "IN PROGRESS",
    "IN DEVELOPMENT",
    "DEVELOPMENT",
    "CODE REVIEW",
    "IN REVIEW",
    "REVIEW",
    "READY FOR QA",
    "QA",
    "IN TESTING",
    "TESTING",
    "READY FOR PM REVIEW",
    "READY TO RELEASE",
    "BLOCKED",
}
BLOCKED_STATUS_NAMES = {"BLOCKED"}
BUG_TYPES = {"bug", "feature bug"}
EXTERNAL_BUG_MARKERS = {"support", "csm", "customer", "client", "external"}


def _connect_override_db(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection for metrics override operations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow_iso() -> str:
    """Return the current UTC timestamp as ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def is_editable_metric(metric_name: str) -> bool:
    """Return True when a metric can be overridden."""
    return metric_name in METRIC_FIELD_NAMES


def get_override_from_db(db_path: Path, metric_name: str) -> float | None:
    """Return a stored override value for a metric, if present."""
    if not is_editable_metric(metric_name):
        raise KeyError(f"Unknown metric: {metric_name}")
    conn = _connect_override_db(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM metrics_override WHERE metric_name = ?",
            (metric_name,),
        ).fetchone()
        return None if row is None or row["value"] is None else float(row["value"])
    finally:
        conn.close()


def set_override_in_db(db_path: Path, metric_name: str, value: float) -> None:
    """Persist an override value for a metric."""
    if not is_editable_metric(metric_name):
        raise KeyError(f"Unknown metric: {metric_name}")
    conn = _connect_override_db(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO metrics_override (metric_name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(metric_name) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (metric_name, float(value), _utcnow_iso()),
            )
    finally:
        conn.close()


def list_metric_overrides(db_path: Path) -> dict[str, dict]:
    """Return all metric overrides keyed by metric name."""
    conn = _connect_override_db(db_path)
    try:
        rows = conn.execute(
            "SELECT metric_name, value, updated_at FROM metrics_override ORDER BY metric_name"
        ).fetchall()
        return {
            str(row["metric_name"]): {
                "value": None if row["value"] is None else float(row["value"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        }
    finally:
        conn.close()


def get_metric(metric_name: str, base_metrics: SprintMetrics, db_path: Path | None = None) -> float | int | None:
    """Return the effective metric value after applying any DB override."""
    if not is_editable_metric(metric_name):
        raise KeyError(f"Unknown metric: {metric_name}")
    base_value = getattr(base_metrics, metric_name)
    if db_path is None:
        return base_value
    override_value = get_override_from_db(db_path, metric_name)
    if override_value is None:
        return base_value
    if isinstance(base_value, int):
        return int(round(override_value))
    return float(override_value)


def apply_metric_overrides(base_metrics: SprintMetrics, db_path: Path | None = None) -> SprintMetrics:
    """Return a metrics dataclass with DB overrides layered on top."""
    if db_path is None:
        return base_metrics
    overrides: dict[str, float | int] = {}
    for metric_name in METRIC_FIELD_NAMES:
        base_value = getattr(base_metrics, metric_name)
        override_value = get_override_from_db(db_path, metric_name)
        if override_value is None:
            continue
        if isinstance(base_value, int):
            overrides[metric_name] = int(round(override_value))
        else:
            overrides[metric_name] = float(override_value)
    if not overrides:
        return base_metrics
    return replace(base_metrics, **overrides)


def list_metric_rows(base_metrics: SprintMetrics, db_path: Path | None = None) -> list[dict]:
    """Return metric metadata for admin editing and dashboard display."""
    overrides = list_metric_overrides(db_path) if db_path else {}
    rows: list[dict] = []
    for metric_name, base_value in asdict(base_metrics).items():
        override_payload = overrides.get(metric_name) or {}
        override_value = override_payload.get("value")
        rows.append(
            {
                "metric_name": metric_name,
                "base_value": base_value,
                "override_value": override_value,
                "value": get_metric(metric_name, base_metrics, db_path),
                "updated_at": override_payload.get("updated_at"),
            }
        )
    return rows


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


def _round_metric(value: float | None, digits: int = 1) -> float | None:
    """Round a metric safely while preserving missing values."""
    if value is None:
        return None
    return round(float(value), digits)


def _safe_percentage(numerator: float, denominator: float, digits: int = 1) -> float:
    """Return a bounded percentage with a zero-safe denominator."""
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100, digits)


def _median(values: list[float]) -> float | None:
    """Return the median for a non-empty numeric list."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def calculate_commitment_reliability(completed_issues: int, committed_issues: int) -> float:
    """Return commitment reliability percentage for a sprint."""
    return _safe_percentage(completed_issues, committed_issues)


def calculate_carryover_rate(carried_over: float, total_scope: float) -> float:
    """Return carryover rate percentage for a sprint."""
    return _safe_percentage(carried_over, total_scope)


def calculate_bug_ratio(bugs_created: int, completed_stories: int) -> float:
    """Return bug ratio percentage for a sprint."""
    return _safe_percentage(bugs_created, completed_stories)


def score_commitment_reliability(commitment_pct: float, config: dict | None = None) -> int:
    """Score commitment reliability using configurable normalized bands."""
    scoring_config = config or {}
    commitment_config = scoring_config.get("commitment") or {}
    points = scoring_config.get("points") or {}
    excellent = int(points.get("excellent", 100))
    good = int(points.get("good", 70))
    warning = int(points.get("warning", 40))
    poor = int(points.get("poor", 0))
    ideal_min = float(commitment_config.get("ideal_min_pct", 85.0))
    ideal_max = float(commitment_config.get("ideal_max_pct", 95.0))
    good_min = float(commitment_config.get("good_min_pct", 70.0))
    warning_min = float(commitment_config.get("warning_min_pct", 50.0))
    extended_cap = int(commitment_config.get("extended_cap_score", good))

    if ideal_min <= commitment_pct <= ideal_max:
        return excellent
    if commitment_pct > ideal_max:
        return extended_cap
    if commitment_pct >= good_min:
        return good
    if commitment_pct >= warning_min:
        return warning
    return poor


def score_carryover_rate(carryover_pct: float, config: dict | None = None) -> int:
    """Score carryover rate where lower spillover yields higher points."""
    scoring_config = config or {}
    carryover_config = scoring_config.get("carryover") or {}
    points = scoring_config.get("points") or {}
    excellent = int(points.get("excellent", 100))
    good = int(points.get("good", 70))
    warning = int(points.get("warning", 40))
    poor = int(points.get("poor", 0))
    excellent_lt = float(carryover_config.get("excellent_lt_pct", 10.0))
    good_lte = float(carryover_config.get("good_lte_pct", 20.0))
    warning_lte = float(carryover_config.get("warning_lte_pct", 30.0))

    if carryover_pct < excellent_lt:
        return excellent
    if carryover_pct <= good_lte:
        return good
    if carryover_pct <= warning_lte:
        return warning
    return poor


def calculate_cycle_time_stability(
    current_avg_cycle_time: float | None,
    previous_cycle_time_days: list[float] | None,
    config: dict | None = None,
) -> dict:
    """Compare current sprint cycle time with the prior sprint baseline."""
    scoring_config = config or {}
    cycle_config = scoring_config.get("cycle_time") or {}
    points = scoring_config.get("points") or {}
    neutral = int(points.get("neutral", 70))
    excellent = int(points.get("excellent", 100))
    good = int(points.get("good", 70))
    warning = int(points.get("warning", 40))
    poor = int(points.get("poor", 0))
    stable_abs_pct = float(cycle_config.get("stable_abs_pct", 10.0))
    good_increase_pct = float(cycle_config.get("good_increase_pct", 20.0))
    warning_increase_pct = float(cycle_config.get("warning_increase_pct", 30.0))
    prior_values = [float(value) for value in (previous_cycle_time_days or []) if value is not None]
    baseline_values = prior_values[:3]
    baseline_avg = round(sum(baseline_values) / len(baseline_values), 2) if baseline_values else None

    if current_avg_cycle_time is None:
        return {
            "current_avg": None,
            "baseline_avg": baseline_avg,
            "pct_change": None,
            "score": neutral,
            "trend": "insufficient_data",
        }

    if baseline_avg in (None, 0):
        return {
            "current_avg": round(float(current_avg_cycle_time), 2),
            "baseline_avg": baseline_avg,
            "pct_change": None,
            "score": neutral,
            "trend": "insufficient_history",
        }

    pct_change = ((float(current_avg_cycle_time) - baseline_avg) / baseline_avg) * 100
    rounded_change = round(pct_change, 1)
    if pct_change <= stable_abs_pct and pct_change >= -stable_abs_pct:
        score = excellent
    elif pct_change < -stable_abs_pct:
        score = excellent
    elif pct_change <= good_increase_pct:
        score = good
    elif pct_change <= warning_increase_pct:
        score = warning
    else:
        score = poor

    if pct_change < -stable_abs_pct:
        trend = "faster"
    elif pct_change > stable_abs_pct:
        trend = "slower"
    else:
        trend = "stable"

    return {
        "current_avg": round(float(current_avg_cycle_time), 2),
        "baseline_avg": baseline_avg,
        "pct_change": rounded_change,
        "score": score,
        "trend": trend,
    }


def score_bug_ratio(bug_ratio_pct: float, config: dict | None = None) -> int:
    """Score bug ratio using configurable normalized bands."""
    scoring_config = config or {}
    bug_config = scoring_config.get("bug_ratio") or {}
    points = scoring_config.get("points") or {}
    excellent = int(points.get("excellent", 100))
    good = int(points.get("good", 70))
    warning = int(points.get("warning", 40))
    poor = int(points.get("poor", 0))
    excellent_lt = float(bug_config.get("excellent_lt_pct", 15.0))
    good_lte = float(bug_config.get("good_lte_pct", 25.0))
    warning_lte = float(bug_config.get("warning_lte_pct", 35.0))

    if bug_ratio_pct < excellent_lt:
        return excellent
    if bug_ratio_pct <= good_lte:
        return good
    if bug_ratio_pct <= warning_lte:
        return warning
    return poor


def aggregate_health_score(
    commitment_score: int,
    carryover_score: int,
    cycle_time_score: int,
    bug_score: int,
    config: dict | None = None,
) -> int:
    """Aggregate weighted health score from the four normalized signals."""
    scoring_config = config or {}
    weights = scoring_config.get("weights") or {}
    final_score_config = scoring_config.get("final_score") or {}
    score = (
        (float(commitment_score) * float(weights.get("commitment", 0.35)))
        + (float(carryover_score) * float(weights.get("carryover", 0.25)))
        + (float(cycle_time_score) * float(weights.get("cycle_time", 0.20)))
        + (float(bug_score) * float(weights.get("bug_ratio", 0.20)))
    )
    if bool(final_score_config.get("round_result", True)):
        score = round(score)
    min_score = int(final_score_config.get("min_score", 0))
    max_score = int(final_score_config.get("max_score", 100))
    return max(min_score, min(max_score, int(score)))


def classify_health_status(score: int, config: dict | None = None) -> str:
    """Return the color-band health status for a score."""
    scoring_config = config or {}
    labels = scoring_config.get("labels") or {}
    green_min = int(labels.get("green_min_score", 85))
    yellow_min = int(labels.get("yellow_min_score", 70))
    orange_min = int(labels.get("orange_min_score", 50))
    if score >= green_min:
        return "Green"
    if score >= yellow_min:
        return "Yellow"
    if score >= orange_min:
        return "Orange"
    return "Red"


def _is_active_work_status(status_name: str) -> bool:
    """Return True when the status represents active flow work."""
    return (status_name or "").strip().upper() in ACTIVE_WORK_STATUSES


def _iter_status_events(issue: dict) -> list[tuple[datetime, str]]:
    """Return ordered status transition events for an issue."""
    events: list[tuple[datetime, str]] = []
    changelog = issue.get("changelog") or {}
    for history in changelog.get("histories") or []:
        event_time = parse_jira_datetime(history.get("created"))
        if event_time is None:
            continue
        for item in history.get("items") or []:
            if str(item.get("field") or "").strip().lower() != "status":
                continue
            to_status = str(item.get("toString") or "").strip()
            events.append((event_time, to_status))
    events.sort(key=lambda item: item[0])
    return events


def _extract_issue_flow_metrics(issue: dict, *, reference_end: datetime | None = None) -> dict:
    """Extract deterministic timing metrics from status history."""
    fields = issue.get("fields", {})
    issue_type = str(((fields.get("issuetype") or {}).get("name")) or "").strip()
    created_dt = parse_jira_datetime(fields.get("created"))
    resolved_dt = parse_jira_datetime(fields.get("resolutiondate"))
    updated_dt = parse_jira_datetime(fields.get("updated"))
    current_status = str(((fields.get("status") or {}).get("name")) or "").strip()
    status_events = _iter_status_events(issue)

    in_progress_dt: datetime | None = None
    done_dt: datetime | None = None
    blocked_started_at: datetime | None = None
    blocked_seconds = 0.0

    for event_dt, to_status in status_events:
        normalized_status = to_status.strip().upper()
        if in_progress_dt is None and _is_active_work_status(to_status):
            in_progress_dt = event_dt
        if blocked_started_at is None and normalized_status in BLOCKED_STATUS_NAMES:
            blocked_started_at = event_dt
        elif blocked_started_at is not None and normalized_status not in BLOCKED_STATUS_NAMES:
            blocked_seconds += max(0.0, (event_dt - blocked_started_at).total_seconds())
            blocked_started_at = None
        if done_dt is None and is_effectively_done_status(to_status, issue_type):
            done_dt = event_dt

    if done_dt is None:
        done_dt = resolved_dt
    if in_progress_dt is None and created_dt and done_dt:
        in_progress_dt = created_dt

    terminal_dt = done_dt or updated_dt or reference_end
    if blocked_started_at is not None and terminal_dt is not None:
        blocked_seconds += max(0.0, (terminal_dt - blocked_started_at).total_seconds())

    cycle_seconds: float | None = None
    if in_progress_dt and terminal_dt and terminal_dt >= in_progress_dt:
        cycle_seconds = max(0.0, (terminal_dt - in_progress_dt).total_seconds())

    return {
        "issue_type": issue_type,
        "current_status": current_status,
        "in_progress_date": in_progress_dt,
        "done_date": done_dt,
        "cycle_seconds": cycle_seconds,
        "cycle_days": None if cycle_seconds is None else cycle_seconds / 86400,
        "blocked_seconds": blocked_seconds,
    }


def _normalize_cycle_time_issue_type(issue_type: str) -> str | None:
    """Map Jira issue types to the analytics cycle-time buckets."""
    normalized = _normalize_issue_type(issue_type)
    if normalized in {"story", "bug", "task", "feature bug"}:
        return "bug" if normalized == "feature bug" else normalized
    return None


def _extract_linked_issue_keys(issue: dict) -> set[str]:
    """Return linked or parent issue keys for the provided Jira issue."""
    linked_keys: set[str] = set()
    fields = issue.get("fields", {})
    parent = fields.get("parent") or {}
    parent_key = str(parent.get("key") or "").strip()
    if parent_key:
        linked_keys.add(parent_key)

    for link in fields.get("issuelinks") or []:
        for side in ("inwardIssue", "outwardIssue"):
            linked_issue = link.get(side) or {}
            linked_key = str(linked_issue.get("key") or "").strip()
            if linked_key:
                linked_keys.add(linked_key)
    return linked_keys


def _is_external_bug(issue: dict) -> bool:
    """Classify support or customer-originated bugs using deterministic heuristics."""
    fields = issue.get("fields", {})
    labels = [str(label).strip().casefold() for label in (fields.get("labels") or [])]
    summary = str(fields.get("summary") or "").casefold()
    reporter = _display_name(fields.get("reporter")).casefold()
    creator = _display_name(fields.get("creator")).casefold()
    haystacks = labels + [summary, reporter, creator]
    return any(marker in haystack for haystack in haystacks for marker in EXTERNAL_BUG_MARKERS)


def calculate_advanced_sprint_metrics(
    issues: list[dict],
    *,
    sprint_start: datetime | None = None,
    sprint_end: datetime | None = None,
    reference_time: datetime | None = None,
) -> dict:
    """Calculate advanced engineering metrics from sprint issues and changelog data."""
    reference_end = reference_time or sprint_end or datetime.now(timezone.utc)
    cycle_time_buckets: dict[str, list[float]] = {"story": [], "bug": [], "task": []}
    cycle_time_samples: list[float] = []
    total_cycle_seconds = 0.0
    total_blocked_seconds = 0.0
    story_keys: set[str] = set()
    completed_story_count = 0
    bug_engineer_counts: Counter[str] = Counter()
    story_bug_engineer_counts: Counter[str] = Counter()
    bug_classification = {
        "from_current_sprint_stories": 0,
        "generated_by_stories_in_sprint": 0,
        "external_bugs": 0,
    }

    for issue in issues:
        fields = issue.get("fields", {})
        issue_key = str(issue.get("key") or "").strip()
        issue_type = str(((fields.get("issuetype") or {}).get("name")) or "").strip()
        normalized_type = _normalize_issue_type(issue_type)
        if normalized_type == "story" and issue_key:
            story_keys.add(issue_key)
            status_name = str(((fields.get("status") or {}).get("name")) or "").strip()
            if is_effectively_done_status(status_name, issue_type):
                completed_story_count += 1

    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = str(((fields.get("issuetype") or {}).get("name")) or "").strip()
        normalized_type = _normalize_issue_type(issue_type)
        timing = _extract_issue_flow_metrics(issue, reference_end=reference_end)
        cycle_days = timing["cycle_days"]
        bucket = _normalize_cycle_time_issue_type(issue_type)

        if bucket and cycle_days is not None:
            cycle_time_buckets[bucket].append(float(cycle_days))
            cycle_time_samples.append(float(cycle_days))
            total_cycle_seconds += float(timing["cycle_seconds"] or 0.0)
            total_blocked_seconds += float(timing["blocked_seconds"] or 0.0)

        if normalized_type not in BUG_TYPES:
            continue

        engineer = (
            _display_name(fields.get("assignee"))
            or _display_name(fields.get("reporter"))
            or _display_name(fields.get("creator"))
            or "Unassigned"
        )
        bug_engineer_counts[engineer] += 1
        linked_story_keys = _extract_linked_issue_keys(issue).intersection(story_keys)
        if linked_story_keys:
            bug_classification["from_current_sprint_stories"] += 1
            story_bug_engineer_counts[engineer] += 1
        elif _is_external_bug(issue):
            bug_classification["external_bugs"] += 1
        else:
            created_dt = parse_jira_datetime(fields.get("created"))
            if sprint_start and created_dt and created_dt >= sprint_start:
                bug_classification["generated_by_stories_in_sprint"] += 1
                story_bug_engineer_counts[engineer] += 1
            else:
                bug_classification["external_bugs"] += 1

    avg_per_story = round(sum(bug_classification.values()) / len(story_keys), 2) if story_keys else 0.0
    top_bug_engineer = bug_engineer_counts.most_common(1)[0][0] if bug_engineer_counts else ""
    top_story_bug_engineer = story_bug_engineer_counts.most_common(1)[0][0] if story_bug_engineer_counts else ""
    current_avg_cycle_time = round(sum(cycle_time_samples) / len(cycle_time_samples), 2) if cycle_time_samples else None
    blocked_ratio = _safe_percentage(total_blocked_seconds, total_cycle_seconds)

    return {
        "current_avg_cycle_time": current_avg_cycle_time,
        "completed_story_count": completed_story_count,
        "story_count": len(story_keys),
        "cycle_time": {
            "story": _round_metric(_median(cycle_time_buckets["story"]), 2),
            "bug": _round_metric(_median(cycle_time_buckets["bug"]), 2),
            "task": _round_metric(_median(cycle_time_buckets["task"]), 2),
        },
        "blocked_ratio": blocked_ratio,
        "bugs": {
            "classification": bug_classification,
            "avg_per_story": avg_per_story,
            "top_bug_engineer": top_bug_engineer,
            "most_story_bug_engineer": top_story_bug_engineer,
        },
    }


def build_sprint_health_payload(
    metrics: SprintMetrics,
    *,
    advanced_metrics: dict | None = None,
    historical_snapshots: list[dict] | None = None,
    config: dict | None = None,
) -> dict:
    """Build the normalized sprint health payload used by the API and dashboard."""
    analytics = advanced_metrics or {}
    history = historical_snapshots or []
    commitment_pct = calculate_commitment_reliability(metrics.completed_items, metrics.total_items)
    carryover_pct = calculate_carryover_rate(metrics.carryover_scope, metrics.committed_scope)
    completed_story_count = int(analytics.get("completed_story_count", 0) or 0)
    bug_ratio_pct = calculate_bug_ratio(metrics.new_bug_count, completed_story_count or metrics.completed_items)
    previous_cycle_times = [
        float(item["metrics"].get("avg_cycle_time_days"))
        for item in history
        if isinstance(item.get("metrics"), dict) and item["metrics"].get("avg_cycle_time_days") is not None
    ]
    current_cycle_time = analytics.get("current_avg_cycle_time")
    if current_cycle_time is None:
        current_cycle_time = metrics.avg_cycle_time_days
    cycle_time_payload = calculate_cycle_time_stability(current_cycle_time, previous_cycle_times, config=config)

    commitment_score = score_commitment_reliability(commitment_pct, config=config)
    carryover_score = score_carryover_rate(carryover_pct, config=config)
    cycle_time_score = int(cycle_time_payload["score"])
    bug_score = score_bug_ratio(bug_ratio_pct, config=config)
    health_score = aggregate_health_score(
        commitment_score=commitment_score,
        carryover_score=carryover_score,
        cycle_time_score=cycle_time_score,
        bug_score=bug_score,
        config=config,
    )
    health_status = classify_health_status(health_score, config=config)

    return {
        "commitment_score": commitment_score,
        "carryover_score": carryover_score,
        "cycle_time_score": cycle_time_score,
        "bug_score": bug_score,
        "health_score": health_score,
        "health_status": health_status,
        "inputs": {
            "commitment_reliability": commitment_pct,
            "carryover_rate": carryover_pct,
            "bug_ratio": bug_ratio_pct,
            "current_avg_cycle_time": cycle_time_payload.get("current_avg"),
            "previous_avg_cycle_time": cycle_time_payload.get("baseline_avg"),
            "cycle_time_change_pct": cycle_time_payload.get("pct_change"),
        },
        "cycle_time": {
            **(analytics.get("cycle_time") or {"story": None, "bug": None, "task": None}),
            "current_avg": cycle_time_payload.get("current_avg"),
            "baseline_avg_last_3": cycle_time_payload.get("baseline_avg"),
            "trend_pct": cycle_time_payload.get("pct_change"),
            "trend": cycle_time_payload.get("trend"),
        },
        "blocked_ratio": float(analytics.get("blocked_ratio", 0.0) or 0.0),
        "bugs": analytics.get("bugs")
        or {
            "classification": {
                "from_current_sprint_stories": 0,
                "generated_by_stories_in_sprint": 0,
                "external_bugs": 0,
            },
            "avg_per_story": 0.0,
            "top_bug_engineer": "",
            "most_story_bug_engineer": "",
        },
    }


def generate_sprint_insights(health_payload: dict) -> list[str]:
    """Generate deterministic root-cause insights for sprint performance."""
    insights: list[str] = []
    if int(health_payload.get("commitment_score", 0) or 0) < 70:
        insights.append("Sprint overcommitment detected. Team is taking more work than capacity.")
    if int(health_payload.get("carryover_score", 0) or 0) < 70:
        insights.append("High carryover indicates unfinished work spilling into next sprint.")
    if int(health_payload.get("cycle_time_score", 0) or 0) < 70:
        insights.append("Cycle time increased significantly, suggesting process bottlenecks.")
    if int(health_payload.get("bug_score", 0) or 0) < 70:
        insights.append("High bug ratio suggests quality issues or rushed development.")
    if float(health_payload.get("blocked_ratio", 0.0) or 0.0) > 20.0:
        insights.append("High blocked time indicates dependency or workflow problems.")
    if not insights:
        insights.append("Sprint delivery is stable across commitment, flow, and quality signals.")
    return insights


def build_sprint_summary(health_payload: dict) -> str:
    """Create a short human-friendly summary for dashboard display."""
    status = str(health_payload.get("health_status") or "Yellow")
    insights = [str(item) for item in (health_payload.get("insights") or []) if str(item).strip()]
    if status == "Green":
        return "Sprint performance is stable. Delivery, flow, and quality signals are within target ranges."
    if not insights:
        return "Sprint performance is mixed. A few signals need attention, but no single driver dominates the outcome."
    dominant = insights[:2]
    if status == "Red":
        prefix = "Sprint performance is at risk."
    elif status == "Orange":
        prefix = "Sprint performance is unstable."
    else:
        prefix = "Sprint performance needs attention."
    return f"{prefix} {' '.join(dominant)}"


def predict_next_sprint_health(history: list[dict]) -> dict:
    """Predict the next sprint health score using recent weighted trend logic."""
    recent = [item for item in history if item]
    if not recent:
        return {"next_sprint_health": 0, "trend": "stable", "confidence": "low", "delta": 0}

    health_scores = [float(item.get("health_score", 0) or 0) for item in recent[:5]]
    last_health = health_scores[0]
    last_three = health_scores[:3] or [last_health]
    avg_last_three = sum(last_three) / len(last_three)

    adjustment_signals = 0
    metric_keys = ("health_score", "commitment_score", "carryover_score", "cycle_time_score", "bug_score")
    for key in metric_keys:
        values = [float(item.get(key, 0) or 0) for item in recent[:3] if item.get(key) is not None]
        if len(values) < 2:
            continue
        if values[0] > values[-1]:
            adjustment_signals += 1
        elif values[0] < values[-1]:
            adjustment_signals -= 1

    trend_adjustment = max(-10, min(10, adjustment_signals * 2.5))
    predicted = round((last_health * 0.5) + (avg_last_three * 0.3) + (trend_adjustment * 0.2))
    predicted = max(0, min(100, predicted))

    if trend_adjustment >= 5:
        trend = "improving"
    elif trend_adjustment <= -5:
        trend = "declining"
    else:
        trend = "stable"

    if len(recent) >= 5:
        confidence = "high"
    elif len(recent) >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "next_sprint_health": predicted,
        "trend": trend,
        "confidence": confidence,
        "delta": round(predicted - last_health, 1),
    }


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
