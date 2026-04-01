import os
import sys
import argparse
import json
from html import escape
from pathlib import Path
import requests
import schedule
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── CONFIG ────────────────────────────────────────────────────────────────────

JIRA_BASE_URL   = os.getenv("JIRA_BASE_URL", "https://lumofyinc.atlassian.net")
JIRA_EMAIL      = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN  = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT    = os.getenv("JIRA_PROJECT_KEY", "PM")
JIRA_BOARD_ID   = int(os.getenv("JIRA_BOARD_ID")) if os.getenv("JIRA_BOARD_ID") else None
SLACK_TOKEN     = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL   = os.getenv("SLACK_CHANNEL_ID")
REPORT_SITE_URL = os.getenv("REPORT_SITE_URL", "").strip()
REPORT_PDF_URL  = os.getenv("REPORT_PDF_URL", "").strip()
METRICS_CONFIG_PATH = Path(
    os.getenv("METRICS_CONFIG_PATH", str(Path(__file__).resolve().with_name("health_metrics_config.json")))
)

# ── Case-insensitive done check ────────────────────────────────────────────────
DONE_STATUSES_RAW = {"Done", "Closed", "Resolved", "DONE"}

def is_done(status_name: str) -> bool:
    return status_name.strip().upper() in {s.upper() for s in DONE_STATUSES_RAW}

BUG_TYPE      = "Bug"
STORY_TYPE    = "Story"
ENHANCEMENT_TYPES = {"Enhancement", "Improvement", "Task"}

# ── Scoring weights ────────────────────────────────────────────────────────────
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
        loaded = json.loads(METRICS_CONFIG_PATH.read_text(encoding="utf-8"))
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

# ── Stale thresholds (days without status change) ─────────────────────────────
def get_stale_threshold(issue_type: str, story_points: float | None) -> int:
    thresholds = METRICS_CONFIG["stale_thresholds"]
    t = (issue_type or "").strip()
    if t == BUG_TYPE or t == "Sub-task":
        key = "bug_days" if t == BUG_TYPE else "subtask_days"
        return int(thresholds[key])
    if t in ENHANCEMENT_TYPES or t == STORY_TYPE:
        if story_points is None:
            return int(thresholds["story_no_points_days"])
        if story_points <= thresholds["story_small_max_points"]:
            return int(thresholds["story_small_days"])
        if story_points <= thresholds["story_medium_max_points"]:
            return int(thresholds["story_medium_days"])
        return int(thresholds["story_large_days"])
    return int(thresholds["default_days"])


# ─── JIRA CLIENT ──────────────────────────────────────────────────────────────

def jira_get(path: str, params: dict = None) -> dict:
    url  = f"{JIRA_BASE_URL}/rest/api/3/{path}"
    resp = requests.get(
        url, params=params,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if resp.status_code == 410 and path == "search":
        url  = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        resp = requests.get(
            url, params=params,
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"},
            timeout=15,
        )
    resp.raise_for_status()
    return resp.json()


def agile_get(path: str, params: dict = None) -> dict:
    url  = f"{JIRA_BASE_URL}/rest/agile/1.0/{path}"
    resp = requests.get(
        url, params=params,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


_BOARD_ID_CACHE = None


def get_board_id():
    global _BOARD_ID_CACHE
    if _BOARD_ID_CACHE is not None:
        return _BOARD_ID_CACHE
    if JIRA_BOARD_ID:
        _BOARD_ID_CACHE = JIRA_BOARD_ID
        print(f"[board] Using JIRA_BOARD_ID from env: {_BOARD_ID_CACHE}")
        return _BOARD_ID_CACHE
    try:
        data   = agile_get("board", {"projectKeyOrId": JIRA_PROJECT, "maxResults": 50})
        boards = data.get("values", [])
        if not boards:
            print(f"[warn] No boards found for project '{JIRA_PROJECT}'")
            return None
        scrum  = [b for b in boards if b.get("type") == "scrum"]
        chosen = scrum[0] if scrum else boards[0]
        _BOARD_ID_CACHE = chosen["id"]
        print(f"[board] Auto-detected: '{chosen['name']}' (id={_BOARD_ID_CACHE})")
        return _BOARD_ID_CACHE
    except Exception as e:
        print(f"[warn] Could not auto-detect board ID: {e}")
        return None


# ─── SPRINT STATE DETECTION ───────────────────────────────────────────────────

class SprintState:
    def __init__(self, sprint: dict | None):
        self.sprint    = sprint or {}
        self.state     = self._detect()
        self.name      = self.sprint.get("name", "Unknown Sprint")
        self.start_str = _parse_sprint_date(self.sprint, "startDate", "start_date")
        self.end_str   = _parse_sprint_date(self.sprint, "endDate",   "end_date", "completeDate")

    def _detect(self) -> str:
        if not self.sprint:
            return "empty"
        raw_state = (self.sprint.get("state") or "").lower()
        if raw_state == "active":
            end_str = _parse_sprint_date(self.sprint, "endDate", "end_date")
            if end_str:
                end_dt = _parse_date_str(end_str)
                if end_dt and datetime.now(timezone.utc).date() > end_dt.date():
                    return "extended"
            return "active"
        if raw_state == "closed":
            return "closed"
        return "active"

    @property
    def is_active(self):
        return self.state in ("active", "extended")

    @property
    def elapsed_days(self) -> int | None:
        start = _parse_date_str(self.start_str)
        if not start:
            return None
        return max(0, (datetime.now(timezone.utc).date() - start.date()).days)

    @property
    def total_days(self) -> int | None:
        start = _parse_date_str(self.start_str)
        end   = _parse_date_str(self.end_str)
        if not start or not end:
            return None
        return max(1, (end.date() - start.date()).days)

    @property
    def sprint_progress_pct(self) -> float | None:
        el = self.elapsed_days
        to = self.total_days
        if el is None or to is None:
            return None
        return round(min(100.0, el / to * 100), 1)


def _parse_date_str(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def fetch_active_sprint_from_board(board_id) -> dict | None:
    try:
        data    = agile_get(f"board/{board_id}/sprint", {"state": "active"})
        sprints = data.get("values", [])
        if sprints:
            s = sprints[0]
            print(f"[sprint] Active: '{s.get('name')}' (id={s.get('id')})")
            return s

        print(f"[warn] No active sprint on board {board_id} — checking recently closed...")
        data   = agile_get(f"board/{board_id}/sprint", {"state": "closed"})
        closed = data.get("values", [])
        if closed:
            def _key(s):
                return s.get("endDate") or s.get("completeDate") or ""
            latest = sorted(closed, key=_key, reverse=True)[0]
            print(f"[sprint] Using last closed: '{latest.get('name')}'")
            return latest

        print(f"[warn] No sprints at all on board {board_id}")
    except Exception as e:
        print(f"[warn] Could not fetch sprint: {e}")
    return None


def fetch_sprint_issues() -> tuple[list, dict]:
    all_issues  = []
    sprint_info = {}

    board_id  = get_board_id()
    sprint_id = None

    if board_id:
        raw = fetch_active_sprint_from_board(board_id)
        if raw:
            sprint_id   = raw["id"]
            sprint_info = raw

    if sprint_id:
        jql = f"project = {JIRA_PROJECT} AND sprint = {sprint_id}"
    elif board_id:
        jql = f"project = {JIRA_PROJECT} AND sprint in activeSprints({board_id})"
    else:
        jql = f"project = {JIRA_PROJECT} AND sprint in activeSprints()"

    start_at = 0
    batch    = 50
    while True:
        data   = jira_get("search/jql", {
            "jql":      jql,
            "fields":   (
                "summary,status,issuetype,created,resolutiondate,"
                "customfield_10016,customfield_10020,customfield_10021,"
                "assignee,labels,updated,customfield_10014,priority"
            ),
            "maxResults": batch,
            "startAt":    start_at,
        })
        issues = data.get("issues", [])
        if not issues:
            break
        all_issues.extend(issues)
        if not sprint_info:
            sprints = issues[0]["fields"].get("customfield_10020") or []
            active  = [s for s in sprints if s.get("state", "").lower() == "active"]
            sprint_info = active[0] if active else (sprints[0] if sprints else {})
        start_at += len(issues)
        if start_at >= data.get("total", 0):
            break

    if not all_issues:
        print("[warn] No issues found in sprint.")
    return all_issues, sprint_info


def fetch_last_n_sprints(n: int = 3) -> list[dict]:
    sprints_data = []
    try:
        data = jira_get("search/jql", {
            "jql":       f"project = {JIRA_PROJECT} AND sprint in closedSprints() ORDER BY created DESC",
            "fields":    "resolutiondate,created,customfield_10020,status,issuetype",
            "maxResults": 200,
        })
        sprint_map = {}
        for issue in data.get("issues", []):
            for s in (issue["fields"].get("customfield_10020") or []):
                sid = s.get("id")
                if sid not in sprint_map:
                    sprint_map[sid] = {"info": s, "issues": []}
                sprint_map[sid]["issues"].append(issue["fields"])

        sorted_sprints = sorted(sprint_map.values(), key=lambda x: x["info"]["id"], reverse=True)[:n]
        for sp in sorted_sprints:
            cycle_times = []
            bug_count   = 0
            for f in sp["issues"]:
                ct = calc_cycle_time_days(f.get("created"), f.get("resolutiondate"))
                if ct is not None:
                    cycle_times.append(ct)
                if (f.get("issuetype") or {}).get("name") == BUG_TYPE:
                    bug_count += 1
            sprints_data.append({
                "name":          sp["info"].get("name"),
                "avg_cycle_time": sum(cycle_times) / len(cycle_times) if cycle_times else None,
                "bugs":          bug_count,
            })
    except Exception as e:
        print(f"[warn] Could not fetch closed sprints: {e}")
    return sprints_data


# ─── BURNDOWN ─────────────────────────────────────────────────────────────────

def build_burndown(issues: list, ss: SprintState) -> dict:
    if not ss.start_str or not ss.end_str:
        return {}

    start_dt = _parse_date_str(ss.start_str)
    end_dt   = _parse_date_str(ss.end_str)
    now_dt   = datetime.now(timezone.utc)

    if not start_dt or not end_dt or end_dt <= start_dt:
        return {}

    total_days   = max(1, (end_dt.date() - start_dt.date()).days)
    elapsed_days = max(0, (now_dt.date() - start_dt.date()).days)
    effective_days = elapsed_days if ss.state == "extended" else min(elapsed_days, total_days)

    total_issues = len(issues)

    completions_by_day: dict[int, int] = {}
    for issue in issues:
        f       = issue["fields"]
        res_raw = f.get("resolutiondate")
        if is_done(f["status"]["name"]) and res_raw:
            res_dt = _parse_date_str(res_raw)
            if res_dt and res_dt >= start_dt:
                day_idx = (res_dt.date() - start_dt.date()).days
                completions_by_day[day_idx] = completions_by_day.get(day_idx, 0) + 1

    actual_line: list[int] = []
    remaining = total_issues
    for d in range(effective_days + 1):
        remaining -= completions_by_day.get(d, 0)
        actual_line.append(max(0, remaining))

    ideal_line: list[float] = [
        round(total_issues * (1 - d / total_days), 1)
        for d in range(total_days + 1)
    ]

    current_remaining = actual_line[-1] if actual_line else total_issues
    ideal_at_today    = ideal_line[min(effective_days, total_days)]

    done_count = total_issues - current_remaining
    velocity   = round(done_count / effective_days, 2) if effective_days > 0 else 0.0

    if velocity > 0 and current_remaining > 0:
        days_to_finish = current_remaining / velocity
        projected_end  = (now_dt + timedelta(days=days_to_finish)).strftime("%Y-%m-%d")
    elif current_remaining == 0:
        projected_end  = "Done ✓"
    else:
        projected_end  = "N/A"

    day_labels = [
        (start_dt + timedelta(days=d)).strftime("%m/%d")
        for d in range(effective_days + 1)
    ]
    ideal_labels = [
        (start_dt + timedelta(days=d)).strftime("%m/%d")
        for d in range(total_days + 1)
    ]

    behind_by = round(current_remaining - ideal_at_today, 1)
    on_track  = current_remaining <= ideal_at_today

    return {
        "total_issues":       total_issues,
        "total_days":         total_days,
        "elapsed_days":       effective_days,
        "actual_line":        actual_line,
        "ideal_line":         ideal_line,
        "day_labels":         day_labels,
        "ideal_labels":       ideal_labels,
        "current_remaining":  current_remaining,
        "ideal_remaining":    ideal_at_today,
        "velocity":           velocity,
        "projected_end":      projected_end,
        "on_track":           on_track,
        "behind_by":          behind_by,
        "is_extended":        ss.state == "extended",
    }


# ─── CALCULATIONS ─────────────────────────────────────────────────────────────

def calc_cycle_time_days(created: str, resolved: str) -> float | None:
    if not created or not resolved:
        return None
    try:
        c = datetime.fromisoformat(created.replace("Z",  "+00:00"))
        r = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
        return max(0.0, (r - c).total_seconds() / 86400)
    except Exception:
        return None


def parse_jira_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def issue_age_days(created: str) -> float | None:
    dt = parse_jira_datetime(created)
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def days_since_updated(updated: str) -> float | None:
    dt = parse_jira_datetime(updated)
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def updated_today(updated: str) -> bool:
    dt = parse_jira_datetime(updated)
    if not dt:
        return False
    return dt.date() == datetime.now(timezone.utc).date()


# ─── SCORING ──────────────────────────────────────────────────────────────────

def _progress_weight(sprint_pct: float | None) -> float:
    if sprint_pct is None:
        return 1.0
    if sprint_pct < 30:
        return sprint_pct / 30
    if sprint_pct < 60:
        return 0.5 + (sprint_pct - 30) / 60
    return 1.0


def _blend(real_score: int, sprint_pct: float | None, neutral: int = 70) -> int:
    w = _progress_weight(sprint_pct)
    return round(neutral + w * (real_score - neutral))


def score_commitment(
    completed: int,
    committed: int,
    sprint_pct: float | None = None,
    is_extended: bool = False,
) -> tuple[int, float]:
    points = _config_points()
    cfg = METRICS_CONFIG["commitment"]
    if committed == 0:
        return points["neutral"], 0.0
    pct = completed / committed * 100
    if cfg["ideal_min_pct"] <= pct <= cfg["ideal_max_pct"]:
        raw = points["excellent"]
    elif pct >= cfg["good_min_pct"]:
        raw = points["good"]
    elif pct >= cfg["warning_min_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    if is_extended:
        score = min(score, int(cfg["extended_cap_score"]))
    return score, round(pct, 1)


def score_carryover(
    carried: int,
    total: int,
    sprint_pct: float | None = None,
    is_extended: bool = False,
) -> tuple[int, float]:
    points = _config_points()
    cfg = METRICS_CONFIG["carryover"]
    if total == 0:
        return points["neutral"], 0.0
    pct = carried / total * 100
    if pct < cfg["excellent_lt_pct"]:
        raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]:
        raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    if is_extended:
        score = max(0, score - int(cfg["extended_penalty"]))
    return score, round(pct, 1)


def score_cycle_time(
    current_avg: float | None,
    prev_avg: float | None,
    sprint_pct: float | None = None,
) -> tuple[int, float | None]:
    points = _config_points()
    cfg = METRICS_CONFIG["cycle_time"]
    if current_avg is None or prev_avg is None or prev_avg == 0:
        return points["neutral"], None
    diff_pct = (current_avg - prev_avg) / prev_avg * 100
    if abs(diff_pct) <= cfg["stable_abs_pct"]:
        raw = points["excellent"]
    elif diff_pct <= cfg["good_increase_pct"]:
        raw = points["good"]
    elif diff_pct <= cfg["warning_increase_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(diff_pct, 1)


def score_bug_ratio(
    new_bugs: int,
    total: int,
    sprint_pct: float | None = None,
) -> tuple[int, float]:
    """Bug ratio based on NEW bugs only (created during this sprint)."""
    points = _config_points()
    cfg = METRICS_CONFIG["bug_ratio"]
    if total == 0 and new_bugs == 0:
        return points["neutral"], 0.0
    denom = total if total > 0 else 1
    pct   = new_bugs / denom * 100
    if pct < cfg["excellent_lt_pct"]:
        raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]:
        raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]:
        raw = points["warning"]
    else:
        raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(pct, 1)


def score_burndown(bd: dict, sprint_pct: float | None) -> int:
    cfg = METRICS_CONFIG["burndown"]
    if not bd:
        return 0
    if bd.get("current_remaining", 0) == 0:
        return int(cfg["done_bonus"])
    if bd.get("on_track"):
        return int(cfg["on_track_bonus"])
    behind = bd.get("behind_by", 0)
    if behind <= cfg["behind_small_max"]:
        return 0
    if behind <= cfg["behind_medium_max"]:
        return int(cfg["behind_medium_penalty"])
    return int(cfg["behind_large_penalty"])


def calc_health_score(c_score, co_score, cy_score, b_score, bd_nudge: int = 0) -> int:
    weights = _config_weights()
    raw = (
        c_score  * weights["commitment"] +
        co_score * weights["carryover"] +
        cy_score * weights["cycle_time"] +
        b_score  * weights["bug_ratio"]
    )
    return max(0, min(100, round(raw) + bd_nudge))


def health_label(score: int) -> tuple[str, str]:
    labels = METRICS_CONFIG["labels"]
    if score >= labels["green_min_score"]:
        return ":green_circle:",  "Predictable sprint"
    elif score >= labels["yellow_min_score"]:
        return ":yellow_circle:", "Some instability"
    elif score >= labels["orange_min_score"]:
        return ":orange_circle:", "Execution issues"
    else:
        return ":red_circle:",    "Sprint breakdown"


def _parse_sprint_date(sprint_info: dict, *keys: str) -> str:
    for key in keys:
        val = sprint_info.get(key)
        if val:
            return str(val)[:10]
    return ""


# ─── DEVELOPER ACTIVITY ───────────────────────────────────────────────────────

def build_developer_activity(issues: list, sprint_start_str: str) -> list[dict]:
    """
    Returns a list of developers with their issues updated today,
    including stale detection and active-days tracking.
    """
    sprint_start = _parse_date_str(sprint_start_str)
    dev_map: dict[str, dict] = {}

    for issue in issues:
        f            = issue["fields"]
        updated_raw  = f.get("updated")
        assignee     = f.get("assignee")
        if not assignee:
            continue

        dev_name     = assignee.get("displayName", "Unknown")
        dev_avatar   = assignee.get("avatarUrls", {}).get("48x48", "")
        issue_type   = (f.get("issuetype") or {}).get("name", "")
        status_name  = f["status"]["name"]
        story_points = f.get("customfield_10016")  # story points field
        key          = issue.get("key", "")
        summary      = f.get("summary", "")

        if not updated_today(updated_raw):
            continue

        # Days since sprint start (how long this issue has been active)
        created_raw  = f.get("created")
        created_dt   = parse_jira_datetime(created_raw)
        active_days  = 0
        if created_dt and sprint_start:
            active_days = max(0, (datetime.now(timezone.utc).date() - max(created_dt.date(), sprint_start.date())).days)

        # Stale detection based on last update
        stale_threshold = get_stale_threshold(issue_type, story_points)
        days_stale      = days_since_updated(updated_raw)
        # If updated today, it's not stale — but check if it was stale before today
        # We use created date vs sprint start for "been sitting" signal
        is_stale = active_days > stale_threshold and not is_done(status_name)

        if dev_name not in dev_map:
            dev_map[dev_name] = {
                "name":   dev_name,
                "avatar": dev_avatar,
                "issues": [],
            }

        dev_map[dev_name]["issues"].append({
            "key":          key,
            "summary":      summary,
            "type":         issue_type,
            "status":       status_name,
            "story_points": story_points,
            "active_days":  active_days,
            "is_stale":     is_stale,
            "stale_threshold": stale_threshold,
            "is_done":      is_done(status_name),
            "url":          f"{JIRA_BASE_URL}/browse/{key}",
        })

    return sorted(dev_map.values(), key=lambda d: d["name"])


# ─── REPORT BUILDER ───────────────────────────────────────────────────────────

def build_report(issues: list, sprint_info: dict, prev_sprints: list) -> dict:
    ss    = SprintState(sprint_info)
    sp    = ss.sprint_progress_pct
    is_ex = ss.state == "extended"

    sprint_start_dt = _parse_date_str(ss.start_str)

    total        = len(issues)
    done         = sum(1 for i in issues if is_done(i["fields"]["status"]["name"]))
    carried_over = total - done

    # ── Bug separation: new (created in sprint) vs carried (from before) ──────
    new_bugs      = 0
    carried_bugs  = 0
    new_bugs_done = 0

    for i in issues:
        f = i["fields"]
        if f["issuetype"]["name"] != BUG_TYPE:
            continue
        created_dt = parse_jira_datetime(f.get("created"))
        if sprint_start_dt and created_dt and created_dt.date() >= sprint_start_dt.date():
            new_bugs += 1
            if is_done(f["status"]["name"]):
                new_bugs_done += 1
        else:
            carried_bugs += 1

    bugs         = new_bugs + carried_bugs  # total bugs (for display)
    stories_done = sum(
        1 for i in issues
        if i["fields"]["issuetype"]["name"] == STORY_TYPE
        and is_done(i["fields"]["status"]["name"])
    )

    # Status breakdowns
    status_counts            = {}
    issue_type_counts        = {}
    assignee_counts          = {}
    unfinished_status_counts = {}
    blockers = flagged = 0
    age_buckets = {"0-3d": 0, "4-7d": 0, "8-14d": 0, "15+d": 0}
    age_values  = []

    for i in issues:
        f = i["fields"]
        s = f["status"]["name"]
        status_counts[s] = status_counts.get(s, 0) + 1
        if not is_done(s):
            unfinished_status_counts[s] = unfinished_status_counts.get(s, 0) + 1

        t = f["issuetype"]["name"]
        issue_type_counts[t] = issue_type_counts.get(t, 0) + 1

        assignee      = f.get("assignee")
        assignee_name = assignee.get("displayName") if assignee else "Unassigned"
        assignee_counts[assignee_name] = assignee_counts.get(assignee_name, 0) + 1

        labels   = [l.lower() for l in (f.get("labels") or [])]
        status_l = s.lower()
        if "blocked" in labels or "blocker" in labels or "block" in status_l:
            blockers += 1
        if "flagged" in labels or bool(f.get("customfield_10021")):
            flagged += 1

        if not is_done(s):
            age = issue_age_days(f.get("created"))
            if age is not None:
                age_values.append(age)
                if age <= 3:
                    age_buckets["0-3d"] += 1
                elif age <= 7:
                    age_buckets["4-7d"] += 1
                elif age <= 14:
                    age_buckets["8-14d"] += 1
                else:
                    age_buckets["15+d"] += 1

    # Cycle time
    cycle_times = [
        ct for i in issues
        if is_done(i["fields"]["status"]["name"])
        for ct in [calc_cycle_time_days(i["fields"].get("created"), i["fields"].get("resolutiondate"))]
        if ct is not None
    ]
    current_avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else None
    prev_avg_ct    = None
    if prev_sprints:
        valid = [s["avg_cycle_time"] for s in prev_sprints if s["avg_cycle_time"] is not None]
        if valid:
            prev_avg_ct = sum(valid) / len(valid)
    prev_bugs = next((s["bugs"] for s in prev_sprints if s.get("bugs") is not None), None)

    # Scores — bug ratio uses new_bugs only
    c_score,  c_pct  = score_commitment(done,         total,     sp, is_ex)
    co_score, co_pct = score_carryover(carried_over,  total,     sp, is_ex)
    cy_score, cy_pct = score_cycle_time(current_avg_ct, prev_avg_ct, sp)
    b_score,  b_pct  = score_bug_ratio(new_bugs, total, sp)

    # Burndown
    bd       = build_burndown(issues, ss)
    bd_nudge = score_burndown(bd, sp)
    health   = calc_health_score(c_score, co_score, cy_score, b_score, bd_nudge)
    emoji, label = health_label(health)

    # Bug change vs previous sprint
    bug_change_pct   = None
    bug_change_arrow = "→"
    if prev_bugs is not None and prev_bugs > 0:
        bug_change_pct   = round((bugs - prev_bugs) / prev_bugs * 100, 1)
        bug_change_arrow = "↓" if bug_change_pct < 0 else ("↑" if bug_change_pct > 0 else "→")

    no_data_signals = []
    if total == 0:
        no_data_signals.extend(["commitment", "carryover", "bug_ratio"])
    if current_avg_ct is None or prev_avg_ct is None:
        no_data_signals.append("cycle_time")

    weights = _config_weights()
    fb = {
        "commitment": round(c_score  * weights["commitment"], 1),
        "carryover":  round(co_score * weights["carryover"],  1),
        "cycle_time": round(cy_score * weights["cycle_time"], 1),
        "bug_ratio":  round(b_score  * weights["bug_ratio"],  1),
    }

    # Developer activity
    dev_activity = build_developer_activity(issues, ss.start_str)

    return {
        # Sprint meta
        "sprint_name":   ss.name,
        "sprint_start":  ss.start_str,
        "sprint_end":    ss.end_str,
        "sprint_state":  ss.state,
        "sprint_progress_pct": sp,
        "elapsed_days":  ss.elapsed_days,
        "total_days":    ss.total_days,

        # Health
        "health_score":  health,
        "health_emoji":  emoji,
        "health_label":  label,
        "bd_nudge":      bd_nudge,

        # Counts
        "total":          total,
        "done":           done,
        "carried_over":   carried_over,
        "bugs":           bugs,
        "new_bugs":       new_bugs,
        "new_bugs_done":  new_bugs_done,
        "carried_bugs":   carried_bugs,
        "stories_done":   stories_done,
        "blocked_count":  blockers,
        "flagged_count":  flagged,

        # Breakdowns
        "status_counts":            status_counts,
        "unfinished_status_counts": unfinished_status_counts,
        "issue_type_counts":        dict(sorted(issue_type_counts.items(), key=lambda x: -x[1])),
        "assignee_counts":          dict(sorted(assignee_counts.items(),   key=lambda x: -x[1])),
        "age_buckets":              age_buckets,
        "avg_unfinished_age_days":  round(sum(age_values) / len(age_values), 1) if age_values else None,

        # Signals
        "no_data_signals": no_data_signals,
        "signals": {
            "commitment": {
                "score": c_score, "pct": c_pct,
                "raw":  f"{done}/{total} issues done",
                "no_data": total == 0,
            },
            "carryover": {
                "score": co_score, "pct": co_pct,
                "raw":  f"{carried_over}/{total} carried over",
                "no_data": total == 0,
            },
            "cycle_time": {
                "score": cy_score, "pct": cy_pct,
                "raw":   (
                    f"avg {round(current_avg_ct,1) if current_avg_ct else 'N/A'} days"
                    + (f" (prev: {round(prev_avg_ct,1)})" if prev_avg_ct else "")
                ),
                "no_data": current_avg_ct is None or prev_avg_ct is None,
            },
            "bug_ratio": {
                "score": b_score, "pct": b_pct,
                "raw":  f"{new_bugs} new bugs / {total} total issues",
                "no_data": total == 0 and new_bugs == 0,
            },
        },
        "formula_breakdown": fb,
        "weights": dict(weights),
        "signal_thresholds": _signal_threshold_texts(),

        # Burndown
        "burndown": bd,

        # Developer activity
        "dev_activity": dev_activity,

        # Misc
        "bug_change_pct":          bug_change_pct,
        "bug_change_arrow":        bug_change_arrow,
        "current_avg_cycle_time":  round(current_avg_ct, 1) if current_avg_ct is not None else None,
        "execution": {
            "completed":       done,
            "unfinished":      carried_over,
            "completion_pct":  c_pct,
            "carryover_pct":   co_pct,
        },
        "formula": (
            f"({c_score}×0.35) + ({co_score}×0.25) + ({cy_score}×0.20) + ({b_score}×0.20)"
            + (f" + burndown nudge ({bd_nudge:+d})" if bd_nudge else "")
            + f" = *{health}*"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ─── SLACK FORMATTING ─────────────────────────────────────────────────────────

def format_slack_message(r: dict) -> str:
    score = r["health_score"]
    health_dot = "🟢" if score >= 85 else "🟡" if score >= 70 else "🟠" if score >= 50 else "🔴"

    filled = round(score / 10)
    bar    = "█" * filled + "░" * (10 - filled)

    def sig_dot(s):
        return "🟢" if s >= 85 else "🟡" if s >= 70 else "🟠" if s >= 50 else "🔴"
    def nd(k):
        return " _— no data yet_" if r["signals"][k].get("no_data") else ""

    sigs = r["signals"]
    fb   = r["formula_breakdown"]
    weights = r["weights"]

    sig_rows = (
        f"{sig_dot(sigs['commitment']['score'])}  *Commitment*   "
        f"{sigs['commitment']['raw']}  →  *{sigs['commitment']['score']} pts*{nd('commitment')}\n"
        f"{sig_dot(sigs['carryover']['score'])}  *Carryover*    "
        f"{sigs['carryover']['raw']}  →  *{sigs['carryover']['score']} pts*{nd('carryover')}\n"
        f"{sig_dot(sigs['cycle_time']['score'])}  *Cycle Time*   "
        f"{sigs['cycle_time']['raw']}  →  *{sigs['cycle_time']['score']} pts*{nd('cycle_time')}\n"
        f"{sig_dot(sigs['bug_ratio']['score'])}  *Bug Ratio*    "
        f"{sigs['bug_ratio']['raw']}  →  *{sigs['bug_ratio']['score']} pts*{nd('bug_ratio')}\n"
        f"🐛  *New Bugs*     {r['new_bugs']} created this sprint "
        f"({r['new_bugs_done']} resolved)   |   "
        f"📦 *Carried Bugs*  {r['carried_bugs']} from previous sprints"
    )

    bd = r.get("burndown", {})
    if bd:
        track_icon = "✅" if bd.get("on_track") else ("⚠️" if not bd.get("is_extended") else "🔴")
        ext_note   = " _(sprint overran)_" if bd.get("is_extended") else ""
        bd_line    = (
            f"\n*Burndown*   Day {bd['elapsed_days']}/{bd['total_days']}  ·  "
            f"{bd['current_remaining']} issues remaining  ·  "
            f"Ideal: {bd['ideal_remaining']}  ·  "
            f"{track_icon} {'On track' if bd.get('on_track') else 'Behind'}{ext_note}  ·  "
            f"Velocity: {bd['velocity']}/day  ·  "
            f"Projected finish: {bd['projected_end']}\n"
        )
    else:
        bd_line = ""

    formula_line = (
        f"`{sigs['commitment']['score']}×0.35` + "
        f"`{sigs['carryover']['score']}×0.25` + "
        f"`{sigs['cycle_time']['score']}×0.20` + "
        f"`{sigs['bug_ratio']['score']}×0.20`"
    )
    if r.get("bd_nudge"):
        formula_line = (
            f"`{sigs['commitment']['score']}x{weights['commitment']:.2f}` + "
            f"`{sigs['carryover']['score']}x{weights['carryover']:.2f}` + "
            f"`{sigs['cycle_time']['score']}x{weights['cycle_time']:.2f}` + "
            f"`{sigs['bug_ratio']['score']}x{weights['bug_ratio']:.2f}`"
        )
        formula_line += f" + burndown nudge `{r['bd_nudge']:+d}`"
    else:
        formula_line = (
            f"`{sigs['commitment']['score']}x{weights['commitment']:.2f}` + "
            f"`{sigs['carryover']['score']}x{weights['carryover']:.2f}` + "
            f"`{sigs['cycle_time']['score']}x{weights['cycle_time']:.2f}` + "
            f"`{sigs['bug_ratio']['score']}x{weights['bug_ratio']:.2f}`"
        )
    formula_line += (
        f"  =  *{fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}*"
        f"  =  *{score}*"
    )

    status_lines = "\n".join(
        f"  • {k}:  {v}"
        for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1])
    ) or "  • No issues found"

    no_data_note = (
        "\n> ℹ️ _Sprint has no issues yet — signals with no data used a neutral score of 70._\n"
        if r["no_data_signals"] else ""
    )

    state_banner = ""
    if r["sprint_state"] == "extended":
        state_banner = "\n> ⚠️ _Sprint has passed its end date but hasn't been closed yet._\n"
    elif r["sprint_state"] == "closed":
        state_banner = "\n> 📋 _No active sprint found — showing last closed sprint._\n"

    date_range = (
        f"{r['sprint_start']} → {r['sprint_end']}"
        if r["sprint_start"] and r["sprint_end"] else "Dates not set"
    )
    progress_note = ""
    if r.get("sprint_progress_pct") is not None:
        progress_note = f"   ·   Day {r.get('elapsed_days','?')}/{r.get('total_days','?')} ({r['sprint_progress_pct']}%)"

    # Developer activity summary for Slack
    dev_lines = ""
    if r.get("dev_activity"):
        dev_lines = "\n*Today's Activity*\n"
        for dev in r["dev_activity"]:
            stale_count = sum(1 for i in dev["issues"] if i["is_stale"])
            stale_note  = f" ⚠️ {stale_count} stale" if stale_count else ""
            dev_lines  += f"  👤 *{dev['name']}* — {len(dev['issues'])} issue(s) updated{stale_note}\n"
            for iss in dev["issues"]:
                type_icon = "🐛" if iss["type"] == BUG_TYPE else "📖" if iss["type"] == STORY_TYPE else "⚡"
                stale_tag = " 🔴 _stale_" if iss["is_stale"] else ""
                active_tag = f" _(active {iss['active_days']}d)_" if iss["active_days"] > 1 else ""
                dev_lines += f"    {type_icon} {iss['key']} · {iss['status']}{active_tag}{stale_tag}\n"

    return (
        f"📊  *Sprint Health Report*  —  Lumofy QA\n"
        f"*{r['sprint_name']}*   ·   {date_range}{progress_note}\n"
        f"{'─' * 44}\n"
        f"\n"
        f"{health_dot}  *Health Score:  {score} / 100*\n"
        f"`{bar}`\n"
        f"_{r['health_label'].title()}_\n"
        f"{state_banner}"
        f"{no_data_note}"
        f"\n"
        f"*Signals*\n"
        f"{sig_rows}\n"
        f"{bd_line}"
        f"\n"
        f"*Formula*\n"
        f"{formula_line}\n"
        f"\n"
        f"{'─' * 44}\n"
        f"*Issue Status*\n"
        f"{status_lines}\n"
        f"\n"
        f"{dev_lines}"
        f"\n"
        f"🐛 Bugs: *{r['bugs']}*   |   📦 Scope: *{r['total']}*   |   🚧 Blockers: *{r['blocked_count']}*\n"
        f"\n"
        f"_Generated {r['generated_at']}  ·  Lumofy QA Dashboard_"
    )


def format_slack_site_message(r: dict, site_url: str, pdf_url: str = "") -> str:
    score      = r["health_score"]
    health_dot = "🟢" if score >= 85 else "🟡" if score >= 70 else "🟠" if score >= 50 else "🔴"
    bugs_line  = f"New Bugs: {r['new_bugs']} | Carried: {r['carried_bugs']}"
    if r.get("bug_change_pct") is not None:
        bugs_pct      = abs(r["bug_change_pct"])
        bugs_pct_text = str(int(bugs_pct)) if float(bugs_pct).is_integer() else str(bugs_pct)
        bugs_line     = f"New Bugs: {r['new_bugs']} ({r['bug_change_arrow']} {bugs_pct_text}%) | Carried: {r['carried_bugs']}"
    cycle_time = (
        f"{r['current_avg_cycle_time']} days"
        if r.get("current_avg_cycle_time") is not None else "N/A"
    )
    bd      = r.get("burndown", {})
    bd_note = ""
    if bd:
        track = "✅ On track" if bd.get("on_track") else "⚠️ Behind"
        bd_note = f"\nBurndown: {bd['current_remaining']} remaining · {track}"

    return (
        "🚀 Sprint Health Report Ready — Lumofy QA\n\n"
        f"Score: {score}/100 {health_dot}\n"
        f"{bugs_line}\n"
        f"Cycle Time: {cycle_time}"
        f"{bd_note}\n\n"
        "🔗 View Report:\n"
        f"{site_url}"
    )


# ─── HTML REPORT ──────────────────────────────────────────────────────────────

def _build_burndown_svg(bd: dict) -> str:
    if not bd or not bd.get("actual_line"):
        return "<p style='color:#4a5568;font-style:italic'>No burndown data available.</p>"

    W, H   = 620, 260
    PAD_L  = 48
    PAD_R  = 20
    PAD_T  = 20
    PAD_B  = 44
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    actual = bd["actual_line"]
    ideal  = bd["ideal_line"]
    max_y  = max(bd["total_issues"], 1)

    def cx(day, total):
        return round(PAD_L + day / total * plot_w, 2)
    def cy(val):
        return round(PAD_T + (1 - val / max_y) * plot_h, 2)

    ideal_pts  = " ".join(f"{cx(d, bd['total_days'])},{cy(v)}" for d, v in enumerate(ideal))
    actual_pts = " ".join(f"{cx(d, bd['total_days'])},{cy(v)}" for d, v in enumerate(actual))

    grid_lines = ""
    for pct in [0, 20, 40, 60, 80, 100]:
        val = max_y * pct / 100
        y   = cy(val)
        grid_lines += (
            f'<line x1="{PAD_L}" y1="{y}" x2="{W - PAD_R}" y2="{y}" stroke="#1e3a5f" stroke-width="1"/>'
            f'<text x="{PAD_L - 6}" y="{y + 4}" text-anchor="end" font-size="10" fill="#4a90d9">{round(max_y * pct / 100)}</text>'
        )

    x_labels   = ""
    label_list = bd.get("ideal_labels", [])
    step       = max(1, len(label_list) // 6)
    for idx in range(0, len(label_list), step):
        x = cx(idx, bd["total_days"])
        x_labels += (
            f'<text x="{x}" y="{H - PAD_B + 16}" text-anchor="middle" font-size="10" fill="#4a90d9">{label_list[idx]}</text>'
        )

    today_x    = cx(min(bd["elapsed_days"], bd["total_days"]), bd["total_days"])
    today_line = (
        f'<line x1="{today_x}" y1="{PAD_T}" x2="{today_x}" y2="{H - PAD_B}" '
        f'stroke="#1a6bff" stroke-width="1.5" stroke-dasharray="4,3"/>'
        f'<text x="{today_x + 4}" y="{PAD_T + 12}" font-size="10" fill="#1a6bff">Today</text>'
    )

    track_color = "#00d4aa" if bd.get("on_track") else "#ff4757"

    return f"""<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;max-width:{W}px;height:auto;display:block">
  {grid_lines}
  <line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{H - PAD_B}" stroke="#1e3a5f" stroke-width="1.5"/>
  <line x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - PAD_R}" y2="{H - PAD_B}" stroke="#1e3a5f" stroke-width="1.5"/>
  <polyline points="{ideal_pts}" fill="none" stroke="#2d5a8e" stroke-width="2" stroke-dasharray="6,4"/>
  <polyline points="{actual_pts}" fill="none" stroke="{track_color}" stroke-width="2.5"/>
  {today_line}
  {x_labels}
  <line x1="{PAD_L + 8}" y1="{H - 6}" x2="{PAD_L + 24}" y2="{H - 6}" stroke="#2d5a8e" stroke-width="2" stroke-dasharray="4,3"/>
  <text x="{PAD_L + 28}" y="{H - 2}" font-size="10" fill="#4a90d9">Ideal</text>
  <line x1="{PAD_L + 72}" y1="{H - 6}" x2="{PAD_L + 88}" y2="{H - 6}" stroke="{track_color}" stroke-width="2.5"/>
  <text x="{PAD_L + 92}" y="{H - 2}" font-size="10" fill="#4a90d9">Actual</text>
</svg>"""


def _build_dev_activity_html(dev_activity: list) -> str:
    if not dev_activity:
        return "<p style='color:#4a90d9;font-style:italic;text-align:center;padding:20px'>No activity recorded today.</p>"

    type_icons = {
        "Bug":         ("🐛", "#ff4757"),
        "Story":       ("📖", "#1a6bff"),
        "Sub-task":    ("🔧", "#4a90d9"),
        "Enhancement": ("⚡", "#00d4aa"),
        "Improvement": ("⚡", "#00d4aa"),
        "Task":        ("📋", "#a78bfa"),
    }

    html = ""
    for dev in dev_activity:
        stale_count = sum(1 for i in dev["issues"] if i["is_stale"])
        stale_badge = (
            f'<span class="dev-stale-badge">⚠️ {stale_count} stale</span>'
            if stale_count else ""
        )
        issues_html = ""
        for iss in dev["issues"]:
            icon, color = type_icons.get(iss["type"], ("📋", "#4a90d9"))
            done_style  = "opacity:0.6;text-decoration:line-through;" if iss["is_done"] else ""
            stale_tag   = (
                f'<span class="issue-stale-tag">🔴 Stale ({iss["active_days"]}d / threshold {iss["stale_threshold"]}d)</span>'
                if iss["is_stale"] else ""
            )
            active_tag = (
                f'<span class="issue-active-tag">Active {iss["active_days"]}d</span>'
                if iss["active_days"] > 1 and not iss["is_stale"] else ""
            )
            pts_tag = (
                f'<span class="issue-pts-tag">{iss["story_points"]} pts</span>'
                if iss["story_points"] else ""
            )
            done_tag = (
                '<span class="issue-done-tag">✓ Done</span>'
                if iss["is_done"] else ""
            )
            issues_html += f"""
            <div class="dev-issue {'stale' if iss['is_stale'] else ''}">
              <span class="issue-icon" style="color:{color}">{icon}</span>
              <div class="issue-body">
                <a href="{iss['url']}" target="_blank" class="issue-key">{iss['key']}</a>
                <span class="issue-summary" style="{done_style}">{escape(iss['summary'][:70])}{'…' if len(iss['summary']) > 70 else ''}</span>
                <div class="issue-tags">
                  <span class="issue-status-tag">{escape(iss['status'])}</span>
                  {pts_tag}{active_tag}{stale_tag}{done_tag}
                </div>
              </div>
            </div>"""

        html += f"""
        <div class="dev-card">
          <div class="dev-header">
            <div class="dev-avatar-wrap">
              {'<img src="' + dev['avatar'] + '" class="dev-avatar"/>' if dev['avatar'] else '<div class="dev-avatar-placeholder">' + dev['name'][0] + '</div>'}
            </div>
            <div class="dev-info">
              <div class="dev-name">{escape(dev['name'])}</div>
              <div class="dev-meta">{len(dev['issues'])} issue(s) updated today {stale_badge}</div>
            </div>
          </div>
          <div class="dev-issues">{issues_html}</div>
        </div>"""

    return html


def write_html_report(r: dict, output_path: str = "sprint_health_report.html") -> str:
    score       = r["health_score"]
    score_class = "green" if score >= 85 else "yellow" if score >= 70 else "orange" if score >= 50 else "red"
    fb          = r["formula_breakdown"]
    sigs        = r["signals"]
    bd          = r.get("burndown", {})
    weights     = r["weights"]
    thresholds  = r["signal_thresholds"]

    def signal_color(s):
        return "green" if s >= 85 else "yellow" if s >= 70 else "orange" if s >= 50 else "red"

    def nd_badge(sig_key):
        if r["signals"][sig_key].get("no_data"):
            return '<span class="no-data-badge">no data — neutral</span>'
        return ""

    issue_type_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td>"
        f"<td>{round(v / r['total'] * 100, 1) if r['total'] else 0}%</td></tr>"
        for k, v in r["issue_type_counts"].items()
    ) or "<tr><td colspan='3'>No data</td></tr>"

    assignee_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td>"
        f"<td><div class='bar'><span style='width:{round(v / r['total'] * 100, 1) if r['total'] else 0}%'></span></div></td></tr>"
        for k, v in list(r["assignee_counts"].items())[:10]
    ) or "<tr><td colspan='3'>No data</td></tr>"

    carryover_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(r["unfinished_status_counts"].items(), key=lambda x: -x[1])
    ) or "<tr><td colspan='2'>No unfinished work</td></tr>"

    _co = max(r["carried_over"], 1)
    age_rows = "\n".join(
        f"<tr><td>{k}</td><td>{v}</td>"
        f"<td><div class='bar'><span style='width:{round(v / _co * 100, 1)}%'></span></div></td></tr>"
        for k, v in r["age_buckets"].items()
    )

    signal_defs = [
        {
            "key":   "commitment",
            "label": "Commitment Reliability",
            "score": sigs["commitment"]["score"],
            "metric": sigs["commitment"]["raw"],
            "pct":   sigs["commitment"]["pct"],
            "formula": "Completed ÷ Committed × 100",
            "thresholds": "85–95% → 100 pts<br>70–84% → 70 pts<br>50–69% → 40 pts<br>&lt;50% → 0 pts",
        },
        {
            "key":   "carryover",
            "label": "Carryover Rate",
            "score": sigs["carryover"]["score"],
            "metric": sigs["carryover"]["raw"],
            "pct":   sigs["carryover"]["pct"],
            "formula": "Carried ÷ Total Scope × 100",
            "thresholds": "&lt;10% → 100 pts<br>10–20% → 70 pts<br>20–30% → 40 pts<br>&gt;30% → 0 pts",
        },
        {
            "key":   "cycle_time",
            "label": "Cycle Time Stability",
            "score": sigs["cycle_time"]["score"],
            "metric": sigs["cycle_time"]["raw"],
            "pct":   (
                f"{sigs['cycle_time']['pct']}% vs 3-sprint avg"
                if sigs["cycle_time"]["pct"] is not None else "No baseline"
            ),
            "formula": "Current avg vs Last 3-sprint avg",
            "thresholds": "±10% → 100 pts<br>+10–20% → 70 pts<br>+20–30% → 40 pts<br>&gt;30% → 0 pts",
        },
        {
            "key":   "bug_ratio",
            "label": "Bug Ratio (New Only)",
            "score": sigs["bug_ratio"]["score"],
            "metric": sigs["bug_ratio"]["raw"],
            "pct":   sigs["bug_ratio"]["pct"],
            "formula": "New Bugs (created this sprint) ÷ Total Issues",
            "thresholds": "&lt;15% → 100 pts<br>15–25% → 70 pts<br>25–35% → 40 pts<br>&gt;35% → 0 pts",
        },
    ]

    for signal_def in signal_defs:
        signal_def["thresholds"] = thresholds[signal_def["key"]]

    signals_html = ""
    for sd in signal_defs:
        sc = signal_color(sd["score"])
        signals_html += f"""
        <div class="signal-card">
          <div class="signal-label">{sd['label']}</div>
          <div class="signal-score {sc}">{sd['score']}<span class="signal-unit">/100</span></div>
          <div class="signal-metric">{sd['metric']} &nbsp;·&nbsp; {sd['pct']}%</div>
          {nd_badge(sd['key'])}
          <div class="signal-explanation">
            <div class="explanation-title">How it's calculated:</div>
            {sd['formula']}<br>
            <strong>Scoring:</strong><br>{sd['thresholds']}
          </div>
        </div>"""

    # Bug cards HTML
    new_bug_pct      = round(r["new_bugs"] / r["total"] * 100, 1) if r["total"] else 0
    new_bugs_res_pct = round(r["new_bugs_done"] / r["new_bugs"] * 100, 1) if r["new_bugs"] else 0
    bug_cards_html   = f"""
    <div class="bug-cards">
      <div class="bug-card new-bugs">
        <div class="bug-card-icon">🟡</div>
        <div class="bug-card-title">New Bugs</div>
        <div class="bug-card-count">{r['new_bugs']}</div>
        <div class="bug-card-sub">Created this sprint</div>
        <div class="bug-card-ratio">Bug Ratio: <strong>{new_bug_pct}%</strong> of total issues</div>
        <div class="bug-card-resolved">✓ Resolved: <strong>{r['new_bugs_done']}</strong> ({new_bugs_res_pct}%)</div>
        <div class="bug-card-note">⚡ Counts toward Health Score</div>
      </div>
      <div class="bug-card carried-bugs">
        <div class="bug-card-icon">🔴</div>
        <div class="bug-card-title">Carried Bugs</div>
        <div class="bug-card-count">{r['carried_bugs']}</div>
        <div class="bug-card-sub">From previous sprints</div>
        <div class="bug-card-note">ℹ️ Display only — not in Health Score</div>
      </div>
    </div>"""

    burndown_svg  = _build_burndown_svg(bd)
    bd_track_cls  = "green" if bd.get("on_track") else "red"
    bd_track_txt  = "On track ✅" if bd.get("on_track") else "Behind ideal ⚠️"
    if bd.get("is_extended"):
        bd_track_cls = "red"
        bd_track_txt = "Sprint overran 🔴"
    burndown_stats = ""
    if bd:
        burndown_stats = f"""
        <div class="bd-stats">
          <div class="bd-stat"><div class="bd-stat-val">{bd['elapsed_days']}/{bd['total_days']}</div><div class="bd-stat-lbl">Days Elapsed</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{bd['current_remaining']}</div><div class="bd-stat-lbl">Remaining</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{bd['ideal_remaining']}</div><div class="bd-stat-lbl">Ideal Remaining</div></div>
          <div class="bd-stat"><div class="bd-stat-val {bd_track_cls}">{bd_track_txt}</div><div class="bd-stat-lbl">Status</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{bd['velocity']}/day</div><div class="bd-stat-lbl">Velocity</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{bd['projected_end']}</div><div class="bd-stat-lbl">Projected Finish</div></div>
        </div>"""

    no_data_banner = ""
    if r["no_data_signals"]:
        no_data_banner = '<div class="no-data-banner">ℹ️ Some signals had no data yet. A neutral score of 70 was used.</div>'

    state_banner = ""
    if r["sprint_state"] == "extended":
        state_banner = '<div class="state-banner extended">⚠️ Sprint has passed its end date but has not been closed in Jira yet.</div>'
    elif r["sprint_state"] == "closed":
        state_banner = '<div class="state-banner closed">📋 No active sprint — showing data from the most recently closed sprint.</div>'

    progress_pct = r.get("sprint_progress_pct") or 0
    date_range   = (
        f"{escape(r['sprint_start'])} → {escape(r['sprint_end'])}"
        if r["sprint_start"] and r["sprint_end"] else "Dates not set"
    )

    bd_nudge_html = ""
    if r.get("bd_nudge"):
        bd_nudge_html = (
            f"<div class='formula-row'><div class='formula-component'>"
            f"<span>Burndown Nudge</span>"
            f"<span class='formula-code'>{r['bd_nudge']:+d} pts</span></div>"
            f"<strong>{'bonus' if r['bd_nudge'] > 0 else 'penalty'}</strong></div>"
        )

    dev_activity_html = _build_dev_activity_html(r.get("dev_activity", []))

    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sprint Health — {escape(r['sprint_name'])}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
  background:#050d1a;color:#e0eaff;padding:32px 16px;min-height:100vh}}
.container{{max-width:1060px;margin:0 auto}}

/* ── Header ── */
.header{{text-align:center;margin-bottom:36px}}
.lumofy-logo{{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:18px}}
.logo-mark{{width:32px;height:32px;background:linear-gradient(135deg,#1a6bff,#00d4aa);
  clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);}}
.logo-text{{font-size:22px;font-weight:800;color:#fff;letter-spacing:-0.5px}}
.logo-text span{{color:#1a6bff}}
.header h1{{font-size:30px;font-weight:700;color:#fff;margin-bottom:6px}}
.header p{{font-size:13px;color:#4a90d9}}
.progress-bar-wrap{{background:rgba(26,107,255,.15);border-radius:999px;height:4px;
  width:260px;margin:12px auto 0;border:1px solid rgba(26,107,255,.2)}}
.progress-bar-fill{{height:4px;border-radius:999px;
  background:linear-gradient(90deg,#1a6bff,#00d4aa)}}

/* ── Cards ── */
.card{{background:rgba(10,20,40,.8);backdrop-filter:blur(20px);border-radius:16px;
  padding:32px 28px;margin-bottom:24px;
  border:1px solid rgba(26,107,255,.2);
  box-shadow:0 4px 24px rgba(0,0,0,.4)}}

/* ── Score ── */
.score-wrap{{text-align:center}}
.score-circle{{width:150px;height:150px;border-radius:50%;margin:0 auto 20px;
  display:flex;align-items:center;justify-content:center;flex-direction:column;
  font-weight:700;border:2px solid rgba(26,107,255,.3)}}
.score-circle.green{{background:linear-gradient(135deg,#00d4aa22,#00d4aa44);border-color:#00d4aa}}
.score-circle.yellow{{background:linear-gradient(135deg,#fbbf2422,#fbbf2444);border-color:#fbbf24}}
.score-circle.orange{{background:linear-gradient(135deg,#fb923c22,#fb923c44);border-color:#fb923c}}
.score-circle.red{{background:linear-gradient(135deg,#ff475722,#ff475744);border-color:#ff4757}}
.score-number{{font-size:52px;color:#fff;line-height:1}}
.score-number.green{{color:#00d4aa}}.score-number.yellow{{color:#fbbf24}}
.score-number.orange{{color:#fb923c}}.score-number.red{{color:#ff4757}}
.score-label{{font-size:12px;color:#4a90d9;margin-top:2px}}
.health-status{{font-size:17px;font-weight:600;margin-top:14px;color:#e0eaff}}
.health-sub{{font-size:12px;color:#4a90d9;margin-top:6px}}

/* ── Banners ── */
.no-data-banner,.state-banner{{border-radius:10px;padding:12px 16px;
  margin-bottom:20px;font-size:13px}}
.no-data-banner{{background:rgba(251,191,36,.08);border-left:3px solid #fbbf24;color:#fbbf24}}
.state-banner.extended{{background:rgba(255,71,87,.08);border-left:3px solid #ff4757;color:#ff4757}}
.state-banner.closed{{background:rgba(26,107,255,.08);border-left:3px solid #1a6bff;color:#4a90d9}}

/* ── Section title ── */
.section-title{{font-size:16px;font-weight:700;color:#4a90d9;
  margin:32px 0 14px;text-transform:uppercase;letter-spacing:.8px;
  display:flex;align-items:center;gap:8px}}
.section-title::after{{content:'';flex:1;height:1px;background:rgba(26,107,255,.2)}}

/* ── Signals ── */
.signals-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin-bottom:24px}}
.signal-card{{background:rgba(10,20,40,.9);border-radius:14px;padding:22px 16px;
  border:1px solid rgba(26,107,255,.2);text-align:center;
  transition:transform .25s,border-color .25s}}
.signal-card:hover{{transform:translateY(-4px);border-color:#1a6bff}}
.signal-label{{font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.8px;color:#4a90d9;margin-bottom:10px}}
.signal-score{{font-size:44px;font-weight:900;margin-bottom:6px;
  display:flex;align-items:baseline;justify-content:center;gap:3px}}
.signal-score.red{{color:#ff4757}}.signal-score.orange{{color:#fb923c}}
.signal-score.yellow{{color:#fbbf24}}.signal-score.green{{color:#00d4aa}}
.signal-unit{{font-size:16px;color:#2d5a8e}}
.signal-metric{{font-size:11px;color:#8ab4d9;font-weight:500;margin-bottom:6px}}
.no-data-badge{{display:inline-block;background:rgba(251,191,36,.1);color:#fbbf24;
  font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;
  margin-bottom:8px;text-transform:uppercase;letter-spacing:.4px;border:1px solid rgba(251,191,36,.3)}}
.signal-explanation{{font-size:10px;color:#4a90d9;line-height:1.6;padding:10px;
  background:rgba(26,107,255,.05);border-radius:7px;border-left:2px solid #1a6bff;text-align:left}}
.explanation-title{{font-weight:600;color:#8ab4d9;margin-bottom:4px}}

/* ── Bug Cards ── */
.bug-cards{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:8px}}
@media(max-width:560px){{.bug-cards{{grid-template-columns:1fr}}}}
.bug-card{{border-radius:14px;padding:24px 20px;border:1px solid;text-align:center}}
.bug-card.new-bugs{{background:rgba(251,191,36,.06);border-color:rgba(251,191,36,.3)}}
.bug-card.carried-bugs{{background:rgba(255,71,87,.06);border-color:rgba(255,71,87,.3)}}
.bug-card-icon{{font-size:28px;margin-bottom:8px}}
.bug-card-title{{font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.8px;color:#4a90d9;margin-bottom:8px}}
.bug-card-count{{font-size:48px;font-weight:900;color:#fff;line-height:1;margin-bottom:4px}}
.new-bugs .bug-card-count{{color:#fbbf24}}
.carried-bugs .bug-card-count{{color:#ff4757}}
.bug-card-sub{{font-size:11px;color:#4a90d9;margin-bottom:10px}}
.bug-card-ratio{{font-size:12px;color:#8ab4d9;margin-bottom:4px}}
.bug-card-resolved{{font-size:12px;color:#00d4aa;margin-bottom:8px}}
.bug-card-note{{font-size:10px;padding:4px 10px;border-radius:999px;display:inline-block}}
.new-bugs .bug-card-note{{background:rgba(251,191,36,.1);color:#fbbf24;border:1px solid rgba(251,191,36,.2)}}
.carried-bugs .bug-card-note{{background:rgba(74,144,217,.1);color:#4a90d9;border:1px solid rgba(74,144,217,.2)}}

/* ── Burndown ── */
.bd-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px;margin-bottom:20px}}
.bd-stat{{text-align:center;padding:14px 10px;background:rgba(26,107,255,.06);
  border-radius:10px;border:1px solid rgba(26,107,255,.15)}}
.bd-stat-val{{font-size:16px;font-weight:700;color:#e0eaff;margin-bottom:4px}}
.bd-stat-val.green{{color:#00d4aa}}.bd-stat-val.red{{color:#ff4757}}
.bd-stat-lbl{{font-size:10px;color:#4a90d9;text-transform:uppercase;letter-spacing:.5px}}

/* ── Formula ── */
.formula-breakdown{{margin-bottom:16px;padding:16px;
  background:rgba(26,107,255,.06);border-radius:10px;border-left:3px solid #1a6bff}}
.formula-row{{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:10px;font-size:13px;color:#8ab4d9}}
.formula-row:last-child{{margin-bottom:0}}
.formula-component{{display:flex;align-items:center;gap:8px}}
.formula-code{{font-family:'Monaco','Courier New',monospace;background:rgba(26,107,255,.15);
  padding:2px 6px;border-radius:4px;font-size:11px;color:#4a90d9;font-weight:600;
  border:1px solid rgba(26,107,255,.2)}}
.formula-final{{background:linear-gradient(135deg,rgba(26,107,255,.2),rgba(0,212,170,.1));
  border:1px solid rgba(26,107,255,.3);color:#e0eaff;
  padding:20px;border-radius:10px;text-align:center;font-size:14px;font-weight:700;
  margin-top:16px;font-family:'Monaco','Courier New',monospace}}
.formula-final .value{{font-size:28px;margin-top:8px;color:#1a6bff}}

/* ── Tables ── */
.tables-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
@media(max-width:680px){{.tables-grid{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{border-bottom:1px solid rgba(26,107,255,.1);padding:8px 10px;text-align:left;color:#8ab4d9}}
th{{background:rgba(26,107,255,.08);color:#4a90d9;font-size:10px;
  text-transform:uppercase;letter-spacing:.5px}}
td:first-child{{color:#e0eaff}}
.bar{{background:rgba(26,107,255,.1);border-radius:999px;height:6px;overflow:hidden;min-width:60px}}
.bar>span{{display:block;height:6px;background:linear-gradient(90deg,#1a6bff,#00d4aa)}}

/* ── Developer Activity ── */
.dev-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}}
.dev-card{{background:rgba(10,20,40,.9);border-radius:14px;
  border:1px solid rgba(26,107,255,.2);overflow:hidden}}
.dev-header{{display:flex;align-items:center;gap:12px;padding:16px 18px;
  background:rgba(26,107,255,.06);border-bottom:1px solid rgba(26,107,255,.15)}}
.dev-avatar{{width:40px;height:40px;border-radius:50%;border:2px solid rgba(26,107,255,.3)}}
.dev-avatar-placeholder{{width:40px;height:40px;border-radius:50%;
  background:linear-gradient(135deg,#1a6bff,#00d4aa);
  display:flex;align-items:center;justify-content:center;
  font-size:16px;font-weight:700;color:#fff}}
.dev-name{{font-size:14px;font-weight:700;color:#e0eaff}}
.dev-meta{{font-size:11px;color:#4a90d9;margin-top:2px}}
.dev-stale-badge{{display:inline-block;background:rgba(255,71,87,.15);color:#ff4757;
  font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;
  margin-left:6px;border:1px solid rgba(255,71,87,.3)}}
.dev-issues{{padding:12px 18px;display:flex;flex-direction:column;gap:10px}}
.dev-issue{{display:flex;align-items:flex-start;gap:10px;padding:10px;
  border-radius:8px;background:rgba(26,107,255,.04);border:1px solid rgba(26,107,255,.1);
  transition:border-color .2s}}
.dev-issue:hover{{border-color:rgba(26,107,255,.3)}}
.dev-issue.stale{{background:rgba(255,71,87,.04);border-color:rgba(255,71,87,.2)}}
.issue-icon{{font-size:18px;margin-top:2px;flex-shrink:0}}
.issue-body{{flex:1;min-width:0}}
.issue-key{{font-size:11px;font-weight:700;color:#1a6bff;text-decoration:none;
  font-family:'Monaco','Courier New',monospace}}
.issue-key:hover{{color:#4a90d9}}
.issue-summary{{display:block;font-size:12px;color:#8ab4d9;margin:2px 0 6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.issue-tags{{display:flex;flex-wrap:wrap;gap:4px}}
.issue-status-tag{{font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;
  background:rgba(26,107,255,.1);color:#4a90d9;border:1px solid rgba(26,107,255,.2);
  text-transform:uppercase;letter-spacing:.3px}}
.issue-stale-tag{{font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;
  background:rgba(255,71,87,.1);color:#ff4757;border:1px solid rgba(255,71,87,.3)}}
.issue-active-tag{{font-size:9px;font-weight:600;padding:2px 7px;border-radius:999px;
  background:rgba(0,212,170,.08);color:#00d4aa;border:1px solid rgba(0,212,170,.2)}}
.issue-pts-tag{{font-size:9px;font-weight:600;padding:2px 7px;border-radius:999px;
  background:rgba(167,139,250,.08);color:#a78bfa;border:1px solid rgba(167,139,250,.2)}}
.issue-done-tag{{font-size:9px;font-weight:600;padding:2px 7px;border-radius:999px;
  background:rgba(0,212,170,.08);color:#00d4aa;border:1px solid rgba(0,212,170,.2)}}

/* ── Interpretation ── */
.interp-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
.interp-item{{padding:16px;border-radius:10px;border:1px solid;text-align:center}}
.interp-item.green{{border-color:rgba(0,212,170,.3);background:rgba(0,212,170,.05)}}
.interp-item.yellow{{border-color:rgba(251,191,36,.3);background:rgba(251,191,36,.05)}}
.interp-item.orange{{border-color:rgba(251,146,60,.3);background:rgba(251,146,60,.05)}}
.interp-item.red{{border-color:rgba(255,71,87,.3);background:rgba(255,71,87,.05)}}
.interp-range{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.interp-range.green{{color:#00d4aa}}.interp-range.yellow{{color:#fbbf24}}
.interp-range.orange{{color:#fb923c}}.interp-range.red{{color:#ff4757}}
.interp-status{{font-size:13px;font-weight:700;margin-bottom:4px;color:#e0eaff}}
.interp-desc{{font-size:11px;color:#4a90d9;line-height:1.4}}

/* ── Footer ── */
.footer{{text-align:center;margin-top:40px;padding:20px;color:#2d5a8e;font-size:11px}}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div class="lumofy-logo">
      <div class="logo-mark"></div>
      <div class="logo-text">Lumo<span>fy</span></div>
    </div>
    <h1>Sprint Health Score</h1>
    <p>{date_range} &nbsp;·&nbsp; Day {r.get('elapsed_days','?')}/{r.get('total_days','?')} ({progress_pct}% through sprint)</p>
    <div class="progress-bar-wrap">
      <div class="progress-bar-fill" style="width:{progress_pct}%"></div>
    </div>
  </div>

  {state_banner}
  {no_data_banner}

  <div class="card score-wrap">
    <div class="score-circle {score_class}">
      <div class="score-number {score_class}">{score}</div>
      <div class="score-label">/100</div>
    </div>
    <div class="health-status">{escape(r['health_label'].title())}</div>
    <div class="health-sub">{escape(r['sprint_name'])} &nbsp;·&nbsp; {escape(r['generated_at'])}</div>
  </div>

  <div class="section-title">Health Signals</div>
  <div class="signals-grid">{signals_html}</div>

  <div class="section-title">Bug Breakdown</div>
  <div class="card">{bug_cards_html}</div>

  <div class="section-title">Burndown Chart</div>
  <div class="card">
    {burndown_stats}
    {burndown_svg}
  </div>

  <div class="section-title">Today's Developer Activity</div>
  <div class="card">
    <div class="dev-grid">{dev_activity_html}</div>
  </div>

  <div class="section-title">Weighted Formula</div>
  <div class="card">
    <div class="formula-breakdown">
      <div class="formula-row">
        <div class="formula-component"><span>Commitment Reliability</span><span class="formula-code">{sigs['commitment']['score']} × 0.35</span></div>
        <strong>= {fb['commitment']}</strong>
      </div>
      <div class="formula-row">
        <div class="formula-component"><span>Carryover Rate</span><span class="formula-code">{sigs['carryover']['score']} × 0.25</span></div>
        <strong>= {fb['carryover']}</strong>
      </div>
      <div class="formula-row">
        <div class="formula-component"><span>Cycle Time Stability</span><span class="formula-code">{sigs['cycle_time']['score']} × 0.20</span></div>
        <strong>= {fb['cycle_time']}</strong>
      </div>
      <div class="formula-row">
        <div class="formula-component"><span>Bug Ratio (New Only)</span><span class="formula-code">{sigs['bug_ratio']['score']} × 0.20</span></div>
        <strong>= {fb['bug_ratio']}</strong>
      </div>
      {bd_nudge_html}
    </div>
    <div class="formula-final">
      {fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}
      {f"+ ({r['bd_nudge']:+d})" if r.get('bd_nudge') else ""}
      <div class="value">= {score}</div>
    </div>
  </div>

  <div class="section-title">Sprint Details</div>
  <div class="card">
    <div class="tables-grid">
      <div>
        <strong style="color:#e0eaff">Carryover Breakdown</strong>
        <table style="margin-top:10px">
          <thead><tr><th>Status</th><th>Issues</th></tr></thead>
          <tbody>{carryover_rows}</tbody>
        </table>
      </div>
      <div>
        <strong style="color:#e0eaff">Age of Unfinished Issues</strong>
        <div style="font-size:12px;color:#4a90d9;margin:6px 0">
          Avg: {r['avg_unfinished_age_days'] if r['avg_unfinished_age_days'] is not None else 'N/A'} days
        </div>
        <table>
          <thead><tr><th>Bucket</th><th>Issues</th><th>Distribution</th></tr></thead>
          <tbody>{age_rows}</tbody>
        </table>
      </div>
    </div>
    <div class="tables-grid" style="margin-top:24px">
      <div>
        <strong style="color:#e0eaff">Issue Type Distribution</strong>
        <table style="margin-top:10px">
          <thead><tr><th>Type</th><th>Issues</th><th>%</th></tr></thead>
          <tbody>{issue_type_rows}</tbody>
        </table>
      </div>
      <div>
        <strong style="color:#e0eaff">Workload by Assignee</strong>
        <table style="margin-top:10px">
          <thead><tr><th>Assignee</th><th>Issues</th><th>Share</th></tr></thead>
          <tbody>{assignee_rows}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="section-title">Score Interpretation</div>
  <div class="card">
    <div class="interp-grid">
      <div class="interp-item green"><div class="interp-range green">85–100</div><div class="interp-status">Predictable Sprint</div><div class="interp-desc">Excellent execution and stability</div></div>
      <div class="interp-item yellow"><div class="interp-range yellow">70–84</div><div class="interp-status">Some Instability</div><div class="interp-desc">Good progress, address minor risks</div></div>
      <div class="interp-item orange"><div class="interp-range orange">50–69</div><div class="interp-status">Execution Issues</div><div class="interp-desc">Needs attention on delivery</div></div>
      <div class="interp-item red"><div class="interp-range red">&lt;50</div><div class="interp-status">Sprint Breakdown</div><div class="interp-desc">Critical issues, act now</div></div>
    </div>
  </div>

  <div class="footer">
    Lumofy QA · Sprint Health Dashboard · {escape(r['generated_at'])}
  </div>

</div>
</body>
</html>"""

    out = Path(output_path)
    out.write_text(html_text, encoding="utf-8")
    print(f"[ok] HTML report: {out.resolve()}")
    return str(out.resolve())


# ─── PDF REPORT ───────────────────────────────────────────────────────────────

def write_pdf_report(r: dict, output_path: str | None = None) -> str | None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as pdf_canvas
    except Exception as e:
        print(f"[warn] PDF skipped (reportlab unavailable): {e}")
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
        "Lumofy — Sprint Health Report",
        f"Sprint: {r['sprint_name']}",
        f"Dates:  {r['sprint_start']} → {r['sprint_end']}",
        f"State:  {r['sprint_state'].upper()}",
        "",
        f"Health Score: {r['health_score']}/100  —  {r['health_label']}",
        "",
        "Signals:",
        f"  Commitment:  {r['signals']['commitment']['raw']}  → {r['signals']['commitment']['score']} pts",
        f"  Carryover:   {r['signals']['carryover']['raw']}   → {r['signals']['carryover']['score']} pts",
        f"  Cycle Time:  {r['signals']['cycle_time']['raw']}  → {r['signals']['cycle_time']['score']} pts",
        f"  Bug Ratio:   {r['signals']['bug_ratio']['raw']}   → {r['signals']['bug_ratio']['score']} pts",
        "",
        f"Bug Breakdown:",
        f"  New Bugs (this sprint):     {r['new_bugs']}  ({r['new_bugs_done']} resolved)",
        f"  Carried Bugs (prev sprints): {r['carried_bugs']}  (display only)",
        "",
    ]
    if bd:
        lines += [
            "Burndown:",
            f"  Day {bd['elapsed_days']}/{bd['total_days']}  |  {bd['current_remaining']} remaining  |  Ideal: {bd['ideal_remaining']}",
            f"  Velocity: {bd['velocity']}/day  |  Projected finish: {bd['projected_end']}",
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
            c.showPage()
            c.setFont("Helvetica", 11)
            y = H - 50
        c.drawString(50, y, line)
        y -= 15

    c.save()
    print(f"[ok] PDF report: {out.resolve()}")
    return str(out.resolve())


# ─── SLACK ────────────────────────────────────────────────────────────────────

def send_to_slack(message: str) -> None:
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_TOKEN}",
            "Content-Type":  "application/json",
        },
        json={"channel": SLACK_CHANNEL, "text": message, "mrkdwn": True},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Slack error: {result.get('error')}")
    print(f"[ok] Slack ts={result.get('ts')}")


# ─── MAIN RUN ─────────────────────────────────────────────────────────────────

def run(
    dry_run:         bool = False,
    export_html:     bool = False,
    export_pdf:      bool = False,
    no_slack:        bool = False,
    site_url:        str  = "",
    pdf_url:         str  = "",
    slack_link_only: bool = False,
) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running Sprint Health Score...")

    missing = [k for k, v in {
        "JIRA_EMAIL":       JIRA_EMAIL,
        "JIRA_API_TOKEN":   JIRA_API_TOKEN,
        "SLACK_BOT_TOKEN":  SLACK_TOKEN,
        "SLACK_CHANNEL_ID": SLACK_CHANNEL,
    }.items() if not v]
    if missing and not dry_run:
        raise EnvironmentError(f"Missing env vars: {', '.join(missing)}")

    print("[1/3] Fetching sprint issues from Jira...")
    issues, sprint_info = fetch_sprint_issues()
    sprint_state = SprintState(sprint_info)
    print(f"      → {len(issues)} issues in '{sprint_state.name}' [{sprint_state.state}]")

    print("[2/3] Fetching last 3 closed sprints...")
    prev_sprints = fetch_last_n_sprints(n=3)
    print(f"      → {len(prev_sprints)} closed sprints found")

    print("[3/3] Building report...")
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

    if export_html:
        write_html_report(report)
    if export_pdf:
        write_pdf_report(report)

    print("\n" + "=" * 60)
    print(message)
    print("=" * 60 + "\n")

    if dry_run or no_slack:
        print("[dry-run] Skipping Slack send.")
    else:
        print("Sending to Slack...")
        send_to_slack(message)

    print(f"[done] {report['health_score']}/100 — {report['health_label']}")


# ─── SCHEDULER ────────────────────────────────────────────────────────────────

def run_scheduled(hour: int = 9, minute: int = 0) -> None:
    import pytz
    cairo = pytz.timezone("Africa/Cairo")

    def job():
        local_now = datetime.now(cairo)
        if local_now.hour != hour or local_now.minute != minute:
            return
        try:
            run()
        except Exception as e:
            print(f"[error] {e}")

    time_str = f"{hour:02d}:{minute:02d}"
    schedule.every().day.at(time_str).do(job)
    print(f"[scheduler] Daily at {time_str} Cairo time. Ctrl+C to stop.")
    print(f"[scheduler] Next run: {schedule.next_run()}")

    while True:
        schedule.run_pending()
        time.sleep(30)


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sprint Health Score Reporter")
    parser.add_argument("--dry-run",         action="store_true", help="Print without sending to Slack")
    parser.add_argument("--schedule",        action="store_true", help="Run daily at 09:00 Cairo time")
    parser.add_argument("--schedule-hour",   type=int, default=9,  help="Hour for scheduled run (Cairo, 24h)")
    parser.add_argument("--schedule-minute", type=int, default=0,  help="Minute for scheduled run")
    parser.add_argument("--html",            action="store_true", help="Export HTML report")
    parser.add_argument("--pdf",             action="store_true", help="Export PDF report")
    parser.add_argument("--no-slack",        action="store_true", help="Skip Slack")
    parser.add_argument("--site-url",        type=str, default="", help="Hosted report URL")
    parser.add_argument("--pdf-url",         type=str, default="", help="Hosted PDF URL")
    parser.add_argument("--slack-link-only", action="store_true", help="Send link-only Slack summary")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled(hour=args.schedule_hour, minute=args.schedule_minute)
    else:
        run(
            dry_run=args.dry_run,
            export_html=args.html,
            export_pdf=args.pdf,
            no_slack=args.no_slack,
            site_url=args.site_url,
            pdf_url=args.pdf_url,
            slack_link_only=args.slack_link_only,
        )
