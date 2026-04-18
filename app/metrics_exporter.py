"""Prometheus metrics telemetry exporter definitions."""

from prometheus_client import Counter

JIRA_REQUESTS = Counter(
    "jira_api_requests_total",
    "Total requests sent upstream to Jira API",
    ["endpoint", "status"]
)

JIRA_CACHE = Counter(
    "jira_cache_requests_total",
    "Jira structural payload cache lookup states",
    ["result"]
)
