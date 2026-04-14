"""Unit tests for metrics calculations."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.metrics import calculate_daily_activity, calculate_metrics, calculate_weekly_activity, get_current_work_week_range


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


def _activity_issue(
    key: str,
    issue_type: str,
    status: str,
    created: str,
    updated: str,
    resolutiondate: str | None,
    assignee: str | None = None,
    reporter: str | None = None,
    histories: list[dict] | None = None,
) -> dict:
    return {
        "key": key,
        "fields": {
            "issuetype": {"name": issue_type},
            "status": {"name": status},
            "created": created,
            "updated": updated,
            "resolutiondate": resolutiondate,
            "assignee": {"displayName": assignee} if assignee else None,
            "reporter": {"displayName": reporter} if reporter else None,
        },
        "changelog": {"histories": histories or []},
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


def test_calculate_daily_activity_summarizes_developers_and_testers() -> None:
    issues = [
        _activity_issue(
            key="PM-10",
            issue_type="Story",
            status="Done",
            created="2026-04-14T08:00:00.000+0300",
            updated="2026-04-14T10:30:00.000+0300",
            resolutiondate="2026-04-14T10:30:00.000+0300",
            assignee="Ahmed Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T07:30:00.000+0300",
                    "author": {"displayName": "Ahmed Dev"},
                    "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
                }
            ],
        ),
        _activity_issue(
            key="PM-11",
            issue_type="Task",
            status="In Progress",
            created="2026-04-13T18:00:00.000+0300",
            updated="2026-04-14T09:10:00.000+0300",
            resolutiondate=None,
            assignee="Ahmed Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T09:10:00.000+0300",
                    "author": {"displayName": "Ahmed Dev"},
                    "items": [{"field": "status", "fromString": "Open", "toString": "In Progress"}],
                }
            ],
        ),
        _activity_issue(
            key="PM-12",
            issue_type="Bug",
            status="Done",
            created="2026-04-14T08:30:00.000+0300",
            updated="2026-04-14T12:00:00.000+0300",
            resolutiondate="2026-04-14T12:00:00.000+0300",
            assignee="Tina QA",
            reporter="Tina QA",
            histories=[
                {
                    "created": "2026-04-14T11:45:00.000+0300",
                    "author": {"displayName": "Tina QA"},
                    "items": [{"field": "status", "fromString": "IN TESTING", "toString": "Done"}],
                }
            ],
        ),
    ]

    activity = calculate_daily_activity(
        issues=issues,
        developer_names=["Ahmed Dev"],
        tester_names=["Tina QA"],
        today=date(2026, 4, 14),
        tz_name="Africa/Cairo",
    )

    assert activity == {
        "developers": [{"name": "Ahmed Dev", "tasks": 2, "completed": 1}],
        "testers": [{"name": "Tina QA", "bugs_logged": 1, "bugs_closed": 1}],
        "bugs_today": 1,
        "top_developer": {"name": "Ahmed Dev", "completed": 1},
        "top_tester": {"name": "Tina QA", "bugs_closed": 1},
        "insights": [
            "1 new bug created today.",
            "Low completed task volume detected today.",
            "Top performer today: Ahmed Dev (1 tasks completed).",
            "Top tester today: Tina QA (1 bugs closed).",
        ],
    }


def test_calculate_daily_activity_falls_back_to_assignee_resolution_when_no_changelog() -> None:
    issues = [
        _activity_issue(
            key="PM-20",
            issue_type="Bug",
            status="Closed",
            created="2026-04-13T18:00:00.000+0300",
            updated="2026-04-14T10:00:00.000+0300",
            resolutiondate="2026-04-14T10:00:00.000+0300",
            assignee="Sam QA",
            reporter="Product",
            histories=[],
        )
    ]

    activity = calculate_daily_activity(
        issues=issues,
        developer_names=[],
        tester_names=["Sam QA"],
        today=date(2026, 4, 14),
        tz_name="Africa/Cairo",
    )

    assert activity == {
        "developers": [],
        "testers": [{"name": "Sam QA", "bugs_logged": 0, "bugs_closed": 1}],
        "bugs_today": 0,
        "top_developer": {"name": "", "completed": 0},
        "top_tester": {"name": "Sam QA", "bugs_closed": 1},
        "insights": [
            "Low completed task volume detected today.",
            "Top tester today: Sam QA (1 bugs closed).",
        ],
    }


def test_calculate_daily_activity_deduplicates_duplicate_status_changes() -> None:
    issues = [
        _activity_issue(
            key="PM-30",
            issue_type="Task",
            status="Done",
            created="2026-04-14T08:00:00.000+0300",
            updated="2026-04-14T12:00:00.000+0300",
            resolutiondate="2026-04-14T12:00:00.000+0300",
            assignee="Nora Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T09:00:00.000+0300",
                    "author": {"displayName": "Nora Dev"},
                    "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
                },
                {
                    "created": "2026-04-14T09:05:00.000+0300",
                    "author": {"displayName": "Nora Dev"},
                    "items": [{"field": "status", "fromString": "Done", "toString": "Done"}],
                },
            ],
        )
    ]

    activity = calculate_daily_activity(
        issues=issues,
        developer_names=["Nora Dev"],
        tester_names=[],
        today=date(2026, 4, 14),
        tz_name="Africa/Cairo",
    )

    assert activity["developers"] == [{"name": "Nora Dev", "tasks": 1, "completed": 1}]
    assert activity["top_developer"] == {"name": "Nora Dev", "completed": 1}


def test_calculate_daily_activity_ignores_comment_only_updates_and_old_bugs() -> None:
    issues = [
        _activity_issue(
            key="PM-40",
            issue_type="Task",
            status="In Progress",
            created="2026-04-13T08:00:00.000+0300",
            updated="2026-04-14T11:00:00.000+0300",
            resolutiondate=None,
            assignee="Lina Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T11:00:00.000+0300",
                    "author": {"displayName": "Lina Dev"},
                    "items": [{"field": "comment", "fromString": "", "toString": ""}],
                }
            ],
        ),
        _activity_issue(
            key="PM-41",
            issue_type="Bug",
            status="In Progress",
            created="2026-04-13T08:00:00.000+0300",
            updated="2026-04-14T12:00:00.000+0300",
            resolutiondate=None,
            assignee="Mina QA",
            reporter="Mina QA",
            histories=[
                {
                    "created": "2026-04-14T12:00:00.000+0300",
                    "author": {"displayName": "Mina QA"},
                    "items": [{"field": "labels", "fromString": "", "toString": "hotfix"}],
                }
            ],
        ),
    ]

    activity = calculate_daily_activity(
        issues=issues,
        developer_names=["Lina Dev"],
        tester_names=["Mina QA"],
        today=date(2026, 4, 14),
        tz_name="Africa/Cairo",
    )

    assert activity["developers"] == []
    assert activity["bugs_today"] == 0
    assert activity["testers"] == []


def test_calculate_daily_activity_respects_local_timezone_for_bugs_today() -> None:
    issues = [
        _activity_issue(
            key="PM-50",
            issue_type="Bug",
            status="Open",
            created="2026-04-13T22:30:00.000+0000",
            updated="2026-04-14T00:10:00.000+0000",
            resolutiondate=None,
            assignee=None,
            reporter="Tina QA",
            histories=[],
        )
    ]

    activity = calculate_daily_activity(
        issues=issues,
        developer_names=[],
        tester_names=["Tina QA"],
        today=date(2026, 4, 14),
        tz_name="Africa/Cairo",
    )

    assert activity["bugs_today"] == 1
    assert activity["testers"] == [{"name": "Tina QA", "bugs_logged": 1, "bugs_closed": 0}]


def test_calculate_daily_activity_handles_missing_people_safely() -> None:
    issues = [
        _activity_issue(
            key="PM-60",
            issue_type="Bug",
            status="Done",
            created="2026-04-14T10:00:00.000+0300",
            updated="2026-04-14T11:00:00.000+0300",
            resolutiondate="2026-04-14T11:00:00.000+0300",
            assignee=None,
            reporter=None,
            histories=[
                {
                    "created": "2026-04-14T11:00:00.000+0300",
                    "author": {"displayName": ""},
                    "items": [{"field": "status", "fromString": "IN TESTING", "toString": "Done"}],
                }
            ],
        )
    ]

    activity = calculate_daily_activity(
        issues=issues,
        developer_names=["Dev One"],
        tester_names=["QA One"],
        today=date(2026, 4, 14),
        tz_name="Africa/Cairo",
    )

    assert activity["bugs_today"] == 1
    assert activity["developers"] == []
    assert activity["testers"] == []


def test_calculate_daily_activity_generates_risk_insights() -> None:
    issues = [
        _activity_issue(
            key="PM-70",
            issue_type="Task",
            status="In Progress",
            created="2026-04-14T08:00:00.000+0300",
            updated="2026-04-14T08:10:00.000+0300",
            resolutiondate=None,
            assignee="Sara Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T08:10:00.000+0300",
                    "author": {"displayName": "Sara Dev"},
                    "items": [{"field": "status", "fromString": "Open", "toString": "In Progress"}],
                }
            ],
        ),
        _activity_issue(
            key="PM-71",
            issue_type="Task",
            status="In Progress",
            created="2026-04-14T08:20:00.000+0300",
            updated="2026-04-14T08:25:00.000+0300",
            resolutiondate=None,
            assignee="Sara Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T08:25:00.000+0300",
                    "author": {"displayName": "Sara Dev"},
                    "items": [{"field": "assignee", "fromString": "", "toString": "Sara Dev"}],
                }
            ],
        ),
        _activity_issue(
            key="PM-72",
            issue_type="Task",
            status="In Progress",
            created="2026-04-14T08:30:00.000+0300",
            updated="2026-04-14T08:35:00.000+0300",
            resolutiondate=None,
            assignee="Sara Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T08:35:00.000+0300",
                    "author": {"displayName": "Sara Dev"},
                    "items": [{"field": "status", "fromString": "Open", "toString": "In Progress"}],
                }
            ],
        ),
    ]
    for index in range(6):
        issues.append(
            _activity_issue(
                key=f"PM-8{index}",
                issue_type="Bug",
                status="Open",
                created=f"2026-04-14T09:{index}0:00.000+0300",
                updated=f"2026-04-14T09:{index}0:00.000+0300",
                resolutiondate=None,
                assignee=None,
                reporter=None,
            )
        )

    activity = calculate_daily_activity(
        issues=issues,
        developer_names=["Sara Dev"],
        tester_names=["Tina QA"],
        today=date(2026, 4, 14),
        tz_name="Africa/Cairo",
    )

    assert activity["bugs_today"] == 6
    assert "High bug creation detected today (6 bugs)." in activity["insights"]
    assert "Low completed task volume detected today." in activity["insights"]
    assert "No tester verification activity detected today." in activity["insights"]
    assert "High carryover risk based on incomplete work still in progress today." in activity["insights"]


def test_get_current_work_week_range_uses_sunday_to_thursday() -> None:
    week_range = get_current_work_week_range(today=date(2026, 4, 15), tz_name="Africa/Cairo")

    assert week_range["start"].date() == date(2026, 4, 12)
    assert week_range["end"].date() == date(2026, 4, 16)
    assert week_range["days"] == ["Sunday", "Monday", "Tuesday", "Wednesday"]


def test_get_current_work_week_range_caps_friday_to_thursday() -> None:
    week_range = get_current_work_week_range(today=date(2026, 4, 17), tz_name="Africa/Cairo")

    assert week_range["start"].date() == date(2026, 4, 12)
    assert week_range["end"].date() == date(2026, 4, 17)
    assert week_range["days"] == ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]


def test_calculate_weekly_activity_returns_work_week_breakdown() -> None:
    week_range = get_current_work_week_range(today=date(2026, 4, 15), tz_name="Africa/Cairo")
    issues = [
        _activity_issue(
            key="PM-90",
            issue_type="Task",
            status="Done",
            created="2026-04-12T09:00:00.000+0300",
            updated="2026-04-14T10:00:00.000+0300",
            resolutiondate="2026-04-15T14:00:00.000+0300",
            assignee="Rana Dev",
            reporter="PM",
            histories=[
                {
                    "created": "2026-04-14T10:00:00.000+0300",
                    "author": {"displayName": "Rana Dev"},
                    "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
                }
            ],
        ),
        _activity_issue(
            key="PM-91",
            issue_type="Bug",
            status="Done",
            created="2026-04-13T11:00:00.000+0300",
            updated="2026-04-15T09:00:00.000+0300",
            resolutiondate="2026-04-15T09:00:00.000+0300",
            assignee="Yara QA",
            reporter="Yara QA",
            histories=[
                {
                    "created": "2026-04-15T09:00:00.000+0300",
                    "author": {"displayName": "Yara QA"},
                    "items": [{"field": "status", "fromString": "IN TESTING", "toString": "Done"}],
                }
            ],
        ),
        _activity_issue(
            key="PM-92",
            issue_type="Bug",
            status="Open",
            created="2026-04-18T08:00:00.000+0300",
            updated="2026-04-18T08:00:00.000+0300",
            resolutiondate=None,
            assignee="Yara QA",
            reporter="Yara QA",
        ),
    ]

    weekly = calculate_weekly_activity(
        issues=issues,
        developer_names=["Rana Dev"],
        tester_names=["Yara QA"],
        week_range=week_range,
        tz_name="Africa/Cairo",
    )

    assert weekly["bugs_this_week"] == 1
    assert weekly["developers"] == [{"name": "Rana Dev", "tasks": 1, "completed": 1}]
    assert weekly["testers"] == [{"name": "Yara QA", "bugs_logged": 1, "bugs_closed": 1}]
    assert weekly["daily_breakdown"]["Sunday"]["tasks_worked"] == 1
    assert weekly["daily_breakdown"]["Monday"]["bugs_created"] == 1
    assert weekly["daily_breakdown"]["Wednesday"]["bugs_closed"] == 1
