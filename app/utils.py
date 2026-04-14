"""Reusable utility helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DONE_STATUSES = {"DONE", "CLOSED", "RESOLVED"}
STORY_DONE_STATUSES = {"READY TO RELEASE"}
TESTER_VERIFIED_STATUSES = {"READY FOR PM REVIEW", "READY TO RELEASE", "DONE", "CLOSED", "RESOLVED"}


def parse_jira_datetime(value: str | None) -> datetime | None:
    """Parse Jira datetime into a timezone-aware UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def get_timezone(tz_name: str) -> ZoneInfo:
    """Return configured timezone or UTC when the name is invalid."""
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def is_effectively_done_status(status_name: str, issue_type: str = "") -> bool:
    """Return True when an issue status should be treated as completed."""
    normalized_status = (status_name or "").strip().upper()
    normalized_type = (issue_type or "").strip().lower()
    if normalized_status in DONE_STATUSES:
        return True
    if normalized_type == "story" and normalized_status in STORY_DONE_STATUSES:
        return True
    return False


def is_tester_verified_status(status_name: str) -> bool:
    """Return True when a tester-oriented status means verification/closure."""
    return (status_name or "").strip().upper() in TESTER_VERIFIED_STATUSES


def issue_weight(issue: dict) -> float:
    """Compute normalized work weight from story points or fallback values."""
    fields = issue.get("fields", {})
    issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
    story_points = fields.get("customfield_10016")
    if isinstance(story_points, (int, float)) and story_points > 0:
        return float(story_points)
    if issue_type.lower() == "story":
        return 3.0
    if issue_type.lower() in {"bug", "feature bug"}:
        return 1.0
    return 0.5
