"""Notification delivery channels for sprint health reports."""

from __future__ import annotations

import json
import logging

import requests

from app.config import Settings, load_settings


logger = logging.getLogger(__name__)


def send_slack_message(settings_or_message: Settings | str, message: str | None = None) -> None:
    """Send a Slack message via webhook or bot token without crashing callers."""
    if isinstance(settings_or_message, Settings):
        settings = settings_or_message
        payload = message or ""
    else:
        settings = load_settings()
        payload = str(settings_or_message)

    if not payload.strip():
        logger.info("Slack integration skipped because message is empty")
        return
    if settings.slack_webhook:
        _send_via_webhook(settings.slack_webhook, payload)
        return
    if settings.slack_bot_token and settings.slack_channel_id:
        _send_via_bot_token(settings.slack_bot_token, settings.slack_channel_id, payload)
        return
    logger.info("Slack integration skipped because no Slack credentials are configured")


def _send_via_webhook(webhook_url: str, message: str) -> None:
    """Send Slack notification using incoming webhook URL."""
    try:
        response = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"text": message}),
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed sending Slack webhook notification: %s", exc)
        return
    logger.info("Slack webhook notification sent successfully")


def _send_via_bot_token(token: str, channel_id: str, message: str) -> None:
    """Send Slack notification using bot token and channel id."""
    try:
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"channel": channel_id, "text": message, "mrkdwn": True},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error", "unknown_slack_error"))
    except (requests.RequestException, RuntimeError) as exc:
        logger.error("Failed sending Slack bot notification: %s", exc)
        return
    logger.info("Slack bot notification sent successfully")
