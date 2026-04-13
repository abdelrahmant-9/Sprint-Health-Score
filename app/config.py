"""Application configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_METRICS_CONFIG = {
    "weights": {"commitment": 0.35, "carryover": 0.25, "cycle_time": 0.20, "bug_ratio": 0.20},
    "points": {"excellent": 100, "good": 70, "warning": 40, "poor": 0, "neutral": 70},
    "commitment": {
        "ideal_min_pct": 60,
        "ideal_max_pct": 85,
        "good_min_pct": 45,
        "warning_min_pct": 30,
        "extended_cap_score": 70,
    },
    "carryover": {"excellent_lt_pct": 15, "good_lte_pct": 30, "warning_lte_pct": 45, "extended_penalty": 10},
    "cycle_time": {"stable_abs_pct": 10, "good_increase_pct": 20, "warning_increase_pct": 30},
    "bug_ratio": {"excellent_lt_pct": 15, "good_lte_pct": 25, "warning_lte_pct": 35},
    "final_score": {
        "custom_formula": (
            "(commitment * weight_commitment) + "
            "(carryover * weight_carryover) + "
            "(cycle_time * weight_cycle_time) + "
            "(bug_ratio * weight_bug_ratio)"
        ),
        "round_result": True,
        "min_score": 0,
        "max_score": 100,
    },
    "ai": {"enabled": False, "model": "gpt-4o", "max_output_tokens": 350, "include_in_html": True, "include_in_slack": False},
    "activity_people": {"qa_names": [], "developer_names": []},
    "jira": {"base_url": "", "project_key": "", "board_id": ""},
    "branding": {"company_name": "Lumofy", "report_title": "Sprint Health Score", "logo_path": ""},
    "ui": {"particle_density": 400},
}

METRICS_CONFIG: dict = {}


class Settings(BaseSettings):
    """Runtime settings for sprint health execution."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    jira_base_url: str = Field(default="https://lumofyinc.atlassian.net")
    jira_email: str = Field(min_length=1)
    jira_api_token: str = Field(min_length=1)
    jira_project_key: str = Field(default="PM", min_length=1)
    jira_board_id: int | None = None
    jira_request_retries: int = Field(default=4, ge=1)
    jira_retry_delay_seconds: float = Field(default=2.0, ge=0.5)
    request_timeout_seconds: int = Field(default=15, ge=5)
    report_timezone: str = Field(default="Africa/Cairo")
    metrics_config_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1] / "health_metrics_config.json")
    issue_cache_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1] / "issue_history_cache.json")
    log_level: str = Field(default="INFO")
    debug: bool = Field(default=False)
    slack_webhook: str = Field(default="")
    slack_bot_token: str = Field(default="")
    slack_channel_id: str = Field(default="")
    report_format: str = Field(default="html")
    run_mode: str = Field(default="once")
    watch_interval_seconds: int = Field(default=60, ge=10)
    report_output_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[1] / "sprint_health_report.html")
    sqlite_path: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[1] / "data" / "sprint_health.db",
        description="Path to SQLite database file for sprint result history.",
    )
    api_base_url: str = Field(
        default="",
        description="Optional base URL for Streamlit to call API (e.g. http://api:8000).",
    )

    @field_validator("jira_base_url")
    @classmethod
    def _normalize_jira_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("report_format")
    @classmethod
    def _validate_report_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"html", "pdf"}:
            raise ValueError("REPORT_FORMAT must be either 'html' or 'pdf'")
        return normalized

    @field_validator("run_mode")
    @classmethod
    def _validate_run_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"once", "watch"}:
            raise ValueError("RUN_MODE must be either 'once' or 'watch'")
        return normalized

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.strip().upper() or "INFO"


def _deep_copy_config(data: dict) -> dict:
    """Return deep-copied config dictionary."""
    return json.loads(json.dumps(data))


def _merge_config(defaults: dict, loaded: dict | None) -> dict:
    """Recursively merge user config values onto defaults."""
    merged = _deep_copy_config(defaults)
    if not isinstance(loaded, dict):
        return merged
    for key, default_value in defaults.items():
        loaded_value = loaded.get(key)
        if isinstance(default_value, dict):
            merged[key] = _merge_config(default_value, loaded_value)
        elif loaded_value is not None:
            merged[key] = loaded_value
    return merged


def load_settings() -> Settings:
    """Load and validate application settings from environment variables."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration: {exc}") from exc


def save_metrics_config(config: dict) -> dict:
    """Persist merged metrics config and return normalized result."""
    settings = load_settings()
    merged = _merge_config(DEFAULT_METRICS_CONFIG, config)
    settings.metrics_config_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def load_metrics_config() -> dict:
    """Load metrics config from disk, creating defaults when absent."""
    settings = load_settings()
    if not settings.metrics_config_path.exists():
        return save_metrics_config(DEFAULT_METRICS_CONFIG)
    try:
        loaded = json.loads(settings.metrics_config_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return _deep_copy_config(DEFAULT_METRICS_CONFIG)
    return _merge_config(DEFAULT_METRICS_CONFIG, loaded)


def reload_metrics_config() -> dict:
    """Refresh process-level metrics config cache and return it."""
    global METRICS_CONFIG
    METRICS_CONFIG = load_metrics_config()
    return METRICS_CONFIG


reload_metrics_config()
