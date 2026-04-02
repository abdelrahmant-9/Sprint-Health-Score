import json
import os
from pathlib import Path


JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://lumofyinc.atlassian.net")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT = os.getenv("JIRA_PROJECT_KEY", "PM")
JIRA_BOARD_ID = int(os.getenv("JIRA_BOARD_ID")) if os.getenv("JIRA_BOARD_ID") else None
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL_ID")
REPORT_SITE_URL = os.getenv("REPORT_SITE_URL", "").strip()
REPORT_PDF_URL = os.getenv("REPORT_PDF_URL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.2").strip() or "gpt-5.2"
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
METRICS_CONFIG_PATH = Path(
    os.getenv("METRICS_CONFIG_PATH", str(Path(__file__).resolve().with_name("health_metrics_config.json")))
)

DONE_STATUSES_RAW = {"Done", "Closed", "Resolved", "DONE"}
BUG_TYPE = "Bug"
STORY_TYPE = "Story"
ENHANCEMENT_TYPES = {"Enhancement", "Improvement", "Task"}

DEFAULT_METRICS_CONFIG = {
    "weights": {
        "commitment": 0.35,
        "carryover": 0.25,
        "cycle_time": 0.20,
        "bug_ratio": 0.20,
    },
    "points": {
        "excellent": 100,
        "good": 70,
        "warning": 40,
        "poor": 0,
        "neutral": 70,
    },
    "commitment": {
        "ideal_min_pct": 85,
        "ideal_max_pct": 95,
        "good_min_pct": 70,
        "warning_min_pct": 50,
        "extended_cap_score": 70,
    },
    "carryover": {
        "excellent_lt_pct": 10,
        "good_lte_pct": 20,
        "warning_lte_pct": 30,
        "extended_penalty": 10,
    },
    "cycle_time": {
        "stable_abs_pct": 10,
        "good_increase_pct": 20,
        "warning_increase_pct": 30,
    },
    "bug_ratio": {
        "excellent_lt_pct": 15,
        "good_lte_pct": 25,
        "warning_lte_pct": 35,
    },
    "burndown": {
        "done_bonus": 5,
        "on_track_bonus": 3,
        "behind_small_max": 2,
        "behind_medium_max": 5,
        "behind_medium_penalty": -3,
        "behind_large_penalty": -5,
    },
    "stale_thresholds": {
        "bug_days": 2,
        "subtask_days": 2,
        "story_no_points_days": 4,
        "story_small_max_points": 3,
        "story_small_days": 3,
        "story_medium_max_points": 7,
        "story_medium_days": 5,
        "story_large_days": 7,
        "default_days": 3,
    },
    "labels": {
        "green_min_score": 85,
        "yellow_min_score": 70,
        "orange_min_score": 50,
    },
    "final_score": {
        "custom_formula": (
            "(commitment * weight_commitment) + "
            "(carryover * weight_carryover) + "
            "(cycle_time * weight_cycle_time) + "
            "(bug_ratio * weight_bug_ratio) + burndown"
        ),
        "round_result": True,
        "min_score": 0,
        "max_score": 100,
    },
    "ai": {
        "enabled": False,
        "model": OPENAI_MODEL,
        "max_output_tokens": 350,
        "include_in_html": True,
        "include_in_slack": False,
    },
}


def is_done(status_name: str) -> bool:
    return status_name.strip().upper() in {status.upper() for status in DONE_STATUSES_RAW}


def _deep_copy_config(data: dict) -> dict:
    return json.loads(json.dumps(data))


def _merge_config(defaults: dict, loaded: dict | None) -> dict:
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


def save_metrics_config(config: dict) -> dict:
    merged = _merge_config(DEFAULT_METRICS_CONFIG, config)
    METRICS_CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def load_metrics_config() -> dict:
    if not METRICS_CONFIG_PATH.exists():
        return save_metrics_config(DEFAULT_METRICS_CONFIG)
    try:
        loaded = json.loads(METRICS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[warn] Could not read metrics config, using defaults: {exc}")
        return _deep_copy_config(DEFAULT_METRICS_CONFIG)
    return _merge_config(DEFAULT_METRICS_CONFIG, loaded)


METRICS_CONFIG = load_metrics_config()


def reload_metrics_config() -> dict:
    global METRICS_CONFIG
    METRICS_CONFIG = load_metrics_config()
    return METRICS_CONFIG


def _config_points() -> dict:
    return METRICS_CONFIG["points"]


def _config_weights() -> dict:
    return METRICS_CONFIG["weights"]


def _config_final_score() -> dict:
    return METRICS_CONFIG["final_score"]


def _config_ai() -> dict:
    return METRICS_CONFIG["ai"]


def _format_decimal(value: float, places: int = 2) -> str:
    text = f"{value:.{places}f}"
    return text.rstrip("0").rstrip(".")


def _weight_text(name: str) -> str:
    return f"{_config_weights()[name]:.2f}"


def _signal_threshold_texts() -> dict:
    points = _config_points()
    commitment = METRICS_CONFIG["commitment"]
    carryover = METRICS_CONFIG["carryover"]
    cycle_time = METRICS_CONFIG["cycle_time"]
    bug_ratio = METRICS_CONFIG["bug_ratio"]

    commitment_ideal_start = _format_decimal(commitment["ideal_min_pct"])
    commitment_ideal_end = _format_decimal(commitment["ideal_max_pct"])
    commitment_good_start = _format_decimal(commitment["good_min_pct"])
    commitment_good_end = _format_decimal(commitment["ideal_min_pct"] - 1)
    commitment_warn_start = _format_decimal(commitment["warning_min_pct"])
    commitment_warn_end = _format_decimal(commitment["good_min_pct"] - 1)

    return {
        "commitment": (
            f"{commitment_ideal_start}-{commitment_ideal_end}% -> {points['excellent']} pts<br>"
            f"{commitment_good_start}-{commitment_good_end}% -> {points['good']} pts<br>"
            f"{commitment_warn_start}-{commitment_warn_end}% -> {points['warning']} pts<br>"
            f"&lt;{commitment_warn_start}% -> {points['poor']} pts"
        ),
        "carryover": (
            f"&lt;{_format_decimal(carryover['excellent_lt_pct'])}% -> {points['excellent']} pts<br>"
            f"{_format_decimal(carryover['excellent_lt_pct'])}-{_format_decimal(carryover['good_lte_pct'])}% -> {points['good']} pts<br>"
            f"{_format_decimal(carryover['good_lte_pct'])}-{_format_decimal(carryover['warning_lte_pct'])}% -> {points['warning']} pts<br>"
            f"&gt;{_format_decimal(carryover['warning_lte_pct'])}% -> {points['poor']} pts"
        ),
        "cycle_time": (
            f"within +/-{_format_decimal(cycle_time['stable_abs_pct'])}% -> {points['excellent']} pts<br>"
            f"up to +{_format_decimal(cycle_time['good_increase_pct'])}% -> {points['good']} pts<br>"
            f"up to +{_format_decimal(cycle_time['warning_increase_pct'])}% -> {points['warning']} pts<br>"
            f"&gt;+{_format_decimal(cycle_time['warning_increase_pct'])}% -> {points['poor']} pts"
        ),
        "bug_ratio": (
            f"&lt;{_format_decimal(bug_ratio['excellent_lt_pct'])}% -> {points['excellent']} pts<br>"
            f"{_format_decimal(bug_ratio['excellent_lt_pct'])}-{_format_decimal(bug_ratio['good_lte_pct'])}% -> {points['good']} pts<br>"
            f"{_format_decimal(bug_ratio['good_lte_pct'])}-{_format_decimal(bug_ratio['warning_lte_pct'])}% -> {points['warning']} pts<br>"
            f"&gt;{_format_decimal(bug_ratio['warning_lte_pct'])}% -> {points['poor']} pts"
        ),
    }


def get_stale_threshold(issue_type: str, story_points: float | None) -> int:
    thresholds = METRICS_CONFIG["stale_thresholds"]
    normalized_type = (issue_type or "").strip()
    if normalized_type == BUG_TYPE or normalized_type == "Sub-task":
        key = "bug_days" if normalized_type == BUG_TYPE else "subtask_days"
        return int(thresholds[key])
    if normalized_type in ENHANCEMENT_TYPES or normalized_type == STORY_TYPE:
        if story_points is None:
            return int(thresholds["story_no_points_days"])
        if story_points <= thresholds["story_small_max_points"]:
            return int(thresholds["story_small_days"])
        if story_points <= thresholds["story_medium_max_points"]:
            return int(thresholds["story_medium_days"])
        return int(thresholds["story_large_days"])
    return int(thresholds["default_days"])
