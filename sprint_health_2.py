import os
import sys
import argparse
import json
import ast
import hashlib
import socket
import subprocess
from html import escape
from pathlib import Path
import requests
import schedule
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from dashboard_ui import (
    write_html_report, 
    format_slack_message, 
    format_slack_site_message,
    _format_decimal,
    format_duration_hours
)

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

#  —  —  —  CONFIG  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

JIRA_BASE_URL   = os.getenv("JIRA_BASE_URL", "https://lumofyinc.atlassian.net")
JIRA_EMAIL      = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN  = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT    = os.getenv("JIRA_PROJECT_KEY", "PM")
JIRA_BOARD_ID   = int(os.getenv("JIRA_BOARD_ID")) if os.getenv("JIRA_BOARD_ID") else None
SLACK_TOKEN     = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL   = os.getenv("SLACK_CHANNEL_ID")
REPORT_SITE_URL = os.getenv("REPORT_SITE_URL", "").strip()
REPORT_PDF_URL  = os.getenv("REPORT_PDF_URL", "").strip()
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"
OPENAI_TIMEOUT  = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
JIRA_REQUEST_RETRIES = max(1, int(os.getenv("JIRA_REQUEST_RETRIES", "4")))
JIRA_RETRY_DELAY_SECONDS = max(1.0, float(os.getenv("JIRA_RETRY_DELAY_SECONDS", "2")))
#  —  —  PERSISTENCE & DATA DIR  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 
# For Railway/Docker with Volumes, use /app/data. Otherwise, fallback to local.
DATA_DIR = Path("/app/data") if Path("/app/data").exists() else Path(__file__).resolve().parent

METRICS_CONFIG_PATH = Path(os.getenv("METRICS_CONFIG_PATH", str(DATA_DIR / "health_metrics_config.json")))
ISSUE_CACHE_PATH    = Path(os.getenv("ISSUE_CACHE_PATH",    str(DATA_DIR / "issue_history_cache.json")))

# Global state for background thread communication
FORCE_REFRESH_REQUESTED = False
LOCAL_TIMEZONE = os.getenv("REPORT_TIMEZONE", "Africa/Cairo").strip() or "Africa/Cairo"
try:
    import pytz
    LOCAL_TZ = pytz.timezone(LOCAL_TIMEZONE)
except Exception:
    print(f"[warn] Could not load timezone '{LOCAL_TIMEZONE}', falling back to UTC.")
    LOCAL_TZ = timezone.utc

#  —  —  Status sets  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 
DONE_STATUSES_RAW = {"Done", "Closed", "Resolved", "DONE"}
STORY_DONE_LIKE_STATUSES = {"READY TO RELEASE"}

def is_done(status_name: str) -> bool:
    return status_name.strip().upper() in {s.upper() for s in DONE_STATUSES_RAW}


def is_effectively_done_status(status_name: str, issue_type: str = "") -> bool:
    normalized_type = (issue_type or "").strip().lower()
    normalized_status = (status_name or "").strip().upper()
    if normalized_status in {s.upper() for s in DONE_STATUSES_RAW}:
        return True
    if normalized_type == STORY_TYPE.lower() and normalized_status in STORY_DONE_LIKE_STATUSES:
        return True
    return False

QA_STATUSES          = {"Ready for Testing", "Ready For Testing", "READY FOR TESTING"}
QA_PENDING_STATUSES  = {"Pending Fixes", "Pending fixes", "PENDING FIXES"}
QA_PM_REVIEW         = {"Ready for PM Review", "Ready For PM Review", "READY FOR PM REVIEW"}

BUG_TYPE          = "Bug"
STORY_TYPE        = "Story"
ENHANCEMENT_TYPES = {"Enhancement", "Improvement", "Task"}

#  —  —  Issue type icons  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 
# Issue type icons moved to dashboard_ui.py

#  —  —  Scoring weights  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 
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
        "ideal_min_pct": 60,
        "ideal_max_pct": 85,
        "good_min_pct": 45,
        "warning_min_pct": 30,
        "extended_cap_score": 70,
    },
    "carryover": {
        "excellent_lt_pct": 15,
        "good_lte_pct": 30,
        "warning_lte_pct": 45,
        "extended_penalty": 10,
    },
    "scope_calculation": {
        "include_mid_sprint_added": False,
        "weighting": "hybrid_scope",
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
    "activity_people": {
        "qa_names": [],
        "developer_names": [],
    },
    "jira": {
        "base_url": "",
        "project_key": "",
        "board_id": "",
    },
    "branding": {
        "company_name": "Lumofy",
        "report_title": "Sprint Health Score",
        "logo_path": "",
    },
    "ui": {
        "particle_density": 400,
    },
}


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
        loaded = json.loads(METRICS_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception as e:
        print(f"[warn] Could not read metrics config, using defaults: {e}")
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


def _config_activity_people() -> dict:
    return METRICS_CONFIG.get("activity_people", {})


def _config_scope_calculation() -> dict:
    return METRICS_CONFIG.get("scope_calculation", {})


def _normalize_person_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _is_story_issue(issue: dict) -> bool:
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    issue_type = ((((fields.get("issuetype")) or {}).get("name")) or "").strip()
    return issue_type == STORY_TYPE


_SAFE_FORMULA_FUNCS = {"abs": abs, "max": max, "min": min, "round": round}


class _SafeFormulaEvaluator(ast.NodeVisitor):
    def __init__(self, variables: dict[str, float]):
        self.variables = variables

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Name(self, node):
        if node.id not in self.variables:
            raise ValueError(f"Unknown formula variable: {node.id}")
        return self.variables[node.id]

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Formula supports numbers only.")

    def visit_UnaryOp(self, node):
        value = self.visit(node.operand)
        if isinstance(node.op, ast.UAdd): return +value
        if isinstance(node.op, ast.USub): return -value
        raise ValueError("Unsupported unary operator.")

    def visit_BinOp(self, node):
        left, right = self.visit(node.left), self.visit(node.right)
        ops = {ast.Add: lambda a,b: a+b, ast.Sub: lambda a,b: a-b,
               ast.Mult: lambda a,b: a*b, ast.Div: lambda a,b: a/b,
               ast.FloorDiv: lambda a,b: a//b, ast.Mod: lambda a,b: a%b,
               ast.Pow: lambda a,b: a**b}
        fn = ops.get(type(node.op))
        if fn: return fn(left, right)
        raise ValueError("Unsupported operator.")

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Unsupported function call.")
        func = _SAFE_FORMULA_FUNCS.get(node.func.id)
        if func is None:
            raise ValueError(f"Unsupported function: {node.func.id}")
        return func(*[self.visit(arg) for arg in node.args])

    def generic_visit(self, node):
        raise ValueError(f"Unsupported syntax: {type(node).__name__}")


def _safe_eval_formula(expression: str, variables: dict[str, float]) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
        result = _SafeFormulaEvaluator(variables).visit(tree)
    except Exception as e:
        raise ValueError(f"Invalid formula: {e}") from e
    return float(result)


def _build_formula_context(c_score, co_score, cy_score, b_score, bd_nudge=0) -> dict:
    weights = _config_weights()
    return {
        "commitment": float(c_score), "carryover": float(co_score),
        "cycle_time": float(cy_score), "bug_ratio": float(b_score),
        "burndown": float(bd_nudge),
        "weight_commitment": float(weights["commitment"]),
        "weight_carryover": float(weights["carryover"]),
        "weight_cycle_time": float(weights["cycle_time"]),
        "weight_bug_ratio": float(weights["bug_ratio"]),
        "weighted_commitment": float(c_score * weights["commitment"]),
        "weighted_carryover": float(co_score * weights["carryover"]),
        "weighted_cycle_time": float(cy_score * weights["cycle_time"]),
        "weighted_bug_ratio": float(b_score * weights["bug_ratio"]),
    }


def get_stale_threshold(issue_type: str, story_points: float | None) -> int:
    thresholds = METRICS_CONFIG["stale_thresholds"]
    t = (issue_type or "").strip()
    if t == BUG_TYPE or t == "Feature Bug":
        return int(thresholds["bug_days"])
    if t == "Sub-task":
        return int(thresholds["subtask_days"])
    if t in ENHANCEMENT_TYPES or t == STORY_TYPE:
        if story_points is None:
            return int(thresholds["story_no_points_days"])
        if story_points <= thresholds["story_small_max_points"]:
            return int(thresholds["story_small_days"])
        if story_points <= thresholds["story_medium_max_points"]:
            return int(thresholds["story_medium_days"])
        return int(thresholds["story_large_days"])
    return int(thresholds["default_days"])


#  —  —  —  JIRA CLIENT  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def jira_get(path: str, params: dict = None) -> dict:
    last_error = None
    for attempt in range(1, JIRA_REQUEST_RETRIES + 1):
        try:
            url  = f"{JIRA_BASE_URL}/rest/api/3/{path}"
            resp = requests.get(
                url, params=params,
                auth=(JIRA_EMAIL, JIRA_API_TOKEN),
                headers={"Accept": "application/json"},
                timeout=15,
            )
            if resp.status_code == 410 and path == "search":
                url  = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
                resp = requests.get(url, params=params,
                                    auth=(JIRA_EMAIL, JIRA_API_TOKEN),
                                    headers={"Accept": "application/json"}, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            if attempt >= JIRA_REQUEST_RETRIES:
                break
            print(f"[warn] Jira API retry {attempt}/{JIRA_REQUEST_RETRIES - 1} failed for '{path}': {e}")
            time.sleep(JIRA_RETRY_DELAY_SECONDS * attempt)
    raise last_error


def agile_get(path: str, params: dict = None) -> dict:
    last_error = None
    for attempt in range(1, JIRA_REQUEST_RETRIES + 1):
        try:
            url  = f"{JIRA_BASE_URL}/rest/agile/1.0/{path}"
            resp = requests.get(url, params=params,
                                auth=(JIRA_EMAIL, JIRA_API_TOKEN),
                                headers={"Accept": "application/json"}, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            if attempt >= JIRA_REQUEST_RETRIES:
                break
            print(f"[warn] Jira Agile retry {attempt}/{JIRA_REQUEST_RETRIES - 1} failed for '{path}': {e}")
            time.sleep(JIRA_RETRY_DELAY_SECONDS * attempt)
    raise last_error


_BOARD_ID_CACHE = None
_SPRINT_CATALOG_CACHE: dict[int, dict[str, dict]] = {}
_ISSUE_HISTORY_CACHE: dict[str, dict] = {}
_ISSUE_CHANGELOG_CACHE: dict[str, dict] = {}
_ISSUE_CACHE_LOADED = False
_ISSUE_CACHE_DIRTY = False


def _serialize_datetime(value: datetime | None) -> str:
    if not value:
        return ""
    return value.astimezone(timezone.utc).isoformat()


def _deserialize_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def _load_issue_cache() -> None:
    global _ISSUE_CACHE_LOADED, _ISSUE_HISTORY_CACHE, _ISSUE_CHANGELOG_CACHE
    if _ISSUE_CACHE_LOADED:
        return
    _ISSUE_CACHE_LOADED = True
    if not ISSUE_CACHE_PATH.exists():
        return
    try:
        payload = json.loads(ISSUE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] Could not load issue cache: {e}")
        return

    history_cache: dict[str, dict] = {}
    for issue_key, row in (payload.get("history") or {}).items():
        history_cache[issue_key] = {
            "updated": row.get("updated", ""),
            "data": {
                "status": [
                    {
                        "from": item.get("from", ""),
                        "to": item.get("to", ""),
                        "datetime": _deserialize_datetime(item.get("datetime", "")),
                    }
                    for item in (row.get("data", {}) or {}).get("status", [])
                    if _deserialize_datetime(item.get("datetime", "")) is not None
                ],
                "sprint": [
                    {
                        "from": item.get("from", ""),
                        "to": item.get("to", ""),
                        "datetime": _deserialize_datetime(item.get("datetime", "")),
                    }
                    for item in (row.get("data", {}) or {}).get("sprint", [])
                    if _deserialize_datetime(item.get("datetime", "")) is not None
                ],
            },
        }
    changelog_cache: dict[str, dict] = {}
    for issue_key, row in (payload.get("changelog") or {}).items():
        changelog_cache[issue_key] = {
            "updated": row.get("updated", ""),
            "data": [
                {
                    "from": item.get("from", ""),
                    "to": item.get("to", ""),
                    "datetime": _deserialize_datetime(item.get("datetime", "")),
                    "actor": item.get("actor", "Unknown"),
                    "actor_account_id": item.get("actor_account_id", ""),
                }
                for item in (row.get("data") or [])
                if _deserialize_datetime(item.get("datetime", "")) is not None
            ],
        }
    _ISSUE_HISTORY_CACHE = history_cache
    _ISSUE_CHANGELOG_CACHE = changelog_cache


def _save_issue_cache() -> None:
    global _ISSUE_CACHE_DIRTY
    if not _ISSUE_CACHE_DIRTY:
        return
    payload = {"history": {}, "changelog": {}}
    for issue_key, row in _ISSUE_HISTORY_CACHE.items():
        payload["history"][issue_key] = {
            "updated": row.get("updated", ""),
            "data": {
                "status": [
                    {
                        "from": item.get("from", ""),
                        "to": item.get("to", ""),
                        "datetime": _serialize_datetime(item.get("datetime")),
                    }
                    for item in (row.get("data", {}) or {}).get("status", [])
                ],
                "sprint": [
                    {
                        "from": item.get("from", ""),
                        "to": item.get("to", ""),
                        "datetime": _serialize_datetime(item.get("datetime")),
                    }
                    for item in (row.get("data", {}) or {}).get("sprint", [])
                ],
            },
        }
    for issue_key, row in _ISSUE_CHANGELOG_CACHE.items():
        payload["changelog"][issue_key] = {
            "updated": row.get("updated", ""),
            "data": [
                {
                    "from": item.get("from", ""),
                    "to": item.get("to", ""),
                    "datetime": _serialize_datetime(item.get("datetime")),
                    "actor": item.get("actor", "Unknown"),
                    "actor_account_id": item.get("actor_account_id", ""),
                }
                for item in (row.get("data") or [])
            ],
        }
    try:
        ISSUE_CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _ISSUE_CACHE_DIRTY = False
    except Exception as e:
        print(f"[warn] Could not save issue cache: {e}")


def get_board_id():
    global _BOARD_ID_CACHE
    if _BOARD_ID_CACHE is not None:
        return _BOARD_ID_CACHE
    if JIRA_BOARD_ID:
        _BOARD_ID_CACHE = JIRA_BOARD_ID
        return _BOARD_ID_CACHE
    try:
        data   = agile_get("board", {"projectKeyOrId": JIRA_PROJECT, "maxResults": 50})
        boards = data.get("values", [])
        if not boards:
            return None
        scrum  = [b for b in boards if b.get("type") == "scrum"]
        chosen = scrum[0] if scrum else boards[0]
        _BOARD_ID_CACHE = chosen["id"]
        print(f"[board] Auto-detected: '{chosen['name']}' (id={_BOARD_ID_CACHE})")
        return _BOARD_ID_CACHE
    except Exception as e:
        print(f"[warn] Could not auto-detect board ID: {e}")
        return None


#  —  —  —  SPRINT STATE  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

class SprintState:
    def __init__(self, sprint: dict | None):
        self.sprint    = sprint or {}
        self.state     = self._detect()
        self.name      = self.sprint.get("name", "Unknown Sprint")
        self.start_str = _parse_sprint_date(self.sprint, "startDate", "start_date")
        self.end_str   = _parse_sprint_date(self.sprint, "endDate", "end_date", "completeDate")

    def _detect(self) -> str:
        if not self.sprint: return "empty"
        raw = (self.sprint.get("state") or "").lower()
        if raw == "active":
            end_str = _parse_sprint_date(self.sprint, "endDate", "end_date")
            if end_str:
                end_dt = _parse_date_str(end_str)
                if end_dt and datetime.now(timezone.utc).date() > end_dt.date():
                    return "extended"
            return "active"
        if raw == "closed": return "closed"
        return "active"

    @property
    def is_active(self): return self.state in ("active", "extended")

    @property
    def elapsed_days(self) -> int | None:
        start = _parse_date_str(self.start_str)
        if not start: return None
        return max(0, (datetime.now(timezone.utc).date() - start.date()).days)

    @property
    def total_days(self) -> int | None:
        start = _parse_date_str(self.start_str)
        end   = _parse_date_str(self.end_str)
        if not start or not end: return None
        return max(1, (end.date() - start.date()).days)

    @property
    def sprint_progress_pct(self) -> float | None:
        el, to = self.elapsed_days, self.total_days
        if el is None or to is None: return None
        return round(min(100.0, el / to * 100), 1)


def _parse_date_str(value: str) -> datetime | None:
    if not value: return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_sprint_date(sprint_info: dict, *keys: str) -> str:
    for key in keys:
        val = sprint_info.get(key)
        if val: return str(val)[:10]
    return ""


def fetch_active_sprint_from_board(board_id) -> dict | None:
    try:
        data    = agile_get(f"board/{board_id}/sprint", {"state": "active"})
        sprints = data.get("values", [])
        if sprints:
            s = sprints[0]
            print(f"[sprint] Active: '{s.get('name')}' (id={s.get('id')})")
            return s
        data   = agile_get(f"board/{board_id}/sprint", {"state": "closed"})
        closed = data.get("values", [])
        if closed:
            latest = sorted(closed, key=lambda s: s.get("endDate") or s.get("completeDate") or "", reverse=True)[0]
            print(f"[sprint] Using last closed: '{latest.get('name')}'")
            return latest
    except Exception as e:
        print(f"[warn] Could not fetch sprint: {e}")
    return None


def fetch_board_sprint_catalog(board_id: int) -> dict[str, dict]:
    cached = _SPRINT_CATALOG_CACHE.get(int(board_id))
    if cached is not None:
        return cached

    catalog: dict[str, dict] = {}
    for state in ("active", "closed", "future"):
        start_at = 0
        while True:
            data = agile_get(f"board/{board_id}/sprint", {
                "state": state,
                "startAt": start_at,
                "maxResults": 100,
            })
            values = data.get("values", []) or []
            if not values:
                break
            for sprint in values:
                name = (sprint.get("name") or "").strip()
                if name and name not in catalog:
                    catalog[name] = sprint
            start_at += len(values)
            if start_at >= int(data.get("total", 0) or 0):
                break

    _SPRINT_CATALOG_CACHE[int(board_id)] = catalog
    return catalog


def _jira_page_has_more(data: dict, fetched_count: int, page_size: int, items_key: str = "issues") -> bool:
    items = data.get(items_key, []) or []
    total = data.get("total")
    if isinstance(total, int) and total > 0:
        return fetched_count < total
    return len(items) >= page_size


def _page_signature(items: list[dict]) -> tuple[str, str, int]:
    if not items:
        return ("", "", 0)
    first_key = str(items[0].get("key", ""))
    last_key = str(items[-1].get("key", ""))
    return (first_key, last_key, len(items))


def _extend_unique_issues(target: list[dict], issues: list[dict], seen_keys: set[str]) -> int:
    new_count = 0
    for issue in issues:
        key = str(issue.get("key", "")).strip()
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        target.append(issue)
        new_count += 1
    return new_count


def _search_jira_issues(
    jql: str,
    fields: str,
    page_size: int = 100,
    repeated_warning: str = "[warn] Jira returned a repeated search page; stopping pagination early.",
    duplicates_warning: str = "[warn] Jira search page contained only duplicates; stopping pagination early.",
) -> list[dict]:
    all_issues: list[dict] = []
    seen_pages: set[tuple[str, str, int]] = set()
    seen_issue_keys: set[str] = set()
    next_page_token = None
    start_at = 0

    while True:
        params = {
            "jql": jql,
            "fields": fields,
            "maxResults": page_size,
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        else:
            params["startAt"] = start_at

        data = jira_get("search", params)
        issues = data.get("issues", []) or []
        if not issues:
            break

        signature = _page_signature(issues)
        new_unique = _extend_unique_issues(all_issues, issues, seen_issue_keys)
        if signature in seen_pages and new_unique == 0:
            raise RuntimeError(repeated_warning.replace("[warn] ", ""))
        seen_pages.add(signature)
        if new_unique == 0:
            raise RuntimeError(duplicates_warning.replace("[warn] ", ""))

        next_page_token = data.get("nextPageToken")
        if next_page_token:
            if data.get("isLast") is True:
                break
            continue

        start_at += len(issues)
        if data.get("isLast") is True or not _jira_page_has_more(data, start_at, page_size):
            break

    return all_issues


def _fetch_agile_issue_pages(path: str, fields: str, page_size: int = 50) -> list[dict]:
    all_issues: list[dict] = []
    seen_issue_keys: set[str] = set()
    seen_pages: set[tuple[str, str, int]] = set()
    start_at = 0
    expected_total = None

    while True:
        data = agile_get(path, {
            "fields": fields,
            "maxResults": page_size,
            "startAt": start_at,
        })
        issues = data.get("issues", []) or []
        if not issues:
            break
        if expected_total is None and isinstance(data.get("total"), int):
            expected_total = int(data.get("total"))

        signature = _page_signature(issues)
        new_unique = _extend_unique_issues(all_issues, issues, seen_issue_keys)
        if signature in seen_pages and new_unique == 0:
            raise RuntimeError("Jira Agile returned a repeated sprint issues page.")
        seen_pages.add(signature)
        if new_unique == 0:
            raise RuntimeError("Jira Agile sprint issues page contained only duplicates.")

        start_at += len(issues)
        if data.get("isLast") is True or not _jira_page_has_more(data, start_at, page_size):
            break

    if expected_total is not None and len(all_issues) < expected_total:
        raise RuntimeError(
            f"Jira Agile returned only {len(all_issues)} sprint issues out of expected {expected_total}."
        )
    return all_issues


def fetch_sprint_issues() -> tuple[list, dict]:
    all_issues, sprint_info = [], {}
    board_id  = get_board_id()
    sprint_id = None
    fields = (
        "summary,status,issuetype,created,resolutiondate,"
        "customfield_10016,customfield_10020,customfield_10021,"
        "assignee,labels,updated,customfield_10014,priority,parent,issuelinks"
    )
    if board_id:
        raw = fetch_active_sprint_from_board(board_id)
        if raw:
            sprint_id   = raw["id"]
            sprint_info = raw

    if sprint_id and board_id:
        all_issues = _fetch_agile_issue_pages(
            path=f"board/{board_id}/sprint/{sprint_id}/issue",
            fields=fields,
            page_size=50,
        )
    else:
        if sprint_id:
            jql = f"project = {JIRA_PROJECT} AND sprint = {sprint_id}"
        elif board_id:
            jql = f"project = {JIRA_PROJECT} AND sprint in activeSprints({board_id})"
        else:
            jql = f"project = {JIRA_PROJECT} AND sprint in activeSprints()"
        all_issues = _search_jira_issues(
            jql=jql,
            fields=fields,
            page_size=100,
            repeated_warning="[warn] Jira returned a repeated sprint page; stopping pagination early.",
            duplicates_warning="[warn] Jira sprint page contained only duplicates; stopping pagination early.",
        )

    if all_issues and not sprint_info:
        sprints = all_issues[0]["fields"].get("customfield_10020") or []
        active  = [s for s in sprints if s.get("state", "").lower() == "active"]
        sprint_info = active[0] if active else (sprints[0] if sprints else {})

    if not all_issues:
        print("[warn] No issues found in sprint.")
    return all_issues, sprint_info


def fetch_recent_project_issues(days: int = 7) -> list:
    """
    Fetch all project issues updated in the last N days (not limited to active sprint).
    """
    start_date = (datetime.now(LOCAL_TZ).date() - timedelta(days=max(0, days - 1))).strftime("%Y-%m-%d")
    jql = f'project = {JIRA_PROJECT} AND updated >= "{start_date}" ORDER BY updated DESC, key DESC'
    return _search_jira_issues(
        jql=jql,
        fields=(
            "summary,status,issuetype,created,resolutiondate,"
            "customfield_10016,customfield_10020,customfield_10021,"
            "assignee,labels,updated,customfield_10014,priority,parent,issuelinks"
        ),
        page_size=100,
        repeated_warning="[warn] Jira returned a repeated project issues page; stopping pagination early.",
        duplicates_warning="[warn] Jira project issues page contained only duplicates; stopping pagination early.",
    )


def fetch_recent_created_bugs(days: int = 7) -> list:
    """
    Fetch bugs and enhancements created in the last N days across the whole project.
    """
    start_date = (datetime.now(LOCAL_TZ).date() - timedelta(days=max(0, days - 1))).strftime("%Y-%m-%d")
    jql = (
        f"project = {JIRA_PROJECT} "
        "AND issuetype in (\"Bug\", \"Feature Bug\", \"Enhancement\", \"Improvement\") "
        f'AND created >= "{start_date}" '
        "ORDER BY created DESC, key DESC"
    )
    return _search_jira_issues(
        jql=jql,
        fields=(
            "summary,status,issuetype,created,updated,"
            "customfield_10016,customfield_10020,"
            "assignee,reporter,creator,parent,issuelinks"
        ),
        page_size=100,
        repeated_warning="[warn] Jira returned a repeated created-items page; stopping pagination early.",
        duplicates_warning="[warn] Jira created-items page contained only duplicates; stopping pagination early.",
    )


def fetch_last_n_sprints(n: int = 3) -> list[dict]:
    sprints_data = []
    try:
        issues = _search_jira_issues(
            jql=f"project = {JIRA_PROJECT} AND sprint in closedSprints() ORDER BY created DESC, key DESC",
            fields="resolutiondate,created,customfield_10020,status,issuetype",
            page_size=100,
            repeated_warning="[warn] Jira returned a repeated closed-sprints page; stopping pagination early.",
            duplicates_warning="[warn] Jira closed-sprints page contained only duplicates; stopping pagination early.",
        )
        sprint_map = {}
        for issue in issues:
            for s in (issue["fields"].get("customfield_10020") or []):
                sid = s.get("id")
                if sid not in sprint_map:
                    sprint_map[sid] = {"info": s, "issues": []}
                sprint_map[sid]["issues"].append(issue["fields"])
        sorted_sprints = sorted(sprint_map.values(), key=lambda x: x["info"]["id"], reverse=True)[:n]
        for sp in sorted_sprints:
            cycle_times, bug_count = [], 0
            for f in sp["issues"]:
                if ((f.get("issuetype") or {}).get("name") or "").strip() != STORY_TYPE:
                    if (f.get("issuetype") or {}).get("name") == BUG_TYPE:
                        bug_count += 1
                    continue
                ct = calc_cycle_time_days(f.get("created"), f.get("resolutiondate"))
                if ct is not None: cycle_times.append(ct)
            sprints_data.append({
                "name": sp["info"].get("name"),
                "avg_cycle_time": sum(cycle_times) / len(cycle_times) if cycle_times else None,
                "bugs": bug_count,
            })
    except Exception as e:
        print(f"[warn] Could not fetch closed sprints: {e}")
    return sprints_data


#  —  —  —  CHANGELOG & STATUS HISTORY  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def fetch_issue_changelog(issue_key: str, updated_hint: str = "") -> list[dict]:
    """Returns status change events for an issue, oldest first."""
    global _ISSUE_CACHE_DIRTY
    _load_issue_cache()
    cached = _ISSUE_CHANGELOG_CACHE.get(issue_key)
    if cached is not None and (not updated_hint or cached.get("updated") == updated_hint):
        cached_events = cached.get("data", [])
        cache_has_avatar_shape = all(
            isinstance(event, dict) and "actor_avatar" in event
            for event in cached_events
        )
        if cache_has_avatar_shape:
            return cached_events

    events = []
    try:
        start_at = 0
        while True:
            data = jira_get(f"issue/{issue_key}/changelog",
                            {"startAt": start_at, "maxResults": 100})
            for entry in data.get("values", []):
                created_raw = entry.get("created")
                actor_name = ((entry.get("author") or {}).get("displayName") or "Unknown")
                actor_account_id = ((entry.get("author") or {}).get("accountId") or "")
                actor_avatar = ((entry.get("author") or {}).get("avatarUrls") or {}).get("48x48", "")
                for item in entry.get("items", []):
                    if item.get("field") == "status":
                        dt = parse_jira_datetime(created_raw)
                        if dt:
                            events.append({
                                "from":     item.get("fromString", ""),
                                "to":       item.get("toString", ""),
                                "datetime": dt,
                                "actor":    actor_name,
                                "actor_account_id": actor_account_id,
                                "actor_avatar": actor_avatar,
                            })
            total = data.get("total", 0)
            start_at += len(data.get("values", []))
            if start_at >= total: break
    except Exception as e:
        print(f"[warn] changelog failed for {issue_key}: {e}")
    events = sorted(events, key=lambda x: x["datetime"])
    _ISSUE_CHANGELOG_CACHE[issue_key] = {"updated": updated_hint or "", "data": events}
    _ISSUE_CACHE_DIRTY = True
    return events


def fetch_issue_history(issue_key: str, updated_hint: str = "") -> dict:
    global _ISSUE_CACHE_DIRTY
    _load_issue_cache()
    cached = _ISSUE_HISTORY_CACHE.get(issue_key)
    if cached is not None and (not updated_hint or cached.get("updated") == updated_hint):
        return cached.get("data", {})

    history = {"status": [], "sprint": []}
    try:
        start_at = 0
        while True:
            data = jira_get(f"issue/{issue_key}/changelog", {"startAt": start_at, "maxResults": 100})
            values = data.get("values", []) or []
            for entry in values:
                created_raw = entry.get("created")
                dt = parse_jira_datetime(created_raw)
                if not dt:
                    continue
                for item in entry.get("items", []):
                    field_id = item.get("fieldId")
                    field_name = item.get("field")
                    if field_id == "status" or field_name == "status":
                        history["status"].append({
                            "from": item.get("fromString", ""),
                            "to": item.get("toString", ""),
                            "datetime": dt,
                        })
                    elif field_id == "customfield_10020" or field_name == "Sprint":
                        history["sprint"].append({
                            "from": item.get("fromString", ""),
                            "to": item.get("to", ""),
                            "datetime": dt,
                        })
            start_at += len(values)
            if start_at >= int(data.get("total", 0) or 0):
                break
    except Exception as e:
        print(f"[warn] issue history failed for {issue_key}: {e}")

    history["status"].sort(key=lambda row: row["datetime"])
    history["sprint"].sort(key=lambda row: row["datetime"])
    _ISSUE_HISTORY_CACHE[issue_key] = {"updated": updated_hint or "", "data": history}
    _ISSUE_CACHE_DIRTY = True
    return history


def calc_time_in_status(changelog: list[dict], target_status: str) -> float:
    """Total hours spent in target_status across all periods."""
    target_upper  = target_status.strip().upper()
    total_seconds = 0.0
    entered_at    = None
    now           = datetime.now(timezone.utc)
    for event in changelog:
        if event["to"].strip().upper() == target_upper:
            entered_at = event["datetime"]
        elif entered_at is not None and event["from"].strip().upper() == target_upper:
            total_seconds += (event["datetime"] - entered_at).total_seconds()
            entered_at = None
    if entered_at is not None:
        total_seconds += (now - entered_at).total_seconds()
    return round(total_seconds / 3600, 1)





def calc_cycle_time_median_per_type(issues: list) -> dict:
    import statistics
    type_cycle_times = {}
    for issue in issues:
        key = issue["key"]
        issue_type = issue["fields"]["issuetype"]["name"]
        hist = _ISSUE_HISTORY_CACHE.get(key, {})
        status_transitions = (hist.get("data") or {}).get("status") or []
        
        status_transitions = sorted(status_transitions, key=lambda x: str(x.get("datetime") or ""))
        
        first_in_progress = None
        first_done = None
        
        for tx in status_transitions:
            to_status = str(tx.get("to", "")).strip().upper()
            dt = tx.get("datetime")
            if not dt:
                continue
            if first_in_progress is None and "IN PROGRESS" in to_status:
                first_in_progress = dt
            if first_in_progress is not None and is_effectively_done_status(to_status, issue_type):
                if first_done is None:
                    first_done = dt
                    
        if first_in_progress and first_done:
            days = max(0.0, (first_done - first_in_progress).total_seconds() / 86400.0)
            if issue_type not in type_cycle_times:
                type_cycle_times[issue_type] = []
            type_cycle_times[issue_type].append(days)
            
    medians = {}
    for t, times in type_cycle_times.items():
        if times:
            medians[t] = statistics.median(times)
            
    return medians


def calc_status_bottlenecks(issues: list) -> dict:
    status_durations = {}
    total_blocked_seconds = 0
    total_active_seconds = 0
    status_hits = {}
    
    waiting_states = {
        "READY FOR TESTING", "PENDING FIXES", "READY FOR PM REVIEW", 
        "BLOCKED", "ON HOLD"
    }

    for issue in issues:
        key = issue["key"]
        issue_type = issue["fields"]["issuetype"]["name"]
        hist = _ISSUE_HISTORY_CACHE.get(key, {})
        status_transitions = (hist.get("data") or {}).get("status") or []
        created_str = issue["fields"].get("created")
        created_dt = _parse_date_str(created_str) if created_str else None
        
        hits_for_issue = set()
        
        if not status_transitions:
            s_name = issue["fields"]["status"]["name"]
            if not is_effectively_done_status(s_name, issue_type) and created_dt:
                dur = max(0.0, (datetime.now(timezone.utc) - created_dt).total_seconds())
                is_blocked = (s_name.upper() in waiting_states) or (s_name.upper() == "OPEN" and issue_type == BUG_TYPE)
                if is_blocked:
                    status_durations[s_name] = status_durations.get(s_name, 0) + dur
                    total_blocked_seconds += dur
                    hits_for_issue.add(s_name)
                elif s_name.upper() == "IN PROGRESS":
                    total_active_seconds += dur
            for s in hits_for_issue:
                status_hits[s] = status_hits.get(s, 0) + 1
            continue
            
        status_transitions = sorted(status_transitions, key=lambda x: str(x.get("datetime") or ""))
        
        last_time = created_dt
        last_status = status_transitions[0]["from"] if status_transitions[0].get("from") else "Open"
        current_time_dt = datetime.now(timezone.utc)
        
        for tx in status_transitions:
            to_status = tx["to"]
            dt = tx["datetime"]
            if not dt:
                continue
            
            if last_time and last_status:
                duration = max(0.0, (dt - last_time).total_seconds())
                is_blocked = (last_status.upper() in waiting_states) or (last_status.upper() == "OPEN" and issue_type == BUG_TYPE)
                if is_blocked:
                    status_durations[last_status] = status_durations.get(last_status, 0) + duration
                    total_blocked_seconds += duration
                    hits_for_issue.add(last_status)
                elif last_status.upper() == "IN PROGRESS":
                    total_active_seconds += duration
                    
            last_status = to_status
            last_time = dt
            
        if last_status and not is_effectively_done_status(last_status, issue_type) and last_time:
            duration = max(0.0, (current_time_dt - last_time).total_seconds())
            is_blocked = (last_status.upper() in waiting_states) or (last_status.upper() == "OPEN" and issue_type == BUG_TYPE)
            if is_blocked:
                status_durations[last_status] = status_durations.get(last_status, 0) + duration
                total_blocked_seconds += duration
                hits_for_issue.add(last_status)
            elif last_status.upper() == "IN PROGRESS":
                total_active_seconds += duration

        for s in hits_for_issue:
            status_hits[s] = status_hits.get(s, 0) + 1

    total_execution_seconds = total_active_seconds + total_blocked_seconds
    blocked_ratio_pct = (total_blocked_seconds / total_execution_seconds * 100.0) if total_execution_seconds > 0 else 0.0
    
    sorted_bottlenecks = sorted(status_durations.items(), key=lambda x: x[1], reverse=True)
    top_bottlenecks = []
    
    for st_name, seconds in sorted_bottlenecks[:3]:
        pct = (seconds / total_blocked_seconds * 100.0) if total_blocked_seconds > 0 else 0.0
        top_bottlenecks.append({
            "name": st_name,
            "pct": pct,
            "days": seconds / 86400.0
        })
        
    worst_bottleneck_name = None
    worst_bottleneck_days = 0.0
    if sorted_bottlenecks:
        worst_name = sorted_bottlenecks[0][0]
        worst_bottleneck_name = worst_name
        hits = status_hits.get(worst_name, 1)
        worst_bottleneck_days = (sorted_bottlenecks[0][1] / 86400.0) / hits if hits > 0 else 0.0
        
    return {
        "blocked_ratio_pct": blocked_ratio_pct,
        "top_bottlenecks": top_bottlenecks,
        "worst_bottleneck_name": worst_bottleneck_name,
        "worst_bottleneck_days": worst_bottleneck_days
    }


def calc_dev_progress_days(changelog: list[dict]) -> int:
    """
    Development duration:
    from first transition to In Progress
    until first transition to Ready for Testing.
    If not reached Ready for Testing yet, count until now.
    """
    in_progress_started_at = None
    qa_upper = {s.upper() for s in QA_STATUSES}

    for event in changelog:
        to_upper = (event.get("to") or "").strip().upper()
        if in_progress_started_at is None and to_upper == "IN PROGRESS":
            in_progress_started_at = event.get("datetime")
            continue
        if in_progress_started_at is not None and to_upper in qa_upper:
            ended_at = event.get("datetime")
            if ended_at:
                return max(0, int((ended_at - in_progress_started_at).total_seconds() // 86400))
            return 0

    if in_progress_started_at is not None:
        return max(0, int((datetime.now(timezone.utc) - in_progress_started_at).total_seconds() // 86400))
    return 0


def get_status_transitions_today(changelog: list[dict]) -> list[dict]:
    today_local = datetime.now(LOCAL_TZ).date()
    return [e for e in changelog if e["datetime"].astimezone(LOCAL_TZ).date() == today_local]


def get_status_transitions_on_date(changelog: list[dict], target_date) -> list[dict]:
    return [e for e in changelog if e["datetime"].astimezone(LOCAL_TZ).date() == target_date]


def updated_on_date(updated: str, target_date) -> bool:
    dt = parse_jira_datetime(updated)
    return bool(dt and dt.astimezone(LOCAL_TZ).date() == target_date)


def _activity_date_key(target_date) -> str:
    return target_date.isoformat()


def _activity_date_label(target_date) -> str:
    today_local = datetime.now(LOCAL_TZ).date()
    if target_date == today_local:
        return f"Today Â· {target_date.strftime('%d %b')}"
    if target_date == today_local - timedelta(days=1):
        return f"Yesterday Â· {target_date.strftime('%d %b')}"
    return target_date.strftime("%a Â· %d %b")


def _recent_activity_dates(days: int = 7) -> list:
    today_local = datetime.now(LOCAL_TZ).date()
    return [today_local - timedelta(days=offset) for offset in range(max(1, days))]


def _sprint_activity_dates(sprint_start_str: str, fallback_days: int = 7) -> list:
    today_local = datetime.now(LOCAL_TZ).date()
    sprint_start_dt = _parse_date_str(sprint_start_str)
    if sprint_start_dt:
        sprint_start_local = sprint_start_dt.astimezone(LOCAL_TZ).date()
        if sprint_start_local > today_local:
            return [today_local]
    else:
        sprint_start_local = today_local

    # Work week is Sunday -> Thursday, excluding Friday and Saturday.
    days_since_sunday = (today_local.weekday() + 1) % 7
    week_start = today_local - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=4)

    # On Wednesday, include Thursday as the final workday option as requested.
    if today_local.weekday() == 2:
        visible_end = min(week_end, today_local + timedelta(days=1))
    elif today_local.weekday() in {4, 5}:  # Friday / Saturday
        visible_end = week_end
    else:
        visible_end = min(today_local, week_end)

    visible_start = max(week_start, sprint_start_local)
    if visible_start > visible_end:
        return [today_local]

    return [
        visible_start + timedelta(days=offset)
        for offset in range((visible_end - visible_start).days + 1)
        if (visible_start + timedelta(days=offset)).weekday() not in {4, 5}
    ]


#  —  —  —  BURNDOWN  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def build_burndown(issues: list, ss: SprintState) -> dict:
    if not ss.start_str or not ss.end_str: return {}
    start_dt = _parse_date_str(ss.start_str)
    end_dt   = _parse_date_str(ss.end_str)
    now_dt   = datetime.now(timezone.utc)
    if not start_dt or not end_dt or end_dt <= start_dt: return {}

    story_issues = [issue for issue in issues if _is_story_issue(issue)]
    total_days    = max(1, (end_dt.date() - start_dt.date()).days)
    elapsed_days  = max(0, (now_dt.date() - start_dt.date()).days)
    effective_days = elapsed_days if ss.state == "extended" else min(elapsed_days, total_days)
    total_issues  = round(sum(get_issue_weight(issue) for issue in story_issues), 1)

    completions_by_day: dict[int, float] = {}
    for issue in story_issues:
        completion_dt = get_effective_completion_datetime(issue)
        if completion_dt and completion_dt >= start_dt:
            day_idx = (completion_dt.date() - start_dt.date()).days
            completions_by_day[day_idx] = round(
                completions_by_day.get(day_idx, 0.0) + get_issue_weight(issue), 1
            )

    actual_line: list[float] = []
    remaining = total_issues
    for d in range(effective_days + 1):
        remaining -= completions_by_day.get(d, 0)
        actual_line.append(round(max(0.0, remaining), 1))

    ideal_line = [round(total_issues * (1 - d / total_days), 1) for d in range(total_days + 1)]
    current_remaining = actual_line[-1] if actual_line else total_issues
    ideal_at_today    = ideal_line[min(effective_days, total_days)]
    done_count = round(total_issues - current_remaining, 1)
    velocity   = round(done_count / effective_days, 2) if effective_days > 0 else 0.0

    if velocity > 0 and current_remaining > 0:
        projected_end = (now_dt + timedelta(days=current_remaining / velocity)).strftime("%Y-%m-%d")
    elif current_remaining == 0:
        projected_end = "Done âœ“"
    else:
        projected_end = "N/A"

    day_labels   = [(start_dt + timedelta(days=d)).strftime("%m/%d") for d in range(effective_days + 1)]
    ideal_labels = [(start_dt + timedelta(days=d)).strftime("%m/%d") for d in range(total_days + 1)]
    behind_by    = round(current_remaining - ideal_at_today, 1)

    return {
        "total_issues": total_issues, "total_scope": total_issues, "total_days": total_days,
        "elapsed_days": effective_days, "actual_line": actual_line,
        "ideal_line": ideal_line, "day_labels": day_labels,
        "ideal_labels": ideal_labels, "current_remaining": current_remaining,
        "completed_scope": done_count,
        "ideal_remaining": ideal_at_today, "velocity": velocity,
        "projected_end": projected_end, "on_track": current_remaining <= ideal_at_today,
        "behind_by": behind_by, "is_extended": ss.state == "extended",
    }


#  —  —  —  CALCULATIONS  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def calc_cycle_time_days(created: str, resolved: str) -> float | None:
    if not created or not resolved: return None
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
        r = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
        return max(0.0, (r - c).total_seconds() / 86400)
    except Exception:
        return None


def parse_jira_datetime(value: str) -> datetime | None:
    if not value: return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def get_effective_completion_datetime(issue: dict) -> datetime | None:
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
    current_status = ((fields.get("status") or {}).get("name") or "").strip()
    if not is_effectively_done_status(current_status, issue_type):
        return None

    resolution_dt = parse_jira_datetime(fields.get("resolutiondate"))
    if resolution_dt:
        return resolution_dt

    issue_key = issue.get("key", "")
    if issue_key:
        history = fetch_issue_history(issue_key, fields.get("updated", "") or "")
        for event in (history.get("status") or []):
            if is_effectively_done_status(event.get("to", ""), issue_type):
                return event.get("datetime")

    return parse_jira_datetime(fields.get("updated"))


def issue_age_days(created: str) -> float | None:
    dt = parse_jira_datetime(created)
    if not dt: return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def days_since_updated(updated: str) -> float | None:
    dt = parse_jira_datetime(updated)
    if not dt: return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def updated_today(updated: str) -> bool:
    dt = parse_jira_datetime(updated)
    if not dt: return False
    return dt.astimezone(LOCAL_TZ).date() == datetime.now(LOCAL_TZ).date()


def get_issue_weight(issue: dict) -> float:
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    story_points = fields.get("customfield_10016")
    if story_points is None:
        return 1.0
    try:
        points = float(story_points)
    except (TypeError, ValueError):
        return 1.0
    return points if points > 0 else 1.0


def get_work_weight(issue: dict, weighting: str = "hybrid_scope") -> float:
    mode = (weighting or "hybrid_scope").strip().lower()
    if mode == "item_count":
        return 1.0
    return get_issue_weight(issue)


def calculate_carryover_metrics(
    issues: list,
    sprint_start_dt: datetime | None,
    include_mid_sprint_added: bool = False,
    weighting: str = "hybrid_scope",
) -> dict:
    committed_work = 0.0
    completed_work = 0.0
    carried_over_work = 0.0
    committed_items = 0
    completed_items = 0
    carried_over_items = 0

    for issue in issues:
        fields = issue.get("fields", {})
        created_dt = parse_jira_datetime(fields.get("created"))
        if not include_mid_sprint_added and sprint_start_dt and created_dt and created_dt > sprint_start_dt:
            continue

        weight = get_work_weight(issue, weighting=weighting)
        committed_work += weight
        committed_items += 1

        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if is_effectively_done_status(status_name, issue_type):
            completed_work += weight
            completed_items += 1
        else:
            carried_over_work += weight
            carried_over_items += 1

    carryover_rate_pct = round((carried_over_work / committed_work * 100), 1) if committed_work > 0 else 0.0
    completion_rate_pct = round((completed_work / committed_work * 100), 1) if committed_work > 0 else 0.0

    return {
        "committed_work": round(committed_work, 1),
        "completed_work": round(completed_work, 1),
        "carried_over_work": round(carried_over_work, 1),
        "carryover_rate_pct": carryover_rate_pct,
        "completion_rate_pct": completion_rate_pct,
        "committed_items": committed_items,
        "completed_items": completed_items,
        "carried_over_items": carried_over_items,
        "include_mid_sprint_added": include_mid_sprint_added,
        "weighting": weighting,
    }


def _parse_sprint_field_date(value) -> datetime | None:
    if not value:
        return None
    return parse_jira_datetime(str(value))


def _get_previous_sprints_for_issue(issue: dict, current_sprint: dict | None, current_sprint_start_dt: datetime | None) -> list[dict]:
    current_sprint = current_sprint or {}
    current_sprint_id = current_sprint.get("id")
    issue_sprints = ((issue.get("fields") or {}).get("customfield_10020") or [])
    previous = []
    for sprint in issue_sprints:
        if not isinstance(sprint, dict):
            continue
        if current_sprint_id is not None and sprint.get("id") == current_sprint_id:
            continue
        sprint_end = _parse_sprint_field_date(sprint.get("completeDate") or sprint.get("endDate") or sprint.get("end_date"))
        sprint_start = _parse_sprint_field_date(sprint.get("startDate") or sprint.get("start_date"))
        if current_sprint_start_dt and sprint_end and sprint_end > current_sprint_start_dt:
            continue
        if current_sprint_start_dt and sprint_start and sprint_start >= current_sprint_start_dt:
            continue
        previous.append(sprint)
    return previous


def calculate_carried_in_work_metrics(
    issues: list,
    current_sprint: dict | None,
    current_sprint_start_dt: datetime | None,
    weighting: str = "hybrid_scope",
    include_item_list: bool = False,
) -> dict:
    total_work = 0.0
    carried_in_work = 0.0
    total_items = 0
    carried_in_items = 0
    carried_in_issue_keys: list[str] = []

    for issue in issues:
        fields = issue.get("fields", {})
        weight = get_work_weight(issue, weighting=weighting)
        total_work += weight
        total_items += 1

        previous_sprints = _get_previous_sprints_for_issue(issue, current_sprint, current_sprint_start_dt)
        if not previous_sprints:
            continue

        resolution_dt = parse_jira_datetime(fields.get("resolutiondate"))
        was_completed_before_current_sprint = bool(
            current_sprint_start_dt and resolution_dt and resolution_dt <= current_sprint_start_dt
        )
        if was_completed_before_current_sprint:
            continue

        carried_in_work += weight
        carried_in_items += 1
        if include_item_list:
            carried_in_issue_keys.append(issue.get("key", ""))

    carried_in_rate_pct = round((carried_in_work / total_work * 100), 1) if total_work > 0 else 0.0
    result = {
        "total_work": round(total_work, 1),
        "carried_in_work": round(carried_in_work, 1),
        "carried_in_rate_pct": carried_in_rate_pct,
        "total_items": total_items,
        "carried_in_items": carried_in_items,
        "weighting": weighting,
    }
    if include_item_list:
        result["carried_in_issue_keys"] = carried_in_issue_keys
    return result


def _status_at_datetime(current_status: str, status_events: list[dict], target_dt: datetime | None) -> str:
    if not target_dt:
        return (current_status or "").strip()
    if not status_events:
        return (current_status or "").strip()

    status_name = (status_events[0].get("from") or current_status or "").strip()
    for event in status_events:
        if event["datetime"] <= target_dt:
            status_name = (event.get("to") or status_name).strip()
        else:
            break
    return status_name


def _find_transition_into_current_sprint(sprint_events: list[dict], current_sprint_name: str) -> dict | None:
    current_name = (current_sprint_name or "").strip()
    if not current_name:
        return None
    matched = None
    for event in sprint_events:
        to_name = (event.get("to") or "").strip()
        if to_name == current_name:
            matched = event
    return matched


def calculate_sprint_carryover_metrics(
    issues: list,
    current_sprint: dict | None,
    current_sprint_start_dt: datetime | None,
    weighting: str = "hybrid_scope",
    include_item_list: bool = False,
) -> dict:
    current_sprint = current_sprint or {}
    current_sprint_name = (current_sprint.get("name") or "").strip()
    board_id = current_sprint.get("boardId") or get_board_id()
    sprint_catalog = fetch_board_sprint_catalog(int(board_id)) if board_id else {}

    total_work = 0.0
    total_items = 0

    historical_work = 0.0
    historical_items = 0
    historical_issue_keys: list[str] = []

    official_rollover_work = 0.0
    official_rollover_items = 0
    official_rollover_issue_keys: list[str] = []

    for issue in issues:
        fields = issue.get("fields", {})
        key = issue.get("key", "")
        weight = get_work_weight(issue, weighting=weighting)
        total_work += weight
        total_items += 1

        history = fetch_issue_history(key, fields.get("updated", "") or "")
        sprint_events = history.get("sprint", [])
        status_events = history.get("status", [])
        transition_into_current = _find_transition_into_current_sprint(sprint_events, current_sprint_name)
        if not transition_into_current:
            continue

        current_status = ((fields.get("status") or {}).get("name") or "").strip()
        status_at_current_start = _status_at_datetime(current_status, status_events, current_sprint_start_dt)
        if not is_effectively_done_status(status_at_current_start, (fields.get("issuetype") or {}).get("name", "")):
            historical_work += weight
            historical_items += 1
            if include_item_list:
                historical_issue_keys.append(key)

        previous_sprint_name = (transition_into_current.get("from") or "").strip()
        previous_sprint = sprint_catalog.get(previous_sprint_name) or {}
        previous_sprint_end_dt = _parse_sprint_field_date(
            previous_sprint.get("completeDate") or previous_sprint.get("endDate") or previous_sprint.get("end_date")
        )
        if previous_sprint_end_dt:
            status_at_previous_close = _status_at_datetime(current_status, status_events, previous_sprint_end_dt)
            if not is_effectively_done_status(status_at_previous_close, (fields.get("issuetype") or {}).get("name", "")):
                official_rollover_work += weight
                official_rollover_items += 1
                if include_item_list:
                    official_rollover_issue_keys.append(key)

    result = {
        "total_work": round(total_work, 1),
        "total_items": total_items,
        "historical_carried_in_work": round(historical_work, 1),
        "historical_carried_in_items": historical_items,
        "historical_carried_in_rate_pct": round((historical_work / total_work * 100), 1) if total_work > 0 else 0.0,
        "official_rollover_work": round(official_rollover_work, 1),
        "official_rollover_items": official_rollover_items,
        "official_rollover_rate_pct": round((official_rollover_work / total_work * 100), 1) if total_work > 0 else 0.0,
        "weighting": weighting,
    }
    if include_item_list:
        result["historical_carried_in_issue_keys"] = historical_issue_keys
        result["official_rollover_issue_keys"] = official_rollover_issue_keys
    return result


#  —  —  —  SCORING  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def _progress_weight(sprint_pct: float | None) -> float:
    if sprint_pct is None: return 1.0
    if sprint_pct < 30: return sprint_pct / 30
    if sprint_pct < 60: return 0.5 + (sprint_pct - 30) / 60
    return 1.0


def _blend(real_score: int, sprint_pct: float | None, neutral: int = 70) -> int:
    w = _progress_weight(sprint_pct)
    return round(neutral + w * (real_score - neutral))


def score_commitment(completed, committed, sprint_pct=None, is_extended=False):
    points, cfg = _config_points(), METRICS_CONFIG["commitment"]
    if committed == 0: return points["neutral"], 0.0
    pct = completed / committed * 100
    if cfg["ideal_min_pct"] <= pct <= cfg["ideal_max_pct"]: raw = points["excellent"]
    elif pct >= cfg["good_min_pct"]: raw = points["good"]
    elif pct >= cfg["warning_min_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    if is_extended: score = min(score, int(cfg["extended_cap_score"]))
    return score, round(pct, 1)


def score_carryover(carried, total, sprint_pct=None, is_extended=False):
    points, cfg = _config_points(), METRICS_CONFIG["carryover"]
    if total == 0: return points["neutral"], 0.0
    pct = carried / total * 100
    if pct < cfg["excellent_lt_pct"]: raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]: raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    return score, round(pct, 1)


def score_cycle_time(current_avg, prev_avg, sprint_pct=None):
    points, cfg = _config_points(), METRICS_CONFIG["cycle_time"]
    if current_avg is None or prev_avg is None or prev_avg == 0:
        return points["neutral"], None
    diff_pct = (current_avg - prev_avg) / prev_avg * 100
    if abs(diff_pct) <= cfg["stable_abs_pct"]: raw = points["excellent"]
    elif diff_pct <= cfg["good_increase_pct"]: raw = points["good"]
    elif diff_pct <= cfg["warning_increase_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(diff_pct, 1)


def score_bug_ratio(new_bugs, total, sprint_pct=None):
    points, cfg = _config_points(), METRICS_CONFIG["bug_ratio"]
    if total == 0 and new_bugs == 0: return points["neutral"], 0.0
    denom = total if total > 0 else 1
    pct   = new_bugs / denom * 100
    if pct < cfg["excellent_lt_pct"]: raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]: raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(pct, 1)


def score_burndown(bd: dict, sprint_pct) -> int:
    cfg = METRICS_CONFIG["burndown"]
    if not bd: return 0
    if bd.get("current_remaining", 0) == 0: return int(cfg["done_bonus"])
    if bd.get("on_track"): return int(cfg["on_track_bonus"])
    behind = bd.get("behind_by", 0)
    if behind <= cfg["behind_small_max"]: return 0
    if behind <= cfg["behind_medium_max"]: return int(cfg["behind_medium_penalty"])
    return int(cfg["behind_large_penalty"])


def calc_health_score(c_score, co_score, cy_score, b_score, bd_nudge=0) -> dict:
    cfg     = _config_final_score()
    formula = (cfg.get("custom_formula") or "").strip() or DEFAULT_METRICS_CONFIG["final_score"]["custom_formula"]
    context = _build_formula_context(c_score, co_score, cy_score, b_score, bd_nudge)
    raw     = _safe_eval_formula(formula, context)
    bounded = max(float(cfg.get("min_score", 0)), min(float(cfg.get("max_score", 100)), raw))
    final   = round(bounded) if cfg.get("round_result", True) else bounded
    return {
        "score": int(round(final)), "raw_score": raw,
        "formula": formula, "context": context,
        "weighted_breakdown": {
            "commitment": round(context["weighted_commitment"], 1),
            "carryover":  round(context["weighted_carryover"],  1),
            "cycle_time": round(context["weighted_cycle_time"], 1),
            "bug_ratio":  round(context["weighted_bug_ratio"],  1),
        },
    }


def health_label(score: int) -> tuple[str, str]:
    labels = METRICS_CONFIG["labels"]
    if score >= labels["green_min_score"]:  return ":green_circle:",  "Predictable sprint"
    if score >= labels["yellow_min_score"]: return ":yellow_circle:", "Some instability"
    if score >= labels["orange_min_score"]: return ":orange_circle:", "Execution issues"
    return ":red_circle:", "Sprint breakdown"


def _extract_response_text(payload: dict) -> str:
    output_text = (payload.get("output_text") or "").strip()
    if output_text: return output_text
    for item in payload.get("output", []):
        if item.get("type") != "message": continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    return ""


def generate_ai_insights(report: dict) -> dict | None:
    cfg = _config_ai()
    if not cfg.get("enabled"): return None
    if not OPENAI_API_KEY:
        return {"status": "disabled", "title": "AI insights unavailable",
                "summary": "Set OPENAI_API_KEY in .env to enable AI recommendations.", "actions": []}
    payload = {
        "model": (cfg.get("model") or OPENAI_MODEL).strip() or OPENAI_MODEL,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": (
                "You analyze sprint health reports. Reply in JSON only with keys "
                "title, summary, actions. actions must be an array of up to 3 short strings."
            )}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(report, ensure_ascii=False)}]},
        ],
        "max_output_tokens": int(cfg.get("max_output_tokens", 350)),
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=OPENAI_TIMEOUT,
        )
        resp.raise_for_status()
        parsed  = json.loads(_extract_response_text(resp.json()))
        actions = parsed.get("actions") if isinstance(parsed.get("actions"), list) else []
        return {
            "status": "ok",
            "title":   str(parsed.get("title") or "AI insight").strip(),
            "summary": str(parsed.get("summary") or "").strip(),
            "actions": [str(i).strip() for i in actions if str(i).strip()][:3],
        }
    except Exception as e:
        return {"status": "error", "title": "AI insight failed",
                "summary": f"AI request failed: {e}", "actions": []}


#  —  —  —  DEVELOPER & QA ACTIVITY  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def build_developer_activity(
    issues: list,
    sprint_start_str: str,
    target_dates: list | None = None,
    allowed_qa_names: set[str] | None = None,
    allowed_dev_names: set[str] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """
    Returns:
      dev_activity  —  developer-owned status transitions grouped by date then assignee
      qa_activity   —  QA status transitions grouped by date then actor
    """
    qa_filter = allowed_qa_names or set()
    dev_filter = allowed_dev_names or set()
    target_dates = target_dates or _sprint_activity_dates(sprint_start_str)
    date_keys = [_activity_date_key(target_date) for target_date in target_dates]
    dev_maps: dict[str, dict[str, dict]] = {date_key: {} for date_key in date_keys}
    qa_items_by_date: dict[str, list[dict]] = {date_key: [] for date_key in date_keys}
    qa_upper = {s.upper() for s in QA_STATUSES}
    pending_upper = {s.upper() for s in QA_PENDING_STATUSES}
    pm_review_upper = {s.upper() for s in QA_PM_REVIEW}

    for issue in issues:
        f            = issue["fields"]
        updated_raw  = f.get("updated")
        assignee     = f.get("assignee")
        issue_type   = (f.get("issuetype") or {}).get("name", "")
        status_name  = f["status"]["name"]
        story_points = f.get("customfield_10016")
        key          = issue.get("key", "")
        summary      = f.get("summary", "")
        url          = f"{JIRA_BASE_URL}/browse/{key}"
        linked_story, linked_story_summary = _extract_linked_story_details(f)

        #  —  —  Fetch changelog for every sprint issue  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 
        changelog = fetch_issue_changelog(key, updated_raw or "")

        # Time in "IN TESTING" from entry until it exits to the next QA outcome.
        time_in_rft = calc_time_in_status(changelog, "IN TESTING")

        #  —  —  Developer Activity  —  developer-owned transitions today  —  —  —  —  —  —  —  —  —  —  — 
        dev_name   = (assignee or {}).get("displayName", "Unassigned")
        dev_avatar = (assignee or {}).get("avatarUrls", {}).get("48x48", "")
        dev_name_norm = _normalize_person_name(dev_name)
        if dev_filter and dev_name_norm not in dev_filter:
            continue

        active_days = calc_dev_progress_days(changelog)

        stale_threshold = get_stale_threshold(issue_type, story_points)
        is_stale        = active_days > stale_threshold and not is_effectively_done_status(status_name, issue_type)

        assignee_account_id = (assignee or {}).get("accountId", "")
        for target_date in target_dates:
            date_key = _activity_date_key(target_date)
            day_transitions = get_status_transitions_on_date(changelog, target_date)

            for t in day_transitions:
                actor_norm = _normalize_person_name(t.get("actor", ""))
                if qa_filter and actor_norm not in qa_filter:
                    continue

                to_upper = t["to"].strip().upper()
                from_upper = t["from"].strip().upper()

                if to_upper in qa_upper:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "started_testing",
                        "â–¶ Started Testing", "#1a6bff", time_in_rft, url, story_points
                    ))
                elif from_upper in qa_upper and to_upper in pending_upper:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "pending_fixes",
                        "ðŸ”„ Pending Fixes", "#fbbf24", time_in_rft, url, story_points
                    ))
                elif from_upper in qa_upper and to_upper in pm_review_upper:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "pm_review",
                        "âœ… Ready for PM Review", "#00d4aa", time_in_rft, url, story_points
                    ))
                elif from_upper in qa_upper and is_effectively_done_status(t["to"], issue_type):
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "done",
                        "âœ… Done", "#00d4aa", time_in_rft, url, story_points
                    ))
                else:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "status_changed",
                        f"â†” {t['from']}  →  {t['to']}", "#4a90d9", time_in_rft, url, story_points
                    ))

            if not day_transitions:
                continue

            seen_transitions = set()
            transitions_for_day: list[str] = []
            for t in day_transitions:
                is_dev_action = False
                if assignee_account_id and t.get("actor_account_id"):
                    is_dev_action = t.get("actor_account_id") == assignee_account_id
                elif dev_name and dev_name != "Unassigned":
                    is_dev_action = (t.get("actor", "") or "").strip().lower() == dev_name.strip().lower()
                if not is_dev_action:
                    continue

                transition_label = f"{t['from']}  →  {t['to']}"
                if transition_label in seen_transitions:
                    continue
                seen_transitions.add(transition_label)
                transitions_for_day.append(transition_label)

            if not transitions_for_day:
                continue

            if dev_name not in dev_maps[date_key]:
                dev_maps[date_key][dev_name] = {"name": dev_name, "avatar": dev_avatar, "issues": []}

            dev_maps[date_key][dev_name]["issues"].append({
                "key": key, "summary": summary, "type": issue_type,
                "status": status_name, "story_points": story_points,
                "active_days": active_days, "is_stale": is_stale,
                "stale_threshold": stale_threshold, "is_done": is_effectively_done_status(status_name, issue_type),
                "time_in_rft": time_in_rft, "transitions_today": transitions_for_day,
                "url": url,
                "linked_story": linked_story,
                "linked_story_summary": linked_story_summary,
            })

    dev_history = {
        date_key: sorted(dev_maps[date_key].values(), key=lambda d: d["name"])
        for date_key in date_keys
    }
    qa_history = {
        date_key: qa_items_by_date[date_key]
        for date_key in date_keys
    }
    return dev_history, qa_history


def _qa_event(key, summary, issue_type, transition, event, label, color,
              time_in_rft, url, story_points) -> dict:
    return {
        "key": key, "summary": summary, "type": issue_type,
        "status": transition["to"], "from_status": transition["from"],
        "event": event, "label": label, "color": color,
        "actor": transition.get("actor", "Unknown"),
        "actor_avatar": transition.get("actor_avatar", ""),
        "time_in_rft": time_in_rft, "url": url, "story_points": story_points,
    }


#  —  —  —  REPORT BUILDER  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def _extract_linked_story_key(fields: dict) -> str:
    parent = fields.get("parent") or {}
    parent_key = parent.get("key")
    parent_type = (((parent.get("fields") or {}).get("issuetype") or {}).get("name") or "").strip().lower()
    if parent_key and parent_type == "story":
        return parent_key

    for link in (fields.get("issuelinks") or []):
        for side in ("outwardIssue", "inwardIssue"):
            issue = link.get(side) or {}
            issue_key = issue.get("key")
            issue_type = (((issue.get("fields") or {}).get("issuetype") or {}).get("name") or "").strip().lower()
            if issue_key and issue_type == "story":
                return issue_key
    return ""


def _extract_linked_story_details(fields: dict) -> tuple[str, str]:
    parent = fields.get("parent") or {}
    parent_fields = parent.get("fields") or {}
    parent_key = parent.get("key") or ""
    parent_type = ((parent_fields.get("issuetype") or {}).get("name") or "").strip().lower()
    parent_summary = (parent_fields.get("summary") or "").strip()
    if parent_key and parent_type == "story":
        return parent_key, parent_summary

    for link in (fields.get("issuelinks") or []):
        for side in ("outwardIssue", "inwardIssue"):
            issue = link.get(side) or {}
            issue_fields = issue.get("fields") or {}
            issue_key = issue.get("key") or ""
            issue_type = ((issue_fields.get("issuetype") or {}).get("name") or "").strip().lower()
            issue_summary = (issue_fields.get("summary") or "").strip()
            if issue_key and issue_type == "story":
                return issue_key, issue_summary
    return "", ""


def _extract_linked_work_category(fields: dict) -> str:
    parent = fields.get("parent") or {}
    parent_fields = parent.get("fields") or {}
    parent_type = ((parent_fields.get("issuetype") or {}).get("name") or "").strip().lower()
    if parent_type == "story":
        return "story"
    if parent_type in {"enhancement", "improvement", "task"}:
        return "enhancement_task"
    if parent_type:
        return "other"

    for link in (fields.get("issuelinks") or []):
        for side in ("outwardIssue", "inwardIssue"):
            issue = link.get(side) or {}
            issue_fields = issue.get("fields") or {}
            issue_type = ((issue_fields.get("issuetype") or {}).get("name") or "").strip().lower()
            if issue_type == "story":
                return "story"
            if issue_type in {"enhancement", "improvement", "task"}:
                return "enhancement_task"
            if issue_type:
                return "other"
    return "no_link"


def _build_bug_linkage_breakdown(issues: list[dict]) -> dict[str, int]:
    counts = {"story": 0, "enhancement_task": 0, "no_link": 0, "other": 0}
    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if issue_type not in {BUG_TYPE, "Feature Bug"}:
            continue
        category = _extract_linked_work_category(fields)
        counts[category] = counts.get(category, 0) + 1
    return counts


def _build_bug_story_insights(issues: list[dict]) -> dict:
    story_bug_count = 0
    linked_story_keys: set[str] = set()
    engineer_counts: dict[str, int] = {}

    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if issue_type not in {BUG_TYPE, "Feature Bug"}:
            continue

        linked_story_key, _ = _extract_linked_story_details(fields)
        if not linked_story_key:
            continue

        story_bug_count += 1
        linked_story_keys.add(linked_story_key)

        assignee = fields.get("assignee") or {}
        assignee_name = (assignee.get("displayName") or "Unassigned").strip() or "Unassigned"
        engineer_counts[assignee_name] = engineer_counts.get(assignee_name, 0) + 1

    unique_story_count = len(linked_story_keys)
    avg_bugs_per_story = round(story_bug_count / unique_story_count, 1) if unique_story_count > 0 else 0.0
    top_engineer_name = ""
    top_engineer_bug_count = 0
    if engineer_counts:
        top_engineer_name, top_engineer_bug_count = sorted(
            engineer_counts.items(),
            key=lambda item: (-item[1], item[0].lower()),
        )[0]

    return {
        "story_bug_count": story_bug_count,
        "unique_story_count": unique_story_count,
        "avg_bugs_per_story": avg_bugs_per_story,
        "top_engineer_name": top_engineer_name,
        "top_engineer_bug_count": top_engineer_bug_count,
    }


def _count_story_linked_bugs(issues: list[dict]) -> int:
    count = 0
    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if issue_type not in {BUG_TYPE, "Feature Bug"}:
            continue
        linked_story_key, _ = _extract_linked_story_details(fields)
        if linked_story_key:
            count += 1
    return count


def _signal_benchmark_summaries() -> dict:
    commitment = METRICS_CONFIG["commitment"]
    carryover = METRICS_CONFIG["carryover"]
    cycle_time = METRICS_CONFIG["cycle_time"]
    bug_ratio = METRICS_CONFIG["bug_ratio"]
    return {
        "commitment": (
            f"Target: {int(commitment['ideal_min_pct'])}-{int(commitment['ideal_max_pct'])}% "
            f"| Good: {int(commitment['good_min_pct'])}%+ | Risk: <{int(commitment['warning_min_pct'])}%"
        ),
        "carryover": (
            f"Best: <{int(carryover['excellent_lt_pct'])}% "
            f"| Good: up to {int(carryover['good_lte_pct'])}% "
            f"| Risk: >{int(carryover['warning_lte_pct'])}%"
        ),
        "cycle_time": (
            f"Best: within +/-{int(cycle_time['stable_abs_pct'])}% "
            f"| Good: up to +{int(cycle_time['good_increase_pct'])}% "
            f"| Risk: >+{int(cycle_time['warning_increase_pct'])}%"
        ),
        "bug_ratio": (
            f"Best: <{int(bug_ratio['excellent_lt_pct'])}% "
            f"| Good: up to {int(bug_ratio['good_lte_pct'])}% "
            f"| Risk: >{int(bug_ratio['warning_lte_pct'])}%"
        ),
    }


def _sprint_placement_label(fields: dict) -> str:
    sprints = fields.get("customfield_10020") or []
    if not sprints:
        return "Backlog"
    active = [
        s.get("name", "").strip()
        for s in sprints
        if (s.get("state", "") or "").lower() == "active" and s.get("name")
    ]
    if active:
        return active[0]
    named = [s.get("name", "").strip() for s in sprints if s.get("name")]
    return named[-1] if named else "Backlog"


def _build_remaining_scope_breakdown(issues: list) -> list[dict]:
    by_type: dict[str, dict] = {}
    for issue in issues:
        fields = issue.get("fields", {})
        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        issue_type = ((fields.get("issuetype") or {}).get("name") or "Unknown").strip() or "Unknown"
        if is_effectively_done_status(status_name, issue_type):
            continue
        row = by_type.setdefault(issue_type, {"type": issue_type, "count": 0, "scope": 0.0})
        row["count"] += 1
        row["scope"] = round(row["scope"] + get_issue_weight(issue), 1)
    return sorted(by_type.values(), key=lambda item: (-item["scope"], -item["count"], item["type"]))


def _build_scope_breakdown(issues: list, remaining_only: bool = False) -> list[dict]:
    by_type: dict[str, dict] = {}
    for issue in issues:
        fields = issue.get("fields", {})
        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        issue_type = ((fields.get("issuetype") or {}).get("name") or "Unknown").strip() or "Unknown"
        if remaining_only and is_effectively_done_status(status_name, issue_type):
            continue
        row = by_type.setdefault(issue_type, {"type": issue_type, "count": 0, "scope": 0.0})
        row["count"] += 1
        row["scope"] = round(row["scope"] + get_issue_weight(issue), 1)
    return sorted(by_type.values(), key=lambda item: (-item["scope"], -item["count"], item["type"]))


def calculate_bug_ratio_base_work(issues: list, weighting: str = "hybrid_scope") -> dict:
    included_types = {STORY_TYPE}
    base_work = 0.0
    base_items = 0
    by_type: dict[str, dict] = {}

    for issue in issues:
        issue_type = ((((issue.get("fields") or {}).get("issuetype")) or {}).get("name") or "").strip()
        if issue_type not in included_types:
            continue
        weight = get_work_weight(issue, weighting=weighting)
        base_work += weight
        base_items += 1
        row = by_type.setdefault(issue_type, {"type": issue_type, "count": 0, "scope": 0.0})
        row["count"] += 1
        row["scope"] = round(row["scope"] + weight, 1)

    return {
        "base_work": round(base_work, 1),
        "base_items": base_items,
        "included_types": sorted(included_types),
        "breakdown": sorted(by_type.values(), key=lambda item: (-item["scope"], -item["count"], item["type"])),
    }


def _is_new_sprint_bug(issue: dict, sprint_start_dt: datetime | None) -> bool:
    fields = issue.get("fields", {})
    issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
    if issue_type not in {BUG_TYPE, "Feature Bug"}:
        return False
    created_dt = parse_jira_datetime(fields.get("created"))
    if not sprint_start_dt or not created_dt:
        return False
    return created_dt.date() >= sprint_start_dt.date()


def _build_planned_scope_metrics(issues: list, sprint_start_dt: datetime | None) -> dict:
    planned_scope = 0.0
    completed_scope = 0.0
    remaining_scope = 0.0
    for issue in issues:
        if _is_new_sprint_bug(issue, sprint_start_dt):
            continue
        weight = get_issue_weight(issue)
        planned_scope += weight
        status_name = (((issue.get("fields") or {}).get("status") or {}).get("name") or "").strip()
        if is_effectively_done_status(status_name, ((issue.get("fields") or {}).get("issuetype") or {}).get("name", "")):
            completed_scope += weight
        else:
            remaining_scope += weight
    return {
        "planned_scope": round(planned_scope, 1),
        "completed_scope": round(completed_scope, 1),
        "remaining_scope": round(remaining_scope, 1),
    }


def build_today_bug_reports(target_dates: list | None = None) -> dict[str, list[dict]]:
    target_dates = target_dates or _recent_activity_dates(7)
    date_keys = [_activity_date_key(target_date) for target_date in target_dates]
    bug_issues = fetch_recent_created_bugs(days=len(target_dates))
    rows_by_date: dict[str, list[dict]] = {date_key: [] for date_key in date_keys}
    for issue in bug_issues:
        f = issue.get("fields", {})
        key = issue.get("key", "")
        creator_user = f.get("creator") or {}
        reporter_user = f.get("reporter") or {}
        creator = (
            creator_user.get("displayName")
            or reporter_user.get("displayName")
            or "Unknown"
        )
        creator_avatar = (
            (creator_user.get("avatarUrls") or {}).get("48x48")
            or (reporter_user.get("avatarUrls") or {}).get("48x48")
            or ""
        )
        linked_story = _extract_linked_story_key(f)
        created_dt = parse_jira_datetime(f.get("created"))
        if not created_dt:
            continue
        date_key = _activity_date_key(created_dt.astimezone(LOCAL_TZ).date())
        if date_key not in rows_by_date:
            continue
        rows_by_date[date_key].append({
            "key": key,
            "summary": f.get("summary", ""),
            "status": ((f.get("status") or {}).get("name") or ""),
            "type": ((f.get("issuetype") or {}).get("name") or ""),
            "created_by": creator,
            "created_by_avatar": creator_avatar,
            "linked_story": linked_story,
            "is_linked_to_story": bool(linked_story),
            "sprint_placement": _sprint_placement_label(f),
            "url": f"{JIRA_BASE_URL}/browse/{key}",
        })
    return rows_by_date


def build_report(issues: list, sprint_info: dict, prev_sprints: list) -> dict:
    ss    = SprintState(sprint_info)
    sp    = ss.sprint_progress_pct
    is_ex = ss.state == "extended"

    sprint_start_dt = _parse_date_str(ss.start_str)
    story_issues = [issue for issue in issues if _is_story_issue(issue)]
    total        = len(issues)
    done         = sum(1 for i in issues if is_effectively_done_status(i["fields"]["status"]["name"], i["fields"]["issuetype"]["name"]))
    carried_over = total - done

    # Bug separation
    new_bugs = carried_bugs = new_bugs_done = 0
    new_bug_items: list[dict] = []
    carried_bug_items: list[dict] = []
    for i in issues:
        f = i["fields"]
        if f["issuetype"]["name"] not in {BUG_TYPE, "Feature Bug"}:
            continue
        created_dt = parse_jira_datetime(f.get("created"))
        if sprint_start_dt and created_dt and created_dt.date() >= sprint_start_dt.date():
            new_bugs += 1
            new_bug_items.append(i)
            if is_effectively_done_status(f["status"]["name"], f["issuetype"]["name"]): new_bugs_done += 1
        else:
            carried_bugs += 1
            carried_bug_items.append(i)

    bugs         = new_bugs + carried_bugs
    new_story_linked_bugs = _count_story_linked_bugs(new_bug_items)
    stories_done = sum(1 for i in issues
                       if i["fields"]["issuetype"]["name"] == STORY_TYPE
                       and is_effectively_done_status(i["fields"]["status"]["name"], i["fields"]["issuetype"]["name"]))

    status_counts = {}; issue_type_counts = {}; assignee_counts = {}
    unfinished_status_counts = {}
    blockers = flagged = 0
    age_buckets = {"0-3d": 0, "4-7d": 0, "8-14d": 0, "15+d": 0}
    age_values  = []

    for i in issues:
        f = i["fields"]
        s = f["status"]["name"]
        t = f["issuetype"]["name"]
        status_counts[s] = status_counts.get(s, 0) + 1
        if not is_effectively_done_status(s, t):
            unfinished_status_counts[s] = unfinished_status_counts.get(s, 0) + 1
        issue_type_counts[t] = issue_type_counts.get(t, 0) + 1
        assignee      = f.get("assignee")
        assignee_name = assignee.get("displayName") if assignee else "Unassigned"
        assignee_counts[assignee_name] = assignee_counts.get(assignee_name, 0) + 1
        labels   = [l.lower() for l in (f.get("labels") or [])]
        if "blocked" in labels or "blocker" in labels or "block" in s.lower(): blockers += 1
        if "flagged" in labels or bool(f.get("customfield_10021")): flagged += 1
        if not is_effectively_done_status(s, t):
            age = issue_age_days(f.get("created"))
            if age is not None:
                age_values.append(age)
                if age <= 3: age_buckets["0-3d"] += 1
                elif age <= 7: age_buckets["4-7d"] += 1
                elif age <= 14: age_buckets["8-14d"] += 1
                else: age_buckets["15+d"] += 1

    cycle_times = [
        ct for i in story_issues
        if is_effectively_done_status(i["fields"]["status"]["name"], i["fields"]["issuetype"]["name"])
        for ct in [calc_cycle_time_days(i["fields"].get("created"), i["fields"].get("resolutiondate"))]
        if ct is not None
    ]
    current_avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else None
    prev_avg_ct    = None
    if prev_sprints:
        valid = [s["avg_cycle_time"] for s in prev_sprints if s["avg_cycle_time"] is not None]
        if valid: prev_avg_ct = sum(valid) / len(valid)
    prev_bugs = next((s["bugs"] for s in prev_sprints if s.get("bugs") is not None), None)

    bd          = build_burndown(issues, ss)
    total_scope = bd.get("total_scope", float(len(story_issues))) if bd else float(len(story_issues))
    scope_cfg = _config_scope_calculation()
    carryover_metrics = calculate_carryover_metrics(
        story_issues,
        sprint_start_dt=sprint_start_dt,
        include_mid_sprint_added=bool(scope_cfg.get("include_mid_sprint_added", False)),
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
    )
    carried_in_metrics = calculate_carried_in_work_metrics(
        issues,
        current_sprint=sprint_info,
        current_sprint_start_dt=sprint_start_dt,
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
        include_item_list=False,
    )
    sprint_carryover_metrics = calculate_sprint_carryover_metrics(
        story_issues,
        current_sprint=sprint_info,
        current_sprint_start_dt=sprint_start_dt,
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
        include_item_list=False,
    )
    committed_scope = total_scope
    completed_scope = sum(
        get_work_weight(issue, weighting=str(scope_cfg.get("weighting", "hybrid_scope")))
        for issue in story_issues
        if is_effectively_done_status(
            ((issue.get("fields", {}).get("status") or {}).get("name") or "").strip(),
            ((issue.get("fields", {}).get("issuetype") or {}).get("name") or "").strip(),
        )
    )
    official_rollover_scope = sprint_carryover_metrics["official_rollover_work"]
    bug_ratio_base = calculate_bug_ratio_base_work(
        story_issues,
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
    )
    bug_ratio_base_work = bug_ratio_base["base_work"]

    c_score,  c_pct  = score_commitment(completed_scope, committed_scope, sp, is_ex)
    co_score, co_pct = score_carryover(official_rollover_scope, total_scope, sp, is_ex)
    cy_score, cy_pct = score_cycle_time(current_avg_ct, prev_avg_ct, sp)
    b_score,  b_pct  = score_bug_ratio(new_story_linked_bugs, bug_ratio_base_work, sp)

    if bd:
        bd["total_breakdown"] = _build_scope_breakdown(issues, remaining_only=False)
        bd["remaining_breakdown"] = _build_scope_breakdown(issues, remaining_only=True)
    bd_nudge    = score_burndown(bd, sp)
    health_calc = calc_health_score(c_score, co_score, cy_score, b_score, bd_nudge)
    health      = health_calc["score"]
    emoji, label = health_label(health)

    bug_change_pct, bug_change_arrow = None, " → "
    if prev_bugs is not None and prev_bugs > 0:
        bug_change_pct   = round((bugs - prev_bugs) / prev_bugs * 100, 1)
        bug_change_arrow = "â†“" if bug_change_pct < 0 else ("â†‘" if bug_change_pct > 0 else " → ")

    no_data_signals = []
    if len(story_issues) == 0: no_data_signals.extend(["commitment", "carryover", "bug_ratio"])
    if current_avg_ct is None or prev_avg_ct is None: no_data_signals.append("cycle_time")

    weights = _config_weights()
    fb      = dict(health_calc["weighted_breakdown"])

    activity_cfg = _config_activity_people()
    qa_name_filter = {
        _normalize_person_name(name)
        for name in (activity_cfg.get("qa_names") or [])
        if _normalize_person_name(str(name))
    }
    dev_name_filter = {
        _normalize_person_name(name)
        for name in (activity_cfg.get("developer_names") or [])
        if _normalize_person_name(str(name))
    }

    activity_dates = _sprint_activity_dates(ss.start_str)
    today_activity_key = _activity_date_key(datetime.now(LOCAL_TZ).date())
    activity_date_options = [
        {
            "key": _activity_date_key(target_date),
            "label": _activity_date_label(target_date),
            "is_default": _activity_date_key(target_date) == today_activity_key,
        }
        for target_date in activity_dates
    ]
    activity_issues = fetch_recent_project_issues(days=len(activity_dates))
    dev_activity, qa_activity = build_developer_activity(
        activity_issues,
        ss.start_str,
        target_dates=activity_dates,
        allowed_qa_names=qa_name_filter,
        allowed_dev_names=dev_name_filter,
    )
    today_bug_reports = build_today_bug_reports(activity_dates)

    cycle_time_medians = calc_cycle_time_median_per_type(issues)
    bottlenecks = calc_status_bottlenecks(issues)

    ai_insights = generate_ai_insights({
        "sprint_name": ss.name, "health_score": health, "health_label": label,
        "signals": {
            "commitment": {"score": c_score, "pct": c_pct},
            "carryover":  {"score": co_score, "pct": co_pct},
            "cycle_time": {"score": cy_score, "pct": cy_pct},
            "bug_ratio":  {"score": b_score,  "pct": b_pct},
        },
        "burndown": bd, "blocked_count": blockers, "new_bugs": new_bugs,
    })

    # Determine health color for UI
    h_color = "green" if health >= 85 else "yellow" if health >= 70 else "orange" if health >= 50 else "red"
    
    # Determine status note
    note = ""
    if ss.state == "closed":
        note = "Showing Last Closed Sprint"
    elif ss.state == "active" and datetime.now(LOCAL_TZ) > ss.end_dt:
        note = f"Sprint Overdue (Ended {ss.end_str})"
    elif ss.state == "active":
        note = "Sprint in Progress"
    
    gen_at = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")

    return {
        "sprint_name": ss.name, "sprint_start": ss.start_str, "sprint_end": ss.end_str,
        "sprint_state": ss.state, "sprint_progress_pct": sp,
        "sprint_status_note": note,
        "elapsed_days": ss.elapsed_days, "total_days": ss.total_days,
        "generated_at": gen_at,
        "health_score": health, "health_emoji": emoji, "health_label": label,
        "health_color": h_color,
        "bd_nudge": bd_nudge,
        "total": total, "done": done, "carried_over": carried_over,
        "bugs": bugs, "new_bugs": new_bugs, "new_bugs_done": new_bugs_done,
        "new_story_linked_bugs": new_story_linked_bugs,
        "carried_bugs": carried_bugs, "stories_done": stories_done,
        "new_bug_linkage": _build_bug_linkage_breakdown(new_bug_items),
        "carried_bug_linkage": _build_bug_linkage_breakdown(carried_bug_items),
        "bug_story_insights": _build_bug_story_insights(new_bug_items + carried_bug_items),
        "blocked_count": blockers, "flagged_count": flagged,
        "status_counts": status_counts,
        "unfinished_status_counts": unfinished_status_counts,
        "issue_type_counts": dict(sorted(issue_type_counts.items(), key=lambda x: -x[1])),
        "assignee_counts":   dict(sorted(assignee_counts.items(),   key=lambda x: -x[1])),
        "age_buckets": age_buckets,
        "avg_unfinished_age_days": round(sum(age_values)/len(age_values), 1) if age_values else None,
        "no_data_signals": no_data_signals,
        "signals": {
            "commitment": {
                "score": c_score, "pct": c_pct,
                "color": "green" if c_score >= 85 else "yellow" if c_score >= 70 else "orange" if c_score >= 50 else "red",
                "raw": f"{_format_decimal(completed_scope)}/{_format_decimal(committed_scope)} story scope done",
                "no_data": committed_scope == 0,
            },
            "carryover":  {
                "score": co_score, "pct": co_pct,
                "color": "green" if co_score >= 85 else "yellow" if co_score >= 70 else "orange" if co_score >= 50 else "red",
                "raw": f"{_format_decimal(official_rollover_scope)}/{_format_decimal(total_scope)} story scope rolled from previous sprint",
                "no_data": total_scope == 0,
            },
            "cycle_time": {
                "score": cy_score, "pct": cy_pct,
                "color": "green" if cy_score >= 85 else "yellow" if cy_score >= 70 else "orange" if cy_score >= 50 else "red",
                "raw": f"avg {round(current_avg_ct,1) if current_avg_ct else 'N/A'} days" +
                       (f" (prev 3 sprints: {round(prev_avg_ct,1)})" if prev_avg_ct else ""),
                "no_data": current_avg_ct is None or prev_avg_ct is None,
            },
            "bug_ratio": {
                "score": b_score, "pct": b_pct,
                "color": "green" if b_score >= 85 else "yellow" if b_score >= 70 else "orange" if b_score >= 50 else "red",
                "raw": f"{new_story_linked_bugs} story-linked new bugs / {_format_decimal(bug_ratio_base_work)} story scope",
                "no_data": bug_ratio_base_work == 0 and new_story_linked_bugs == 0,
            },
        },
        "formula_breakdown": fb, "weights": dict(weights),
        "formula_expression": health_calc["formula"],
        "formula_context": health_calc["context"],
        "signal_thresholds": _signal_benchmark_summaries(),
        "ai_insights": ai_insights,
        "burndown": bd,
        "carryover_metrics": carryover_metrics,
        "carried_in_metrics": carried_in_metrics,
        "sprint_carryover_metrics": sprint_carryover_metrics,
        "bug_ratio_base": bug_ratio_base,
        "dev_activity": dev_activity,
        "qa_activity":  qa_activity,
        "today_bug_reports": today_bug_reports,
        "cycle_time_medians": cycle_time_medians,
        "bottlenecks": bottlenecks,
        "activity_date_options": activity_date_options,
        "bug_change_pct": bug_change_pct, "bug_change_arrow": bug_change_arrow,
        "current_avg_cycle_time": round(current_avg_ct, 1) if current_avg_ct is not None else None,
        "execution": {"completed": done, "unfinished": carried_over,
                      "completion_pct": c_pct, "carryover_pct": co_pct},
        "formula": (
            f"({c_score} * 0.35) + ({co_score} * 0.25) + ({cy_score} * 0.20) + ({b_score} * 0.20)"
            + (f" + burndown nudge ({bd_nudge:+d})" if bd_nudge else "")
            + f" = *{health}*"
        ),
        "ai_insights_html": ai_insights,
    }

# --- UI and Slack Logic Moved to dashboard_ui.py ---


# --- UI and Slack Logic Moved to dashboard_ui.py ---

#  —  —  —  PDF REPORT  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def write_pdf_report(r: dict, output_path: str | None = None) -> str | None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as pdf_canvas
    except Exception as e:
        print(f"[warn] PDF skipped: {e}")
        return None
    if not output_path:
        output_path = f"sprint-health-{datetime.now().strftime('%Y-%m-%d')}.pdf"
    out  = Path(output_path)
    c    = pdf_canvas.Canvas(str(out), pagesize=A4)
    W, H = A4
    y    = H - 50
    fb   = r["formula_breakdown"]
    bd   = r.get("burndown", {})
    lines = [
        "Lumofy  —  Sprint Health Report",
        f"Sprint: {r['sprint_name']}",
        f"Dates:  {r['sprint_start']}  →  {r['sprint_end']}",
        f"State:  {r['sprint_state'].upper()}",
        "",
        f"Health Score: {r['health_score']}/100   —   {r['health_label']}",
        "",
        "Signals:",
        f"  Commitment:  {r['signals']['commitment']['raw']}   →  {r['signals']['commitment']['score']} pts",
        f"  Carryover:   {r['signals']['carryover']['raw']}    →  {r['signals']['carryover']['score']} pts",
        f"  Cycle Time:  {r['signals']['cycle_time']['raw']}   →  {r['signals']['cycle_time']['score']} pts",
        f"  Bug Ratio:   {r['signals']['bug_ratio']['raw']}    →  {r['signals']['bug_ratio']['score']} pts",
        "",
        "Bug Breakdown:",
        f"  New Bugs:     {r['new_bugs']}  ({r['new_bugs_done']} resolved)",
        f"  Carried Bugs: {r['carried_bugs']}  (display only)",
        "",
    ]
    if bd:
        lines += [
            "Burndown:",
            f"  Day {bd['elapsed_days']}/{bd['total_days']}  |  {_format_decimal(float(bd['current_remaining']), 0)} scope remaining  |  Ideal: {_format_decimal(float(bd['ideal_remaining']), 0)}",
            f"  Velocity: {bd['velocity']}/day  |  Projected: {bd['projected_end']}",
            f"  Status: {'On track' if bd.get('on_track') else 'Behind'}",
            "",
        ]
    lines += [
        f"Formula: {fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}"
        + (f" + ({r['bd_nudge']:+d})" if r.get('bd_nudge') else "")
        + f" = {r['health_score']}",
        "",
        "Issue Status:",
    ]
    for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"  {k}: {v}")
    lines += ["", f"Bugs: {r['bugs']}  |  Scope: {r['total']}  |  Generated: {r['generated_at']}"]
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, lines[0])
    y -= 28
    c.setFont("Helvetica", 11)
    for line in lines[1:]:
        if y < 50:
            c.showPage(); c.setFont("Helvetica", 11); y = H - 50
        c.drawString(50, y, line)
        y -= 15
    c.save()
    print(f"[ok] PDF report: {out.resolve()}")
    return str(out.resolve())


#  —  —  —  SLACK SEND  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def send_to_slack(message: str) -> None:
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json"},
        json={"channel": SLACK_CHANNEL, "text": message, "mrkdwn": True},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Slack error: {result.get('error')}")
    print(f"[ok] Slack ts={result.get('ts')}")


#  —  —  —  MAIN RUN  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  —  — 

def run(dry_run=False, export_html=False, export_pdf=False, no_slack=False,
        site_url="", pdf_url="", slack_link_only=False) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running Sprint Health Score...")
    missing = [k for k, v in {
        "JIRA_EMAIL": JIRA_EMAIL, "JIRA_API_TOKEN": JIRA_API_TOKEN,
        "SLACK_BOT_TOKEN": SLACK_TOKEN, "SLACK_CHANNEL_ID": SLACK_CHANNEL,
    }.items() if not v]
    if missing and not dry_run:
        raise EnvironmentError(f"Missing env vars: {', '.join(missing)}")

    print("[1/3] Fetching sprint issues from Jira...")
    issues, sprint_info = fetch_sprint_issues()
    ss = SprintState(sprint_info)
    print(f"       →  {len(issues)} issues in '{ss.name}' [{ss.state}]")

    print("[2/3] Fetching last 3 closed sprints...")
    prev_sprints = fetch_last_n_sprints(n=3)
    print(f"       →  {len(prev_sprints)} closed sprints found")

    print("[3/3] Building report (fetching changelogs)...")
    report = build_report(issues, sprint_info, prev_sprints)

    effective_site_url = (site_url or REPORT_SITE_URL).strip()
    effective_pdf_url  = (pdf_url  or REPORT_PDF_URL).strip()

    if slack_link_only and effective_site_url:
        message = format_slack_site_message(report, effective_site_url, effective_pdf_url)
    else:
        message = format_slack_message(report)
        if effective_site_url:
            message += f"\n\n:link: Hosted report: {effective_site_url}"
            if effective_pdf_url:
                message += f"\n:file_folder: PDF: {effective_pdf_url}"

    if export_html: write_html_report(report)
    if export_pdf:  write_pdf_report(report)
    _save_issue_cache()

    print("\n" + "=" * 60)
    print(message)
    print("=" * 60 + "\n")

    if dry_run or no_slack:
        print("[dry-run] Skipping Slack send.")
    else:
        print("Sending to Slack...")
        send_to_slack(message)

    print(f"[done] {report['health_score']}/100  —  {report['health_label']}")


def run_scheduled(hour=9, minute=0) -> None:
    import pytz
    cairo = pytz.timezone("Africa/Cairo")
    def job():
        local_now = datetime.now(cairo)
        if local_now.hour != hour or local_now.minute != minute: return
        try: run()
        except Exception as e: print(f"[error] {e}")
    time_str = f"{hour:02d}:{minute:02d}"
    schedule.every().day.at(time_str).do(job)
    print(f"[scheduler] Daily at {time_str} Cairo time.")
    while True:
        schedule.run_pending()
        time.sleep(30)


def run_every_hours(
    hours: int = 1,
    html_output_path: str = "sprint_health_report.html",
    export_pdf: bool = False,
) -> None:
    hours = max(1, int(hours))
    ensure_admin_dashboard_running()

    def job():
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            print(f"[hourly:{started_at}] Running scheduled dashboard refresh...")
            run(dry_run=True, export_html=True, export_pdf=export_pdf, no_slack=True)
            print(f"[hourly:{started_at}] Dashboard refresh completed.")
        except Exception as e:
            print(f"[hourly:{started_at}] Error while refreshing dashboard: {e}")

    if html_output_path != "sprint_health_report.html":
        print(
            f"[hourly] Custom html path '{html_output_path}' requested, "
            "but hourly mode currently writes the standard report file."
        )

    job()
    schedule.every(hours).hours.do(job)
    print(f"[scheduler] Dashboard will auto-refresh every {hours} hour(s).")
    print("[scheduler] Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)


def _issues_fingerprint(issues: list, sprint_info: dict) -> str:
    """
    Stable snapshot key to detect Jira changes without rebuilding full report every loop.
    """
    rows = []
    for issue in issues:
        fields = issue.get("fields", {})
        rows.append(
            (
                issue.get("key", ""),
                fields.get("updated", ""),
                (fields.get("status") or {}).get("name", ""),
                (fields.get("assignee") or {}).get("accountId", ""),
            )
        )
    rows.sort()
    payload = {
        "sprint_id": sprint_info.get("id"),
        "sprint_state": sprint_info.get("state"),
        "issue_count": len(issues),
        "rows": rows,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run_watch(interval_seconds: int = 30, html_output_path: str = "sprint_health_report.html") -> None:
    """
    Poll Jira and refresh the HTML dashboard whenever sprint issues change.
    """
    interval_seconds = max(10, int(interval_seconds))
    ensure_admin_dashboard_running()
    print(f"[watch] Live mode enabled. Poll interval: {interval_seconds}s")
    print("[watch] Press Ctrl+C to stop.")

    last_fp = None

    while True:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        global FORCE_REFRESH_REQUESTED
        
        try:
            issues, sprint_info = fetch_sprint_issues()
            current_fp = _issues_fingerprint(issues, sprint_info)

            # Refresh if issues changed OR if a manual refresh was requested
            if current_fp != last_fp or FORCE_REFRESH_REQUESTED:
                if FORCE_REFRESH_REQUESTED:
                    print(f"[watch:{started_at}] Manual refresh triggered via Admin UI...")
                    FORCE_REFRESH_REQUESTED = False # Reset trigger

                prev_sprints = fetch_last_n_sprints(n=3)
                report = build_report(issues, sprint_info, prev_sprints)
                write_html_report(report, output_path=html_output_path)
                _save_issue_cache()
                ss = SprintState(sprint_info)
                print(
                    f"[watch:{started_at}] Updated HTML from Jira change "
                    f"({len(issues)} issues, sprint='{ss.name}', score={report['health_score']})."
                )
                last_fp = current_fp
            else:
                print(f"[watch:{started_at}] No Jira changes detected.")
        except Exception as e:
            print(f"[watch:{started_at}] Error while refreshing dashboard: {e}")

        # Wait for the next poll, but check frequently for the FORCE_REFRESH_REQUESTED flag
        for _ in range(interval_seconds):
            if FORCE_REFRESH_REQUESTED:
                break
            time.sleep(1)


def _is_admin_dashboard_running(host: str, port: int, timeout_seconds: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def ensure_admin_dashboard_running() -> bool:
    admin_host = os.getenv("ADMIN_DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    admin_port = int(os.getenv("ADMIN_DASHBOARD_PORT", "8765"))

    if _is_admin_dashboard_running(admin_host, admin_port):
        print(f"[admin] Dashboard already running at http://{admin_host}:{admin_port}")
        return False

    cmd = [sys.executable, str(Path(__file__).resolve()), "--admin-dashboard"]
    kwargs = {
        "cwd": str(Path(__file__).resolve().parent),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        print(f"[warn] Could not auto-start admin dashboard: {e}")
        return False

    for _ in range(20):
        time.sleep(0.25)
        if _is_admin_dashboard_running(admin_host, admin_port):
            print(f"[admin] Auto-started admin dashboard at http://{admin_host}:{admin_port}")
            return True

    print(f"[warn] Admin dashboard did not start on http://{admin_host}:{admin_port} yet.")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sprint Health Score Reporter")
    parser.add_argument("--admin-dashboard", action="store_true")
    parser.add_argument("--dry-run",         action="store_true")
    parser.add_argument("--schedule",        action="store_true")
    parser.add_argument("--hourly",          action="store_true")
    parser.add_argument("--hourly-every",    type=int, default=1)
    parser.add_argument("--schedule-hour",   type=int, default=9)
    parser.add_argument("--schedule-minute", type=int, default=0)
    parser.add_argument("--html",            action="store_true")
    parser.add_argument("--pdf",             action="store_true")
    parser.add_argument("--no-slack",        action="store_true")
    parser.add_argument("--site-url",        type=str, default="")
    parser.add_argument("--pdf-url",         type=str, default="")
    parser.add_argument("--slack-link-only", action="store_true")
    parser.add_argument("--watch",           action="store_true")
    parser.add_argument("--watch-interval",  type=int, default=30)
    parser.add_argument("--watch-html-path", type=str, default="sprint_health_report.html")
    args = parser.parse_args()

    if args.admin_dashboard:
        from admin_dashboard import run_dashboard
        run_dashboard()
    elif args.watch:
        run_watch(interval_seconds=args.watch_interval, html_output_path=args.watch_html_path)
    elif args.hourly:
        run_every_hours(
            hours=args.hourly_every,
            html_output_path=args.watch_html_path,
            export_pdf=args.pdf,
        )
    elif args.schedule:
        run_scheduled(hour=args.schedule_hour, minute=args.schedule_minute)
    else:
        if args.html:
            ensure_admin_dashboard_running()
        run(
            dry_run=args.dry_run, export_html=args.html, export_pdf=args.pdf,
            no_slack=args.no_slack, site_url=args.site_url,
            pdf_url=args.pdf_url, slack_link_only=args.slack_link_only,
        )
