import os
import sys
import math
import argparse
from html import escape
from pathlib import Path
import requests
import schedule
import time
from datetime import datetime, timezone
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

DONE_STATUSES   = {"Done", "Closed", "Resolved"}
BUG_TYPE        = "Bug"
STORY_TYPE      = "Story"

# Weights
W_COMMITMENT  = 0.35
W_CARRYOVER   = 0.25
W_CYCLE_TIME  = 0.20
W_BUG_RATIO   = 0.20


# ─── JIRA CLIENT ────────────────────────────────────────────────────────────────

def jira_get(path: str, params: dict = None) -> dict:
    """Make authenticated GET request to Jira REST API v3."""
    url = f"{JIRA_BASE_URL}/rest/api/3/{path}"
    resp = requests.get(
        url,
        params=params,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    # Handle 410 Gone: /search was deprecated, retry with /search/jql
    if resp.status_code == 410 and path == "search":
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        resp = requests.get(
            url,
            params=params,
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"},
            timeout=15,
        )
    resp.raise_for_status()
    return resp.json()


def agile_get(path: str, params: dict = None) -> dict:
    """Make authenticated GET request to Jira Agile API."""
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/{path}"
    resp = requests.get(
        url,
        params=params,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# Cache board ID so we only fetch once per run
_BOARD_ID_CACHE = None


def get_board_id():
    """
    Return board ID. Priority:
      1. JIRA_BOARD_ID env var (explicit, most reliable)
      2. Auto-detect from Agile API (prefers scrum board)
    """
    global _BOARD_ID_CACHE
    if _BOARD_ID_CACHE is not None:
        return _BOARD_ID_CACHE

    # Priority 1: explicit env var
    if JIRA_BOARD_ID:
        _BOARD_ID_CACHE = JIRA_BOARD_ID
        print(f"[board] Using JIRA_BOARD_ID from env: {_BOARD_ID_CACHE}")
        return _BOARD_ID_CACHE

    # Priority 2: auto-detect via Agile API
    try:
        data = agile_get("board", {"projectKeyOrId": JIRA_PROJECT, "maxResults": 50})
        boards = data.get("values", [])
        if not boards:
            print(f"[warn] No boards found for project '{JIRA_PROJECT}'")
            return None

        # Prefer scrum, fall back to first
        scrum = [b for b in boards if b.get("type") == "scrum"]
        chosen = scrum[0] if scrum else boards[0]
        _BOARD_ID_CACHE = chosen["id"]
        print(f"[board] Auto-detected: '{chosen['name']}' (id={_BOARD_ID_CACHE}, type={chosen.get('type')})")
        return _BOARD_ID_CACHE

    except Exception as e:
        print(f"[warn] Could not auto-detect board ID: {e}")
        return None


def fetch_active_sprint_from_board(board_id):
    """
    Fetch the most relevant sprint from the board:
      1. Active sprint (sprint in progress right now)
      2. If no active sprint, fall back to the most recently closed sprint
         (handles the case where sprint just ended and next hasnt started yet)
    """
    try:
        # Try active first
        data = agile_get(f"board/{board_id}/sprint", {"state": "active"})
        sprints = data.get("values", [])
        if sprints:
            sprint = sprints[0]
            print(f"[sprint] Found active sprint: '{sprint.get('name')}' (id={sprint.get('id')})")
            return sprint

        # No active sprint — fall back to most recent closed sprint
        print(f"[warn] No active sprint on board {board_id} — checking recently closed sprints...")
        data = agile_get(f"board/{board_id}/sprint", {"state": "closed"})
        closed = data.get("values", [])
        if closed:
            # Sort by endDate descending, pick the most recent
            def end_date_key(s):
                return s.get("endDate") or s.get("completeDate") or ""
            latest = sorted(closed, key=end_date_key, reverse=True)[0]
            print(f"[sprint] No active sprint — using last closed: '{latest.get('name')}' (ended {end_date_key(latest)[:10]})")
            return latest

        print(f"[warn] No sprints found at all on board {board_id}")
    except Exception as e:
        print(f"[warn] Could not fetch sprint from board API: {e}")
    return None


def fetch_sprint_issues(sprint_state: str = "active"):
    """
    Fetch all issues in the active sprint.
    Returns (issues_list, sprint_info_dict).

    Resolution strategy (most to least reliable):
      1. Auto-detect board -> get active sprint via Agile API -> query by sprint ID
      2. Fall back to JQL activeSprints(board_id) with detected board
      3. Last resort: global activeSprints() with no board scope
    """
    all_issues = []
    sprint_info = {}

    # Step 1: detect board + sprint via Agile API
    board_id  = get_board_id()
    sprint_id = None

    if board_id:
        active_sprint = fetch_active_sprint_from_board(board_id)
        if active_sprint:
            sprint_id   = active_sprint["id"]
            sprint_info = active_sprint  # has name, startDate, endDate

    # Build JQL based on what we found
    if sprint_id:
        jql = f"project = {JIRA_PROJECT} AND sprint = {sprint_id}"
        print(f"[jql] Querying by sprint ID: {sprint_id}")
    elif board_id:
        jql = f"project = {JIRA_PROJECT} AND sprint in activeSprints({board_id})"
        print(f"[jql] Querying activeSprints({board_id})")
    else:
        jql = f"project = {JIRA_PROJECT} AND sprint in activeSprints()"
        print(f"[jql] Querying global activeSprints() -- board not found")

    # Paginate through all issues
    start_at = 0
    batch    = 50

    while True:
        data = jira_get("search/jql", {
            "jql": jql,
            "fields": (
                "summary,status,issuetype,created,resolutiondate,"
                "customfield_10016,customfield_10020,customfield_10021,"
                "assignee,labels"
            ),
            "maxResults": batch,
            "startAt":    start_at,
        })

        issues = data.get("issues", [])
        if not issues:
            break

        all_issues.extend(issues)

        # Extract sprint_info from issue fields if board API did not provide it
        if not sprint_info:
            sprints = issues[0]["fields"].get("customfield_10020") or []
            # Prefer the active sprint in case issue belongs to multiple sprints
            active = [s for s in sprints if s.get("state", "").lower() == "active"]
            sprint_info = active[0] if active else (sprints[0] if sprints else {})

        start_at += len(issues)
        if start_at >= data.get("total", 0):
            break

    if not all_issues:
        print("[warn] No issues found in active sprint -- sprint may be empty or not started yet.")

    return all_issues, sprint_info

def fetch_last_n_sprints(n: int = 3) -> list[dict]:
    """
    Fetch issues from the last N closed sprints.
    Returns list of sprint summaries for cycle time calculation.
    """
    sprints_data = []

    try:
        data = jira_get("search/jql", {
            "jql": f"project = {JIRA_PROJECT} AND sprint in closedSprints() ORDER BY created DESC",
            "fields": "resolutiondate,created,customfield_10020",
            "maxResults": 200,
        })

        # Group by sprint
        sprint_map = {}
        for issue in data.get("issues", []):
            sprints = issue["fields"].get("customfield_10020") or []
            for s in sprints:
                sid = s.get("id")
                if sid not in sprint_map:
                    sprint_map[sid] = {"info": s, "issues": []}
                sprint_map[sid]["issues"].append(issue["fields"])

        # Sort by sprint id (higher = more recent) and take last N
        sorted_sprints = sorted(sprint_map.values(), key=lambda x: x["info"]["id"], reverse=True)[:n]

        for sp in sorted_sprints:
            cycle_times = []
            for f in sp["issues"]:
                ct = calc_cycle_time_days(f.get("created"), f.get("resolutiondate"))
                if ct is not None:
                    cycle_times.append(ct)
            avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else None
            sprints_data.append({"name": sp["info"].get("name"), "avg_cycle_time": avg_ct})

    except Exception as e:
        print(f"[warn] Could not fetch closed sprints: {e}")

    return sprints_data


# ─── CALCULATIONS ───────────────────────────────────────────────────────────────

def calc_cycle_time_days(created: str, resolved: str) -> float | None:
    """Calculate cycle time in days between created and resolved dates."""
    if not created or not resolved:
        return None
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
        c = datetime.strptime(created[:26] + "+0000", fmt) if "+" not in created else datetime.fromisoformat(created)
        r = datetime.strptime(resolved[:26] + "+0000", fmt) if "+" not in resolved else datetime.fromisoformat(resolved)
        return max(0.0, (r - c).total_seconds() / 86400)
    except Exception:
        return None


def parse_jira_datetime(value: str) -> datetime | None:
    """Parse Jira datetime strings into timezone-aware UTC datetime."""
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def issue_age_days(created: str) -> float | None:
    """Age in days from issue creation until now (UTC)."""
    dt = parse_jira_datetime(created)
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def score_commitment(completed: int, committed: int) -> tuple[int, float]:
    """
    Score commitment reliability.
    FIX: If committed == 0 (sprint not started or no data), return neutral score 70
    instead of 0, since 0 of 0 is not a failure — it's simply no data yet.
    """
    if committed == 0:
        return 70, 0.0  # No data → neutral score

    pct = (completed / committed * 100)
    if 85 <= pct <= 95:
        score = 100
    elif pct >= 70:
        score = 70
    elif pct >= 50:
        score = 40
    else:
        score = 0
    return score, round(pct, 1)


def score_carryover(carried: int, total: int) -> tuple[int, float]:
    """
    Score carryover rate.
    FIX: If total == 0, return neutral score 70 instead of 100,
    since 0 carried from 0 total means no data, not perfect health.
    """
    if total == 0:
        return 70, 0.0  # No data → neutral score

    pct = (carried / total * 100)
    if pct < 10:
        score = 100
    elif pct <= 20:
        score = 70
    elif pct <= 30:
        score = 40
    else:
        score = 0
    return score, round(pct, 1)


def score_cycle_time(current_avg: float | None, prev_avg: float | None) -> tuple[int, float | None]:
    if current_avg is None or prev_avg is None or prev_avg == 0:
        return 70, None  # default: no data
    diff_pct = ((current_avg - prev_avg) / prev_avg) * 100
    if abs(diff_pct) <= 10:
        score = 100
    elif diff_pct <= 20:
        score = 70
    elif diff_pct <= 30:
        score = 40
    else:
        score = 0
    return score, round(diff_pct, 1)


def score_bug_ratio(bugs: int, stories_completed: int) -> tuple[int, float]:
    """
    Score bug ratio.
    FIX: If both bugs and stories_completed are 0, return neutral score 70
    instead of forcing division by 1 which gives 0% → 100 pts misleadingly.
    The sprint has no data yet, so we should not reward or penalize it.
    """
    if stories_completed == 0 and bugs == 0:
        return 70, 0.0  # No data → neutral score

    denom = stories_completed if stories_completed > 0 else 1
    pct = (bugs / denom) * 100
    if pct < 15:
        score = 100
    elif pct <= 25:
        score = 70
    elif pct <= 35:
        score = 40
    else:
        score = 0
    return score, round(pct, 1)


def calc_health_score(c_score, co_score, cy_score, b_score) -> int:
    raw = (c_score * W_COMMITMENT + co_score * W_CARRYOVER +
           cy_score * W_CYCLE_TIME + b_score * W_BUG_RATIO)
    return round(raw)


def health_label(score: int) -> tuple[str, str]:
    """Returns (emoji, label) based on score."""
    if score >= 85:
        return ":green_circle:", "Predictable sprint"
    elif score >= 70:
        return ":yellow_circle:", "Some instability"
    elif score >= 50:
        return ":orange_circle:", "Execution issues"
    else:
        return ":red_circle:", "Sprint breakdown"



def _parse_sprint_date(sprint_info: dict, *keys: str) -> str:
    """
    Extract a date string from sprint_info trying multiple key names.
    Handles both Agile API format (startDate/endDate)
    and customfield_10020 format (start_date/end_date / completeDate).
    Returns YYYY-MM-DD string or empty string if not found.
    """
    for key in keys:
        val = sprint_info.get(key)
        if val:
            # Trim to date part only (handles ISO datetime strings)
            return str(val)[:10]
    return ""



# ─── REPORT BUILDER ─────────────────────────────────────────────────────────────

def build_report(issues: list, sprint_info: dict, prev_sprints: list) -> dict:
    """
    Analyze issues and return a structured report dict.
    """
    total = len(issues)
    done = sum(1 for i in issues if i["fields"]["status"]["name"] in DONE_STATUSES)
    bugs = sum(1 for i in issues if i["fields"]["issuetype"]["name"] == BUG_TYPE)
    stories_done = sum(
        1 for i in issues
        if i["fields"]["issuetype"]["name"] == STORY_TYPE
        and i["fields"]["status"]["name"] in DONE_STATUSES
    )
    carried_over = total - done
    unfinished_issues = [i for i in issues if i["fields"]["status"]["name"] not in DONE_STATUSES]

    # Status breakdown
    status_counts = {}
    issue_type_counts = {}
    assignee_counts = {}
    unfinished_status_counts = {}
    blockers = 0
    flagged = 0
    age_buckets = {"0-3d": 0, "4-7d": 0, "8-14d": 0, "15+d": 0}
    age_values = []

    for i in issues:
        f = i["fields"]
        s = f["status"]["name"]
        status_counts[s] = status_counts.get(s, 0) + 1
        if s not in DONE_STATUSES:
            unfinished_status_counts[s] = unfinished_status_counts.get(s, 0) + 1

        issue_type = f["issuetype"]["name"]
        issue_type_counts[issue_type] = issue_type_counts.get(issue_type, 0) + 1

        assignee = f.get("assignee")
        assignee_name = assignee.get("displayName") if assignee else "Unassigned"
        assignee_counts[assignee_name] = assignee_counts.get(assignee_name, 0) + 1

        labels = [lbl.lower() for lbl in (f.get("labels") or [])]
        status_l = s.lower()
        flagged_field = f.get("customfield_10021")
        if "blocked" in labels or "blocker" in labels or "block" in status_l:
            blockers += 1
        if "flagged" in labels or bool(flagged_field):
            flagged += 1

        if s not in DONE_STATUSES:
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

    # Cycle time for current sprint (done issues only)
    cycle_times = []
    for i in issues:
        f = i["fields"]
        if f["status"]["name"] in DONE_STATUSES:
            ct = calc_cycle_time_days(f.get("created"), f.get("resolutiondate"))
            if ct is not None:
                cycle_times.append(ct)
    current_avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else None

    # Previous sprint avg cycle time
    prev_avg_ct = None
    if prev_sprints:
        valid = [s["avg_cycle_time"] for s in prev_sprints if s["avg_cycle_time"] is not None]
        if valid:
            prev_avg_ct = sum(valid) / len(valid)

    # Scores — FIX: use corrected scoring functions
    c_score, c_pct   = score_commitment(done, total)
    co_score, co_pct = score_carryover(carried_over, total)
    cy_score, cy_pct = score_cycle_time(current_avg_ct, prev_avg_ct)

    # FIX: use stories_done for bug ratio denominator only if > 0,
    # otherwise fall back to total done issues; never pass both as 0 silently.
    bug_denom = stories_done if stories_done > 0 else done
    b_score, b_pct = score_bug_ratio(bugs, bug_denom)

    health = calc_health_score(c_score, co_score, cy_score, b_score)
    emoji, label = health_label(health)

    # FIX: annotate when scores are based on "no data" so the report is honest
    no_data_signals = []
    if total == 0:
        no_data_signals.extend(["commitment", "carryover", "bug_ratio"])
    if current_avg_ct is None or prev_avg_ct is None:
        no_data_signals.append("cycle_time")

    return {
        "sprint_name": (sprint_info.get("name") or sprint_info.get("goal") or "Current Sprint"),
        "sprint_start": _parse_sprint_date(sprint_info, "startDate", "start_date"),
        "sprint_end":   _parse_sprint_date(sprint_info, "endDate",   "end_date"),
        "health_score": health,
        "health_emoji": emoji,
        "health_label": label,
        "total": total,
        "done": done,
        "carried_over": carried_over,
        "bugs": bugs,
        "stories_done": stories_done,
        "blocked_count": blockers,
        "flagged_count": flagged,
        "status_counts": status_counts,
        "unfinished_status_counts": unfinished_status_counts,
        "issue_type_counts": dict(sorted(issue_type_counts.items(), key=lambda x: -x[1])),
        "assignee_counts": dict(sorted(assignee_counts.items(), key=lambda x: -x[1])),
        "age_buckets": age_buckets,
        "avg_unfinished_age_days": round(sum(age_values) / len(age_values), 1) if age_values else None,
        "no_data_signals": no_data_signals,
        "signals": {
            "commitment": {
                "score": c_score,
                "pct": c_pct,
                "raw": f"{done}/{total} issues done",
                "no_data": total == 0,
            },
            "carryover": {
                "score": co_score,
                "pct": co_pct,
                "raw": f"{carried_over}/{total} carried over",
                "no_data": total == 0,
            },
            "cycle_time": {
                "score": cy_score,
                "pct": cy_pct,
                "raw": (
                    f"avg {round(current_avg_ct, 1) if current_avg_ct else 'N/A'} days"
                    + (f" (prev: {round(prev_avg_ct, 1)})" if prev_avg_ct else "")
                ),
                "no_data": current_avg_ct is None or prev_avg_ct is None,
            },
            "bug_ratio": {
                "score": b_score,
                "pct": b_pct,
                "raw": f"{bugs} bugs / {bug_denom} completed",
                "no_data": bug_denom == 0 and bugs == 0,
            },
        },
        "execution": {
            "completed": done,
            "unfinished": carried_over,
            "completion_pct": c_pct,
            "carryover_pct": co_pct,
            "scope_change_pct": round((carried_over / done) * 100, 1) if done else None,
        },
        "formula": (
            f"({c_score}×0.35) + ({co_score}×0.25) + ({cy_score}×0.20) + ({b_score}×0.20) = *{health}*"
        ),
        "formula_breakdown": {
            "commitment": round(c_score * W_COMMITMENT, 1),
            "carryover":  round(co_score * W_CARRYOVER, 1),
            "cycle_time": round(cy_score * W_CYCLE_TIME, 1),
            "bug_ratio":  round(b_score * W_BUG_RATIO, 1),
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ─── SLACK SENDER ───────────────────────────────────────────────────────────────

def format_slack_message(r: dict) -> str:
    """Build a clean, well-structured Slack message from the report dict."""

    # ── Health emoji (real unicode, not :code:) ──────────────────────────────
    score = r["health_score"]
    if score >= 85:
        health_dot = "🟢"
    elif score >= 70:
        health_dot = "🟡"
    elif score >= 50:
        health_dot = "🟠"
    else:
        health_dot = "🔴"

    # ── Score bar (10 blocks) ─────────────────────────────────────────────────
    filled  = round(score / 10)
    bar     = "█" * filled + "░" * (10 - filled)

    # ── Signal rows ───────────────────────────────────────────────────────────
    def sig_dot(s):
        if s >= 85: return "🟢"
        if s >= 70: return "🟡"
        if s >= 50: return "🟠"
        return "🔴"

    def nd(sig_key):
        return " _— no data yet_" if r["signals"][sig_key].get("no_data") else ""

    sigs = r["signals"]
    fb   = r["formula_breakdown"]

    sig_rows = (
        f"{sig_dot(sigs['commitment']['score'])}  *Commitment*      "
        f"{sigs['commitment']['raw']}  →  *{sigs['commitment']['score']} pts*{nd('commitment')}\n"

        f"{sig_dot(sigs['carryover']['score'])}  *Carryover*       "
        f"{sigs['carryover']['raw']}  →  *{sigs['carryover']['score']} pts*{nd('carryover')}\n"

        f"{sig_dot(sigs['cycle_time']['score'])}  *Cycle Time*      "
        f"{sigs['cycle_time']['raw']}  →  *{sigs['cycle_time']['score']} pts*{nd('cycle_time')}\n"

        f"{sig_dot(sigs['bug_ratio']['score'])}  *Bug Ratio*       "
        f"{sigs['bug_ratio']['raw']}  →  *{sigs['bug_ratio']['score']} pts*{nd('bug_ratio')}"
    )

    # ── Formula line ──────────────────────────────────────────────────────────
    formula_line = (
        f"`{sigs['commitment']['score']}×0.35` + "
        f"`{sigs['carryover']['score']}×0.25` + "
        f"`{sigs['cycle_time']['score']}×0.20` + "
        f"`{sigs['bug_ratio']['score']}×0.20`  =  "
        f"*{fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}*  =  *{score}*"
    )

    # ── Issue status ──────────────────────────────────────────────────────────
    status_lines = "\n".join(
        f"  • {k}:  {v}"
        for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1])
    ) or "  • No issues found"

    # ── No-data note ──────────────────────────────────────────────────────────
    no_data_note = (
        "\n> ℹ️ _Sprint has no issues yet — signals with no data used a neutral score of 70._\n"
        if r["no_data_signals"] else ""
    )

    # ── Sprint dates ──────────────────────────────────────────────────────────
    date_range = (
        f"{r['sprint_start']} → {r['sprint_end']}"
        if r["sprint_start"] and r["sprint_end"]
        else "Dates not set"
    )

    return (
        f"📊  *Sprint Health Report*\n"
        f"*{r['sprint_name']}*   ·   {date_range}\n"
        f"{'─' * 40}\n"
        f"\n"
        f"{health_dot}  *Health Score:  {score} / 100*\n"
        f"`{bar}`\n"
        f"_{r['health_label'].title()}_\n"
        f"{no_data_note}"
        f"\n"
        f"*Signals*\n"
        f"{sig_rows}\n"
        f"\n"
        f"*Formula*\n"
        f"{formula_line}\n"
        f"\n"
        f"{'─' * 40}\n"
        f"*Issue Status*\n"
        f"{status_lines}\n"
        f"\n"
        f"🐛 Bugs: *{r['bugs']}*   |   📦 Scope: *{r['total']}*   |   🚧 Blockers: *{r['blocked_count']}*\n"
        f"\n"
        f"_Generated {r['generated_at']}  ·  Claude + Jira API_"
    )


def format_slack_site_message(r: dict, site_url: str, pdf_url: str = "") -> str:
    """Build a concise Slack message focused on hosted report link."""
    date_range = (
        f"{r['sprint_start']} -> {r['sprint_end']}"
        if r["sprint_start"] and r["sprint_end"]
        else "Dates not set"
    )
    score = r["health_score"]
    health_tag = "GOOD" if score >= 85 else "WARN" if score >= 70 else "RISK" if score >= 50 else "BAD"
    label = r["health_label"].lower()
    lines = [
        "*Sprint Health Report*",
        f"*Sprint:* {r['sprint_name']} | *Link:* {site_url}",
        "",
        f"• *Dates:* {date_range}",
        f"• *Score:* {score}/100 [{health_tag}] - {label}",
        f"• *Scope:* {r['total']} issues | *Bugs:* {r['bugs']}",
        "",
        f"_Generated {r['generated_at']}_",
    ]
    return "\n".join(lines)


def write_html_report(r: dict, output_path: str = "sprint_health_report.html") -> str:
    """Render a dynamic HTML report fully built from the report dict."""

    score = r["health_score"]
    score_class = (
        "green" if score >= 85
        else "yellow" if score >= 70
        else "orange" if score >= 50
        else "red"
    )

    completion    = r["execution"]["completion_pct"]
    carryover_pct = r["execution"]["carryover_pct"]
    bug_ratio     = r["signals"]["bug_ratio"]["pct"]
    fb            = r["formula_breakdown"]

    def signal_color(s):
        if s >= 85: return "green"
        if s >= 70: return "yellow"
        if s >= 50: return "orange"
        return "red"

    def nd_badge(sig_key):
        if r["signals"][sig_key].get("no_data"):
            return '<span class="no-data-badge">no data — neutral</span>'
        return ""

    issue_type_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td>"
        f"<td>{round((v / r['total']) * 100, 1) if r['total'] else 0}%</td></tr>"
        for k, v in r["issue_type_counts"].items()
    ) or "<tr><td colspan='3'>No data</td></tr>"

    assignee_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td>"
        f"<td><div class='bar'><span style='width:{round((v / r['total']) * 100, 1) if r['total'] else 0}%'></span></div></td></tr>"
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
    # Build signal cards HTML
    signals_html = ""
    signal_defs = [
        {
            "key": "commitment",
            "label": "Commitment Reliability",
            "score": r["signals"]["commitment"]["score"],
            "metric": r["signals"]["commitment"]["raw"],
            "pct": r["signals"]["commitment"]["pct"],
            "formula": "Completed ÷ Committed × 100",
            "thresholds": "85–95% → 100 pts<br>70–84% → 70 pts<br>50–69% → 40 pts<br>&lt;50% → 0 pts",
        },
        {
            "key": "carryover",
            "label": "Carryover Rate",
            "score": r["signals"]["carryover"]["score"],
            "metric": r["signals"]["carryover"]["raw"],
            "pct": r["signals"]["carryover"]["pct"],
            "formula": "Carried ÷ Total Scope × 100",
            "thresholds": "&lt;10% → 100 pts<br>10–20% → 70 pts<br>20–30% → 40 pts<br>&gt;30% → 0 pts",
        },
        {
            "key": "cycle_time",
            "label": "Cycle Time Stability",
            "score": r["signals"]["cycle_time"]["score"],
            "metric": r["signals"]["cycle_time"]["raw"],
            "pct": (
                f"{r['signals']['cycle_time']['pct']}% vs 3-sprint avg"
                if r["signals"]["cycle_time"]["pct"] is not None
                else "No baseline data"
            ),
            "formula": "Current avg vs Last 3 avg",
            "thresholds": "±10% → 100 pts<br>+10–20% → 70 pts<br>+20–30% → 40 pts<br>&gt;30% → 0 pts",
        },
        {
            "key": "bug_ratio",
            "label": "Bug Ratio",
            "score": r["signals"]["bug_ratio"]["score"],
            "metric": r["signals"]["bug_ratio"]["raw"],
            "pct": r["signals"]["bug_ratio"]["pct"],
            "formula": "Bugs ÷ Stories Completed",
            "thresholds": "&lt;15% → 100 pts<br>15–25% → 70 pts<br>25–35% → 40 pts<br>&gt;35% → 0 pts",
        },
    ]

    for sd in signal_defs:
        sc = signal_color(sd["score"])
        nd = nd_badge(sd["key"])
        signals_html += f"""
        <div class="signal-card">
          <div class="signal-label">{sd['label']}</div>
          <div class="signal-score {sc}">{sd['score']}<span class="signal-unit">/100</span></div>
          <div class="signal-metric">{sd['metric']} &nbsp;·&nbsp; {sd['pct']}%</div>
          {nd}
          <div class="signal-explanation">
            <div class="explanation-title">How it's calculated:</div>
            {sd['formula']}<br>
            <strong>Scoring:</strong><br>{sd['thresholds']}
          </div>
        </div>"""

    no_data_banner = ""
    if r["no_data_signals"]:
        no_data_banner = """
        <div class="no-data-banner">
          ℹ️ Some signals had no data yet (sprint may not have started). A neutral score of 70 was used for those signals instead of 0.
        </div>"""

    html_text = f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sprint Health Score Report — {escape(r['sprint_name'])}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #1a1a2e;
    padding: 32px 16px;
    min-height: 100vh;
  }}

  .container {{ max-width: 1000px; margin: 0 auto; }}

  .header {{ text-align: center; color: white; margin-bottom: 40px; }}
  .header h1 {{ font-size: 36px; font-weight: 700; margin-bottom: 8px; }}
  .header p {{ font-size: 14px; opacity: 0.9; }}

  .score-card {{
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(20px);
    border-radius: 24px;
    padding: 48px 40px;
    margin-bottom: 32px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.1);
    border: 1px solid rgba(255,255,255,0.3);
    text-align: center;
  }}

  .score-circle {{
    width: 160px; height: 160px; border-radius: 50%;
    margin: 0 auto 24px;
    display: flex; align-items: center; justify-content: center; flex-direction: column;
    font-weight: 700;
    box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  }}
  .score-circle.green  {{ background: linear-gradient(135deg,#34d399,#10b981); border: 3px solid #059669; }}
  .score-circle.yellow {{ background: linear-gradient(135deg,#fbbf24,#f59e0b); border: 3px solid #d97706; }}
  .score-circle.orange {{ background: linear-gradient(135deg,#fb923c,#f97316); border: 3px solid #ea580c; }}
  .score-circle.red    {{ background: linear-gradient(135deg,#ef4444,#dc2626); border: 3px solid #b91c1c; }}

  .score-number {{ font-size: 56px; color: white; line-height: 1; }}
  .score-label  {{ font-size: 12px; color: rgba(255,255,255,0.85); margin-top: 4px; }}
  .health-status {{ font-size: 18px; font-weight: 600; margin-top: 20px; color: #1a1a2e; }}
  .health-interpretation {{ font-size: 13px; color: #666; margin-top: 8px; font-style: italic; }}

  .no-data-banner {{
    background: rgba(255,255,255,0.9);
    border-left: 4px solid #f59e0b;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 24px;
    font-size: 13px;
    color: #78350f;
  }}

  .signals-title {{ font-size: 20px; font-weight: 700; color: white; margin-bottom: 24px; margin-top: 40px; }}

  .signals-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 20px;
    margin-bottom: 40px;
  }}

  .signal-card {{
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(20px);
    border-radius: 20px;
    padding: 28px 20px;
    border: 1px solid rgba(255,255,255,0.3);
    box-shadow: 0 8px 24px rgba(0,0,0,0.1);
    transition: transform 0.3s ease, box-shadow 0.3s ease;
    text-align: center;
  }}
  .signal-card:hover {{ transform: translateY(-8px); box-shadow: 0 16px 32px rgba(0,0,0,0.15); }}

  .signal-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; color: #666; margin-bottom: 12px; }}
  .signal-score {{ font-size: 48px; font-weight: 900; margin-bottom: 8px; display: flex; align-items: baseline; justify-content: center; gap: 4px; }}
  .signal-score.red    {{ color: #dc2626; }}
  .signal-score.orange {{ color: #f97316; }}
  .signal-score.yellow {{ color: #f59e0b; }}
  .signal-score.green  {{ color: #10b981; }}
  .signal-unit {{ font-size: 18px; color: #999; }}
  .signal-metric {{ font-size: 12px; color: #333; font-weight: 500; margin-bottom: 8px; }}

  .no-data-badge {{
    display: inline-block;
    background: #fef3c7;
    color: #92400e;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 999px;
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }}

  .signal-explanation {{
    font-size: 11px; color: #666; line-height: 1.6;
    padding: 12px; background: rgba(0,0,0,0.02);
    border-radius: 8px; border-left: 3px solid #667eea;
    text-align: left;
  }}
  .explanation-title {{ font-weight: 600; color: #333; margin-bottom: 6px; }}

  .formula-section, .interpretation-section, .tables-section {{
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(20px);
    border-radius: 20px;
    padding: 32px;
    border: 1px solid rgba(255,255,255,0.3);
    box-shadow: 0 8px 24px rgba(0,0,0,0.1);
    margin-bottom: 32px;
  }}

  .formula-title, .interpretation-title, .tables-title {{
    font-size: 18px; font-weight: 700; color: #1a1a2e; margin-bottom: 24px;
  }}

  .formula-breakdown {{
    margin-bottom: 20px; padding: 16px;
    background: rgba(102,126,234,0.05);
    border-radius: 12px; border-left: 4px solid #667eea;
  }}

  .formula-row {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 12px; font-size: 13px;
  }}
  .formula-row:last-child {{ margin-bottom: 0; }}
  .formula-component {{ display: flex; align-items: center; gap: 8px; }}
  .formula-code {{
    font-family: 'Monaco','Courier New',monospace;
    background: #f3f4f6; padding: 2px 6px;
    border-radius: 4px; font-size: 12px; color: #667eea; font-weight: 600;
  }}

  .formula-final {{
    background: linear-gradient(135deg,#667eea,#764ba2);
    color: white; padding: 24px; border-radius: 12px;
    text-align: center; font-size: 16px; font-weight: 700;
    margin-top: 20px; font-family: 'Monaco','Courier New',monospace;
  }}
  .formula-final .value {{ font-size: 28px; margin-top: 8px; }}

  .interpretation-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap: 16px;
  }}
  .interpretation-item {{
    padding: 16px; border-radius: 12px; border: 2px solid; text-align: center;
  }}
  .interpretation-item.green  {{ border-color:#10b981; background:rgba(16,185,129,0.05); }}
  .interpretation-item.yellow {{ border-color:#f59e0b; background:rgba(245,158,11,0.05); }}
  .interpretation-item.orange {{ border-color:#f97316; background:rgba(249,115,22,0.05); }}
  .interpretation-item.red    {{ border-color:#dc2626; background:rgba(220,38,38,0.05);  }}

  .interpretation-range {{ font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px; }}
  .interpretation-range.green  {{ color:#10b981; }}
  .interpretation-range.yellow {{ color:#f59e0b; }}
  .interpretation-range.orange {{ color:#f97316; }}
  .interpretation-range.red    {{ color:#dc2626; }}
  .interpretation-status {{ font-size:14px;font-weight:700;margin-bottom:8px;color:#1a1a2e; }}
  .interpretation-desc   {{ font-size:12px;color:#666;line-height:1.4; }}

  /* Tables */
  .tables-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media (max-width: 700px) {{ .tables-grid {{ grid-template-columns: 1fr; }} }}
  table {{ width:100%;border-collapse:collapse;font-size:13px; }}
  th,td {{ border-bottom:1px solid #e5e7eb;padding:8px 10px;text-align:left; }}
  th {{ background:#f8fafc;color:#334155;font-size:11px;text-transform:uppercase;letter-spacing:.5px; }}
  .bar {{ background:#e5e7eb;border-radius:999px;height:8px;overflow:hidden;min-width:80px; }}
  .bar > span {{ display:block;height:8px;background:#667eea; }}

  .footer {{
    text-align:center;margin-top:48px;padding:20px;
    color:rgba(255,255,255,0.8);font-size:12px;
  }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1>Sprint Health Score</h1>
    <p>{escape(r['sprint_name'])} &nbsp;·&nbsp; {escape(r['sprint_start'])} → {escape(r['sprint_end'])}</p>
  </div>

  <div class="score-card">
    <div class="score-circle {score_class}">
      <div class="score-number">{score}</div>
      <div class="score-label">/100</div>
    </div>
    <div class="health-status">{escape(r['health_label'].title())}</div>
    <div class="health-interpretation">
      {escape(r['sprint_name'])} &nbsp;·&nbsp; Generated {escape(r['generated_at'])}
    </div>
  </div>

  {no_data_banner}

  <div class="signals-title">Health Signals</div>
  <div class="signals-grid">
    {signals_html}
  </div>

  <div class="formula-section">
    <div class="formula-title">Weighted Health Formula</div>
    <div class="formula-breakdown">
      <div class="formula-row">
        <div class="formula-component">
          <span>Commitment Reliability</span>
          <span class="formula-code">{r['signals']['commitment']['score']} × 0.35</span>
        </div>
        <strong>= {fb['commitment']}</strong>
      </div>
      <div class="formula-row">
        <div class="formula-component">
          <span>Carryover Rate</span>
          <span class="formula-code">{r['signals']['carryover']['score']} × 0.25</span>
        </div>
        <strong>= {fb['carryover']}</strong>
      </div>
      <div class="formula-row">
        <div class="formula-component">
          <span>Cycle Time Stability</span>
          <span class="formula-code">{r['signals']['cycle_time']['score']} × 0.20</span>
        </div>
        <strong>= {fb['cycle_time']}</strong>
      </div>
      <div class="formula-row">
        <div class="formula-component">
          <span>Bug Ratio</span>
          <span class="formula-code">{r['signals']['bug_ratio']['score']} × 0.20</span>
        </div>
        <strong>= {fb['bug_ratio']}</strong>
      </div>
    </div>
    <div class="formula-final">
      Health Score = {fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}
      <div class="value">= {score}</div>
    </div>
  </div>

  <div class="tables-section">
    <div class="tables-title">Sprint Details</div>
    <div class="tables-grid">
      <div>
        <strong>Carryover Breakdown</strong>
        <table style="margin-top:12px">
          <thead><tr><th>Status</th><th>Issues</th></tr></thead>
          <tbody>{carryover_rows}</tbody>
        </table>
      </div>
      <div>
        <strong>Average Age of Unfinished Issues</strong>
        <div style="font-size:13px;color:#555;margin:8px 0">
          Avg: {r['avg_unfinished_age_days'] if r['avg_unfinished_age_days'] is not None else 'N/A'} days
        </div>
        <table>
          <thead><tr><th>Age Bucket</th><th>Issues</th><th>Distribution</th></tr></thead>
          <tbody>{age_rows}</tbody>
        </table>
      </div>
    </div>

    <div class="tables-grid" style="margin-top:28px">
      <div>
        <strong>Work Distribution by Issue Type</strong>
        <table style="margin-top:12px">
          <thead><tr><th>Type</th><th>Issues</th><th>%</th></tr></thead>
          <tbody>{issue_type_rows}</tbody>
        </table>
      </div>
      <div>
        <strong>Workload by Assignee</strong>
        <table style="margin-top:12px">
          <thead><tr><th>Assignee</th><th>Issues</th><th>Share</th></tr></thead>
          <tbody>{assignee_rows}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="interpretation-section">
    <div class="interpretation-title">Health Level Interpretation</div>
    <div class="interpretation-grid">
      <div class="interpretation-item green">
        <div class="interpretation-range green">85–100</div>
        <div class="interpretation-status">Predictable Sprint</div>
        <div class="interpretation-desc">Excellent execution and stability</div>
      </div>
      <div class="interpretation-item yellow">
        <div class="interpretation-range yellow">70–84</div>
        <div class="interpretation-status">Some Instability</div>
        <div class="interpretation-desc">Good progress but address minor risks</div>
      </div>
      <div class="interpretation-item orange">
        <div class="interpretation-range orange">50–69</div>
        <div class="interpretation-status">Execution Issues</div>
        <div class="interpretation-desc">Needs attention on delivery stability</div>
      </div>
      <div class="interpretation-item red">
        <div class="interpretation-range red">&lt;50</div>
        <div class="interpretation-status">Sprint Breakdown</div>
        <div class="interpretation-desc">Critical issues, immediate action needed</div>
      </div>
    </div>
  </div>

  <div class="footer">
    <p>Sprint Health Score &nbsp;·&nbsp; Powered by Claude + Jira API &nbsp;·&nbsp; {escape(r['generated_at'])}</p>
  </div>

</div>
</body>
</html>
"""
    out = Path(output_path)
    out.write_text(html_text, encoding="utf-8")
    print(f"[ok] HTML report written: {out.resolve()}")
    return str(out.resolve())


def write_pdf_report(r: dict, output_path: str | None = None) -> str | None:
    """Render a simple PDF report using reportlab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except Exception as e:
        print(f"[warn] PDF export skipped (reportlab unavailable): {e}")
        return None

    if not output_path:
        output_path = f"sprint-health-{datetime.now().strftime('%Y-%m-%d')}.pdf"

    out = Path(output_path)
    c = canvas.Canvas(str(out), pagesize=A4)
    width, height = A4
    y = height - 50

    fb = r["formula_breakdown"]
    lines = [
        "Sprint Health Report",
        f"Sprint: {r['sprint_name']}",
        f"Date: {r['sprint_start']} -> {r['sprint_end']}",
        "",
        f"Health Score: {r['health_score']}/100",
        f"Health Label: {r['health_label']}",
        "",
        "Signal Breakdown:",
        f"- Commitment Reliability: {r['signals']['commitment']['raw']} | Score: {r['signals']['commitment']['score']}" + (" (no data — neutral)" if r['signals']['commitment'].get('no_data') else ""),
        f"- Carryover Rate: {r['signals']['carryover']['raw']} | Score: {r['signals']['carryover']['score']}" + (" (no data — neutral)" if r['signals']['carryover'].get('no_data') else ""),
        f"- Cycle Time Stability: {r['signals']['cycle_time']['raw']} | Score: {r['signals']['cycle_time']['score']}" + (" (no data — neutral)" if r['signals']['cycle_time'].get('no_data') else ""),
        f"- Bug Ratio: {r['signals']['bug_ratio']['raw']} | Score: {r['signals']['bug_ratio']['score']}" + (" (no data — neutral)" if r['signals']['bug_ratio'].get('no_data') else ""),
        "",
        f"Formula: ({fb['commitment']}) + ({fb['carryover']}) + ({fb['cycle_time']}) + ({fb['bug_ratio']}) = {r['health_score']}",
        "",
        "Issue Status:",
    ]
    for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {v}")
    lines.extend([
        "",
        f"Open Bugs: {r['bugs']} | Total Scope: {r['total']}",
        f"Generated at: {r['generated_at']}",
    ])

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, lines[0])
    y -= 30
    c.setFont("Helvetica", 11)
    for line in lines[1:]:
        if y < 50:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - 50
        c.drawString(50, y, line)
        y -= 16

    c.save()
    print(f"[ok] PDF report written: {out.resolve()}")
    return str(out.resolve())


def send_to_slack(message: str) -> None:
    """Post message to Slack channel."""
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"channel": SLACK_CHANNEL, "text": message, "mrkdwn": True},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Slack error: {result.get('error')}")
    print(f"[ok] Sent to Slack: {result.get('ts')}")


# ─── MAIN ───────────────────────────────────────────────────────────────────────

def run(
    dry_run: bool = False,
    export_html: bool = False,
    export_pdf: bool = False,
    no_slack: bool = False,
    site_url: str = "",
    pdf_url: str = "",
    slack_link_only: bool = False,
) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running Sprint Health Score...")

    # 1. Validate config
    missing = [k for k, v in {
        "JIRA_EMAIL": JIRA_EMAIL,
        "JIRA_API_TOKEN": JIRA_API_TOKEN,
        "SLACK_BOT_TOKEN": SLACK_TOKEN,
        "SLACK_CHANNEL_ID": SLACK_CHANNEL,
    }.items() if not v]
    if missing and not dry_run:
        raise EnvironmentError(f"Missing env vars: {', '.join(missing)}")

    # 2. Fetch data
    print("[1/3] Fetching active sprint issues from Jira...")
    issues, sprint_info = fetch_sprint_issues("active")
    print(f"      -> {len(issues)} issues in '{sprint_info.get('name', '?')}'")

    print("[2/3] Fetching last 3 closed sprints for cycle time baseline...")
    prev_sprints = fetch_last_n_sprints(n=3)
    print(f"      -> {len(prev_sprints)} closed sprints found")

    # 3. Calculate & build report
    print("[3/3] Calculating Sprint Health Score...")
    report = build_report(issues, sprint_info, prev_sprints)

    effective_site_url = (site_url or REPORT_SITE_URL).strip()
    effective_pdf_url = (pdf_url or REPORT_PDF_URL).strip()

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

    print(f"[done] Health Score: {report['health_score']}/100 - {report['health_label']}")


def run_scheduled() -> None:
    """Run every Friday at 09:00 Cairo time (UTC+2)."""
    import pytz
    cairo = pytz.timezone("Africa/Cairo")

    def job():
        try:
            run()
        except Exception as e:
            print(f"[error] {e}")

    schedule.every().friday.at("09:00").do(job)
    print("[scheduler] Running every Friday at 09:00 Cairo time. Press Ctrl+C to stop.")
    print("[scheduler] Next run:", schedule.next_run())

    while True:
        schedule.run_pending()
        time.sleep(60)


# ─── ENTRY POINT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sprint Health Score Reporter")
    parser.add_argument("--dry-run",  action="store_true", help="Print report without sending to Slack")
    parser.add_argument("--schedule", action="store_true", help="Run on schedule (every Friday 9AM Cairo)")
    parser.add_argument("--html", action="store_true", help="Export report as HTML file")
    parser.add_argument("--pdf", action="store_true", help="Export report as PDF file")
    parser.add_argument("--no-slack", action="store_true", help="Do not send report to Slack")
    parser.add_argument("--site-url", type=str, default="", help="Hosted report URL to include in Slack message")
    parser.add_argument("--pdf-url", type=str, default="", help="Hosted PDF URL to include in Slack message")
    parser.add_argument("--slack-link-only", action="store_true", help="Send hosted report link summary to Slack")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
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
