"""Tests for API key protection and basic run rate limiting."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app


def test_activity_requires_api_key(monkeypatch) -> None:
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("API_KEY", "test-api-key")

    with TestClient(app) as client:
        response = client.get("/activity")

    assert response.status_code == 401


def test_run_is_rate_limited(monkeypatch) -> None:
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("API_KEY", "test-api-key")

    monkeypatch.setattr(
        "api.main.calculate_health_snapshot",
        lambda _settings: {
            "score": 80,
            "completion_rate": 82.5,
            "breakdown": {"commitment": 80, "carryover": 80, "cycle_time": 80, "bug_ratio": 80},
            "report": {"sprint": {"name": "Sprint 1"}},
        },
    )
    monkeypatch.setattr(
        "api.main.get_daily_activity",
        lambda _settings: {
            "developers": [],
            "testers": [],
            "bugs_today": 0,
            "top_developer": {"name": "", "completed": 0},
            "top_tester": {"name": "", "bugs_closed": 0},
            "insights": [],
        },
    )
    monkeypatch.setattr("api.main.save_sprint_result", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr("api.main.send_slack_message", lambda *_args, **_kwargs: None)

    with TestClient(app) as client:
        headers = {"X-API-KEY": "test-api-key"}
        first = client.post("/run", headers=headers)
        second = client.post("/run", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
