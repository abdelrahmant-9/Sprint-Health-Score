"""Jira API client with retries and lightweight caching."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from app.config import Settings


logger = logging.getLogger(__name__)


@dataclass
class JiraClient:
    """Encapsulates Jira REST access for search and sprint issue retrieval."""

    settings: Settings
    _board_id_cache: int | None = None
    _cache: dict[str, dict] = field(default_factory=dict)

    def _get(self, endpoint: str, path: str, params: dict | None = None) -> dict:
        """Perform a GET request with retry handling and explicit error propagation."""
        url = f"{self.settings.jira_base_url}/rest/{endpoint}/{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.settings.jira_request_retries + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    auth=(self.settings.jira_email, self.settings.jira_api_token),
                    headers={"Accept": "application/json"},
                    timeout=self.settings.request_timeout_seconds,
                )
                if response.status_code == 410 and endpoint == "api/3" and path == "search":
                    fallback = f"{self.settings.jira_base_url}/rest/api/3/search/jql"
                    response = requests.get(
                        fallback,
                        params=params,
                        auth=(self.settings.jira_email, self.settings.jira_api_token),
                        headers={"Accept": "application/json"},
                        timeout=self.settings.request_timeout_seconds,
                    )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.settings.jira_request_retries:
                    sleep_for = self.settings.jira_retry_delay_seconds * attempt
                    logger.warning("Jira request retry %s for %s after error: %s", attempt, path, exc)
                    time.sleep(sleep_for)
        raise RuntimeError(f"Jira request failed for {path}: {last_error}") from last_error

    def api_get(self, path: str, params: dict | None = None) -> dict:
        """Call Jira platform API v3 endpoint."""
        return self._get("api/3", path, params)

    def agile_get(self, path: str, params: dict | None = None) -> dict:
        """Call Jira Agile API endpoint."""
        return self._get("agile/1.0", path, params)

    def get_board_id(self) -> int | None:
        """Resolve board id from explicit config or Jira board listing."""
        if self._board_id_cache is not None:
            return self._board_id_cache
        if self.settings.jira_board_id:
            self._board_id_cache = self.settings.jira_board_id
            return self._board_id_cache
        boards = self.agile_get("board", {"projectKeyOrId": self.settings.jira_project_key, "maxResults": 50})
        values = boards.get("values", [])
        if not values:
            return None
        scrum = [item for item in values if item.get("type") == "scrum"]
        chosen = scrum[0] if scrum else values[0]
        self._board_id_cache = int(chosen["id"])
        logger.info("Detected board '%s' with id=%s", chosen.get("name"), self._board_id_cache)
        return self._board_id_cache

    def fetch_sprint_issues(self, *, include_activity_fields: bool = False) -> tuple[list[dict], dict]:
        """Fetch active sprint issues with de-duplication and request-level cache."""
        board_id = self.get_board_id()
        if not board_id:
            raise RuntimeError("Could not resolve Jira board id.")

        sprint_data = self.agile_get(f"board/{board_id}/sprint", {"state": "active"})
        sprints = sprint_data.get("values", [])
        if not sprints:
            raise RuntimeError("No active sprint found for configured board.")
        sprint = sprints[0]
        sprint_id = int(sprint["id"])

        fields = (
            "summary,status,issuetype,created,resolutiondate,customfield_10016,"
            "assignee,labels,updated,customfield_10021"
        )
        if include_activity_fields:
            fields = f"{fields},reporter"
        cache_key = json.dumps(
            {"board": board_id, "sprint": sprint_id, "fields": fields, "expand": "changelog" if include_activity_fields else ""},
            sort_keys=True,
        )
        if cache_key in self._cache:
            logger.info("Using cached sprint issue payload for sprint %s", sprint_id)
            return self._cache[cache_key]["issues"], sprint

        issues: list[dict] = []
        seen = set()
        start_at = 0
        while True:
            params = {"fields": fields, "startAt": start_at, "maxResults": 50}
            if include_activity_fields:
                params["expand"] = "changelog"
            data = self.agile_get(f"board/{board_id}/sprint/{sprint_id}/issue", params)
            page_issues = data.get("issues", [])
            if not page_issues:
                break
            for issue in page_issues:
                issue_key = issue.get("key")
                if issue_key and issue_key not in seen:
                    seen.add(issue_key)
                    issues.append(issue)
            start_at += len(page_issues)
            if data.get("isLast") is True or start_at >= int(data.get("total", 0) or 0):
                break

        self._cache[cache_key] = {"issues": issues}
        return issues, sprint

    def fetch_issues_updated_between(self, since: datetime, until: datetime | None = None) -> list[dict]:
        """Fetch project issues updated within the provided UTC range with changelog."""
        since_utc = since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        until_utc = until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if until else ""
        fields = "summary,status,issuetype,created,resolutiondate,assignee,reporter,updated"
        cache_key = json.dumps(
            {"query": "updated_between", "since": since_utc, "until": until_utc, "fields": fields, "expand": "changelog"},
            sort_keys=True,
        )
        if cache_key in self._cache:
            logger.info("Using cached updated issue payload since %s until %s", since_utc, until_utc or "open")
            return self._cache[cache_key]["issues"]

        jql_parts = [
            f'project = "{self.settings.jira_project_key}"',
            f'updated >= "{since_utc}"',
        ]
        if until_utc:
            jql_parts.append(f'updated < "{until_utc}"')
        jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"
        issues: list[dict] = []
        start_at = 0
        while True:
            data = self.api_get(
                "search",
                {
                    "jql": jql,
                    "fields": fields,
                    "expand": "changelog",
                    "startAt": start_at,
                    "maxResults": 50,
                },
            )
            page_issues = data.get("issues", [])
            if not page_issues:
                break
            issues.extend(page_issues)
            start_at += len(page_issues)
            if start_at >= int(data.get("total", 0) or 0):
                break

        self._cache[cache_key] = {"issues": issues}
        return issues

    def fetch_issues_updated_since(self, since: datetime) -> list[dict]:
        """Fetch project issues updated since the provided timestamp with changelog."""
        return self.fetch_issues_updated_between(since=since, until=None)
