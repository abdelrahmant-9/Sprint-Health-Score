"""Tests for config validation and change descriptions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import DEFAULT_METRICS_CONFIG, Settings, describe_config_changes, save_metrics_config


def test_describe_config_changes_returns_high_signal_diffs() -> None:
    before = json.loads(json.dumps(DEFAULT_METRICS_CONFIG))
    after = json.loads(json.dumps(DEFAULT_METRICS_CONFIG))
    after["weights"]["bug_ratio"] = 0.4
    after["weights"]["carryover"] = 0.2

    changes = describe_config_changes(before, after)

    assert "Bug weight: 0.2 -> 0.4" in changes
    assert "Carryover weight: 0.25 -> 0.2" in changes


def test_save_metrics_config_rejects_invalid_score_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target_path = tmp_path / "metrics.json"
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("METRICS_CONFIG_PATH", str(target_path))

    config = json.loads(json.dumps(DEFAULT_METRICS_CONFIG))
    config["final_score"]["min_score"] = 90
    config["final_score"]["max_score"] = 10

    with pytest.raises(ValueError, match="minimum must be less than maximum"):
        save_metrics_config(config)


def test_settings_require_api_key() -> None:
    with pytest.raises(Exception):
        Settings(
            jira_email="user@example.com",
            jira_api_token="token",
            jira_project_key="PM",
        )


def test_settings_require_slack_credentials_when_enabled() -> None:
    with pytest.raises(Exception, match="Slack is enabled"):
        Settings(
            jira_email="user@example.com",
            jira_api_token="token",
            jira_project_key="PM",
            api_key="test-api-key",
            slack_enabled=True,
        )
