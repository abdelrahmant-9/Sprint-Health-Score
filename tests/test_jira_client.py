"""Unit tests for Jira client integration behavior."""

from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from app.config import Settings
from app.jira_client import JiraClient


def _settings() -> Settings:
    return Settings(
        jira_email="user@example.com",
        jira_api_token="token",
        jira_project_key="PM",
        jira_board_id=55,
    )


def test_fetch_sprint_issues_returns_deduplicated_issues() -> None:
    client = JiraClient(settings=_settings())

    active_sprint = {"values": [{"id": 999, "name": "Sprint A", "state": "active"}]}
    page = {
        "issues": [{"key": "PM-1", "fields": {}}, {"key": "PM-1", "fields": {}}],
        "isLast": True,
        "total": 2,
    }

    with patch.object(client, "agile_get", side_effect=[active_sprint, page]):
        issues, sprint = client.fetch_sprint_issues()

    assert sprint["id"] == 999
    assert len(issues) == 1
    assert issues[0]["key"] == "PM-1"


def test_api_get_retries_then_succeeds() -> None:
    client = JiraClient(settings=_settings())
    first_error = requests.RequestException("temporary")
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None

    with patch("app.jira_client.requests.get", side_effect=[first_error, response]):
        result = client.api_get("search", {"jql": "project = PM"})

    assert result == {"ok": True}
