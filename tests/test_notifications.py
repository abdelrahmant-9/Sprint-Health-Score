"""Unit tests for Slack notification channels."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from app.config import Settings
from app.notifications import send_slack_message


def _settings(**overrides) -> Settings:
    base = {
        "jira_email": "user@example.com",
        "jira_api_token": "token",
        "jira_project_key": "PM",
    }
    base.update(overrides)
    return Settings(**base)


def test_send_slack_message_uses_webhook() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    with patch("app.notifications.requests.post", return_value=response) as post_mock:
        send_slack_message(_settings(slack_webhook="https://hooks.slack.com/services/x"), "hello")
    assert post_mock.called


def test_send_slack_message_uses_bot_token() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True}
    with patch("app.notifications.requests.post", return_value=response) as post_mock:
        send_slack_message(_settings(slack_bot_token="xoxb-1", slack_channel_id="C1"), "hello")
    assert post_mock.called


def test_send_slack_message_raises_on_webhook_failure() -> None:
    with patch("app.notifications.requests.post", side_effect=requests.RequestException("network")):
        with pytest.raises(RuntimeError):
            send_slack_message(_settings(slack_webhook="https://hooks.slack.com/services/x"), "hello")
