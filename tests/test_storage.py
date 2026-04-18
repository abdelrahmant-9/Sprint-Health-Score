"""Tests for SQLite sprint result storage."""

from __future__ import annotations

import json
from pathlib import Path

from app.storage import init_schema, list_recent_reports, list_recent_results, save_sprint_result


def test_save_and_list_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    snapshot = {
        "score": 82,
        "completion_rate": 71.5,
        "breakdown": {"commitment": 80, "carryover": 70, "cycle_time": 90, "bug_ratio": 85, "final_score": 82},
        "report": {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "sprint": {"id": 1, "name": "Sprint A", "state": "active"},
            "metrics": {},
            "scores": {},
            "health_label": "ok",
        },
    }
    rid = save_sprint_result(db, snapshot)
    assert rid >= 1
    rows = list_recent_results(db, limit=10)
    assert len(rows) == 1
    assert rows[0]["score"] == 82
    assert rows[0]["sprint_name"] == "Sprint A"
    assert rows[0]["breakdown"]["commitment"] == 80
    assert isinstance(json.loads(json.dumps(rows[0]["breakdown"])), dict)


def test_init_schema_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    init_schema(db)
    init_schema(db)


def test_list_recent_reports_includes_report_payload(tmp_path: Path) -> None:
    db = tmp_path / "report.db"
    snapshot = {
        "score": 70,
        "completion_rate": 80.0,
        "breakdown": {"commitment": 70, "carryover": 70, "cycle_time": 70, "bug_ratio": 70, "final_score": 70},
        "report": {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "sprint": {"id": 11, "name": "Sprint 11", "state": "closed"},
            "metrics": {"avg_cycle_time_days": 4.2},
            "scores": {"final_score": 70},
            "analytics": {"health_score": 70, "history": []},
            "health_label": "Some instability",
        },
    }

    save_sprint_result(db, snapshot)
    rows = list_recent_reports(db, limit=5)

    assert rows[0]["sprint_name"] == "Sprint 11"
    assert rows[0]["report"]["analytics"]["health_score"] == 70
