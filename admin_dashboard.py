import json
import logging
import os
import secrets
import signal
import threading
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from app import config as sprint_health
from app.auth.password import hash_password, verify_password
from app.auth.service import (
    authenticate as db_authenticate,
    create_user as db_create_user,
    delete_user as db_delete_user,
    get_user_by_email,
    list_users as db_list_users,
    log_audit_event,
)
from app.notifications import send_slack_message
from app.storage import init_schema

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)

# Use 0.0.0.0 for production (Railway, Heroku, etc), 127.0.0.1 for local
DEFAULT_HOST = "0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT") else "127.0.0.1"
HOST = os.getenv("ADMIN_DASHBOARD_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST

# Railway sets PORT env var, default to 8765 for local
PORT = int(os.getenv("PORT", os.getenv("ADMIN_DASHBOARD_PORT", "8765")))

SESSION_EXPIRY_DAYS = 7

# Resolve database path from settings
_settings = sprint_health.load_settings()
DB_PATH = _settings.sqlite_path
init_schema(DB_PATH)

# ---------------------------------------------------------------------------
# Session management (cookie-based sessions backed by in-memory store)
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()

# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------

_csrf_tokens: dict[str, str] = {}
_csrf_lock = threading.Lock()


def _generate_csrf_token(session_id: str) -> str:
    """Generate and store a CSRF token for a given session."""
    token = secrets.token_urlsafe(32)
    with _csrf_lock:
        _csrf_tokens[session_id] = token
    return token


def _validate_csrf_token(session_id: str, token: str) -> bool:
    """Validate a CSRF token against the stored value."""
    with _csrf_lock:
        expected = _csrf_tokens.get(session_id)
    return expected is not None and secrets.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _create_session(email: str, role: str) -> str:
    """Create an in-memory session and return the session ID."""
    session_id = str(uuid.uuid4())
    expiry = (datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRY_DAYS)).isoformat()
    with _sessions_lock:
        _sessions[session_id] = {"email": email, "role": role, "expires": expiry}
    return session_id


def _get_session_user_by_id(session_id: str | None) -> dict | None:
    """Look up session user.  Returns ``{'email': ..., 'role': ...}`` or None."""
    if not session_id:
        return None
    with _sessions_lock:
        session = _sessions.get(session_id)
    if not session:
        return None
    try:
        if datetime.fromisoformat(session["expires"]) < datetime.now(timezone.utc):
            _delete_session(session_id)
            return None
    except (ValueError, TypeError):
        return None
    return {"email": session["email"], "role": session["role"]}


def _delete_session(session_id: str) -> None:
    """Remove a session."""
    with _sessions_lock:
        _sessions.pop(session_id, None)
    with _csrf_lock:
        _csrf_tokens.pop(session_id, None)


# ---------------------------------------------------------------------------
# Form parsing helpers
# ---------------------------------------------------------------------------

def _float_value(params: dict, key: str) -> float:
    raw = (params.get(key, [""])[0] or "").strip()
    return float(raw) if raw else 0.0


def _int_value(params: dict, key: str) -> int:
    raw = (params.get(key, [""])[0] or "").strip()
    return int(float(raw)) if raw else 0


def _text_value(params: dict, key: str) -> str:
    return (params.get(key, [""])[0] or "").strip()


def _bool_value(params: dict, key: str) -> bool:
    return (params.get(key, [""])[0] or "").strip().lower() in {"1", "true", "on", "yes"}


def _list_values(params: dict, key: str) -> list[str]:
    raw = (params.get(key, [""])[0] or "").strip()
    if not raw: return []
    return [i.strip() for i in raw.replace(",", "\n").splitlines() if i.strip()]


def _field_input(label: str, name: str, value, step: str = "1", input_type: str = "number", hint: str = "") -> str:
    hint_html = f"<small>{escape(hint)}</small>" if hint else ""
    return (
        f"<label class='field'>"
        f"<span>{escape(label)}</span>"
        f"<input type='{escape(input_type)}' name='{escape(name)}' step='{escape(step)}' value='{escape(str(value))}' required>"
        f"{hint_html}"
        f"</label>"
    )


def _field_textarea(label: str, name: str, value: str, hint: str = "", rows: int = 3) -> str:
    hint_html = f"<small>{escape(hint)}</small>" if hint else ""
    return (
        f"<label class='field'>"
        f"<span>{escape(label)}</span>"
        f"<textarea name='{escape(name)}' rows='{rows}' required>{escape(value)}</textarea>"
        f"{hint_html}"
        f"</label>"
    )


def _field_checkbox(label: str, name: str, checked: bool, hint: str = "") -> str:
    hint_html = f"<small>{escape(hint)}</small>" if hint else ""
    return (
        f"<label class='field'>"
        f"<span>{escape(label)}</span>"
        f"<div><input type='checkbox' name='{escape(name)}' value='1' {'checked' if checked else ''} style='width:auto'> {hint_html}</div>"
        f"</label>"
    )


def _section(title: str, description: str, fields: list[str], compact: bool = False) -> str:
    grid_class = "grid compact-grid" if compact else "grid"
    return (
        f"<section class='card'>"
        f"<div class='section-head'><h3>{escape(title)}</h3><p>{escape(description)}</p></div>"
        f"<div class='{grid_class}'>{''.join(fields)}</div>"
        f"</section>"
    )


def _build_config_from_form(params: dict) -> dict:
    c = sprint_health.load_metrics_config()
    conf = sprint_health._deep_copy_config(c)

    # Weights
    conf["weights"]["commitment"] = _float_value(params, "w_commit")
    conf["weights"]["carryover"] = _float_value(params, "w_carry")
    conf["weights"]["cycle_time"] = _float_value(params, "w_cycle")
    conf["weights"]["bug_ratio"] = _float_value(params, "w_bug")

    # Points
    conf["points"]["excellent"] = _int_value(params, "p_exc")
    conf["points"]["good"] = _int_value(params, "p_good")
    conf["points"]["warning"] = _int_value(params, "p_warn")
    conf["points"]["poor"] = _int_value(params, "p_poor")
    conf["points"]["neutral"] = _int_value(params, "p_neut")

    # Metrics
    conf["commitment"]["ideal_min_pct"] = _float_value(params, "c_imin")
    conf["commitment"]["ideal_max_pct"] = _float_value(params, "c_imax")
    conf["commitment"]["good_min_pct"] = _float_value(params, "c_gmin")
    conf["commitment"]["warning_min_pct"] = _float_value(params, "c_wmin")
    conf["commitment"]["extended_cap_score"] = _int_value(params, "c_cap")

    conf["carryover"]["excellent_lt_pct"] = _float_value(params, "co_exc")
    conf["carryover"]["good_lte_pct"] = _float_value(params, "co_good")
    conf["carryover"]["warning_lte_pct"] = _float_value(params, "co_warn")
    conf["carryover"]["extended_penalty"] = _int_value(params, "co_pen")

    conf["cycle_time"]["stable_abs_pct"] = _float_value(params, "ct_st")
    conf["cycle_time"]["good_increase_pct"] = _float_value(params, "ct_gi")
    conf["cycle_time"]["warning_increase_pct"] = _float_value(params, "ct_wi")

    conf["bug_ratio"]["excellent_lt_pct"] = _float_value(params, "br_exc")
    conf["bug_ratio"]["good_lte_pct"] = _float_value(params, "br_good")
    conf["bug_ratio"]["warning_lte_pct"] = _float_value(params, "br_warn")

    conf["burndown"]["done_bonus"] = _int_value(params, "bd_db")
    conf["burndown"]["on_track_bonus"] = _int_value(params, "bd_ot")
    conf["burndown"]["behind_small_max"] = _int_value(params, "bd_bsm")
    conf["burndown"]["behind_medium_max"] = _int_value(params, "bd_bmm")
    conf["burndown"]["behind_medium_penalty"] = _int_value(params, "bd_bmp")
    conf["burndown"]["behind_large_penalty"] = _int_value(params, "bd_blp")

    st = conf["stale_thresholds"]
    st["bug_days"] = _int_value(params, "st_bug")
    st["subtask_days"] = _int_value(params, "st_sub")
    st["story_no_points_days"] = _int_value(params, "st_snp")
    st["story_small_max_points"] = _float_value(params, "st_ssm")
    st["story_small_days"] = _int_value(params, "st_ss")
    st["story_medium_max_points"] = _float_value(params, "st_smm")
    st["story_medium_days"] = _int_value(params, "st_sm")
    st["story_large_days"] = _int_value(params, "st_sl")
    st["default_days"] = _int_value(params, "st_def")

    conf["labels"]["green_min_score"] = _int_value(params, "l_g")
    conf["labels"]["yellow_min_score"] = _int_value(params, "l_y")
    conf["labels"]["orange_min_score"] = _int_value(params, "l_o")

    fs = conf["final_score"]
    fs["custom_formula"] = _text_value(params, "fs_f")
    fs["round_result"] = _bool_value(params, "fs_r")
    fs["min_score"] = _int_value(params, "fs_min")
    fs["max_score"] = _int_value(params, "fs_max")

    conf["activity_people"]["qa_names"] = _list_values(params, "ap_qa")
    conf["activity_people"]["developer_names"] = _list_values(params, "ap_dev")
    conf["activity_thresholds"]["bugs_today_warning"] = _int_value(params, "at_bugwarn")
    conf["activity_thresholds"]["low_completed_tasks"] = _int_value(params, "at_lowcomp")

    conf["jira"]["base_url"] = _text_value(params, "j_url")
    conf["jira"]["project_key"] = _text_value(params, "j_proj")
    conf["jira"]["board_id"] = _int_value(params, "j_board")

    conf["branding"]["company_name"] = _text_value(params, "b_name")
    conf["branding"]["report_title"] = _text_value(params, "b_title")
    conf["branding"]["logo_path"] = _text_value(params, "b_logo")

    conf["ui"]["particle_density"] = _int_value(params, "u_pd")
    conf["ui"]["theme_color"] = _text_value(params, "u_tc")

    return conf


def _build_sections(config: dict) -> list[str]:
    w, p, c, co, ct, br, bd, st, l, fs, ap, at, j, b, u = (
        config["weights"], config["points"], config["commitment"], config["carryover"],
        config["cycle_time"], config["bug_ratio"], config["burndown"], config["stale_thresholds"],
        config["labels"], config["final_score"], config["activity_people"], config["activity_thresholds"], config["jira"],
        config["branding"], config["ui"]
    )
    return [
        _section("Identity & Branding", "Header information for the report.", [
            _field_input("Company Name", "b_name", b["company_name"], input_type="text"),
            _field_input("Report Title", "b_title", b["report_title"], input_type="text"),
            _field_input("Logo Filename/URL", "b_logo", b["logo_path"], input_type="text"),
        ], compact=True),
        _section("Health Bands", "Score thresholds for color labels.", [
            _field_input("Green Min", "l_g", l["green_min_score"]),
            _field_input("Yellow Min", "l_y", l["yellow_min_score"]),
            _field_input("Orange Min", "l_o", l["orange_min_score"]),
        ], compact=True),
        _section("Commitment Metric", "Thresholds for sprint scope completion.", [
            _field_input("Ideal Min %", "c_imin", c["ideal_min_pct"], "0.1"),
            _field_input("Ideal Max %", "c_imax", c["ideal_max_pct"], "0.1"),
            _field_input("Good Min %", "c_gmin", c["good_min_pct"], "0.1"),
            _field_input("Warning Min %", "c_wmin", c["warning_min_pct"], "0.1"),
            _field_input("Cap Score", "c_cap", c["extended_cap_score"]),
        ]),
        _section("Carryover Metric", "Penalties for unfinished work.", [
            _field_input("Excellent < %", "co_exc", co["excellent_lt_pct"], "0.1"),
            _field_input("Good <= %", "co_good", co["good_lte_pct"], "0.1"),
            _field_input("Warning <= %", "co_warn", co["warning_lte_pct"], "0.1"),
            _field_input("Penalty", "co_pen", co["extended_penalty"]),
        ]),
        _section("Cycle Time Metric", "History-based comparisons.", [
            _field_input("Stable Abs %", "ct_st", ct["stable_abs_pct"], "0.1"),
            _field_input("Good Incr %", "ct_gi", ct["good_increase_pct"], "0.1"),
            _field_input("Warn Incr %", "ct_wi", ct["warning_increase_pct"], "0.1"),
        ], compact=True),
        _section("Bug Ratio Metric", "Newly created bugs ratio.", [
            _field_input("Exc < %", "br_exc", br["excellent_lt_pct"], "0.1"),
            _field_input("Good <= %", "br_good", br["good_lte_pct"], "0.1"),
            _field_input("Warn <= %", "br_warn", br["warning_lte_pct"], "0.1"),
        ], compact=True),
        _section("Scoring Logic", "Base points and weights.", [
            _field_input("P: Excellent", "p_exc", p["excellent"]), _field_input("P: Good", "p_good", p["good"]),
            _field_input("P: Warning", "p_warn", p["warning"]), _field_input("P: Poor", "p_poor", p["poor"]),
            _field_input("W: Commitment", "w_commit", w["commitment"], "0.01"), _field_input("W: Carryover", "w_carry", w["carryover"], "0.01"),
            _field_input("W: Cycle Time", "w_cycle", w["cycle_time"], "0.01"), _field_input("W: Bug Ratio", "w_bug", w["bug_ratio"], "0.01"),
        ]),
        _section("Final Score Formula", "Combine signals into one number.", [
            _field_textarea("Formula", "fs_f", fs["custom_formula"], rows=2),
            _field_checkbox("Round", "fs_r", fs["round_result"], "Round result"),
            _field_input("Min", "fs_min", fs["min_score"]), _field_input("Max", "fs_max", fs["max_score"]),
        ]),
        _section("Burndown Nudge", "Score adjustments.", [
            _field_input("Done Bonus", "bd_db", bd["done_bonus"]), _field_input("OnTrack Bonus", "bd_ot", bd["on_track_bonus"]),
            _field_input("Small Max", "bd_bsm", bd["behind_small_max"]), _field_input("Med Max", "bd_bmm", bd["behind_medium_max"]),
            _field_input("Med Penalty", "bd_bmp", bd["behind_medium_penalty"]), _field_input("Large Penalty", "bd_blp", bd["behind_large_penalty"]),
        ]),
        _section("Stale Issue Days", "Inactivity thresholds.", [
            _field_input("Bug Days", "st_bug", st["bug_days"]), _field_input("Subtask", "st_sub", st["subtask_days"]),
            _field_input("Story(0pt)", "st_snp", st["story_no_points_days"]), _field_input("Story(Large)", "st_sl", st["story_large_days"]),
        ], compact=True),
        _section("Jira & UI", "System and visual settings.", [
            _field_input("Jira URL", "j_url", j["base_url"], input_type="text"),
            _field_input("Project", "j_proj", j["project_key"], input_type="text"),
            _field_input("Board ID", "j_board", j["board_id"]),
            _field_input("Density", "u_pd", u["particle_density"]),
        ]),
        _section("Activity Filters", "Who appears in activity logs.", [
            _field_textarea("QA Names", "ap_qa", "\n".join(ap["qa_names"])),
            _field_textarea("Dev Names", "ap_dev", "\n".join(ap["developer_names"])),
        ], compact=True),
        _section("Activity Thresholds", "Controls alerts and insight sensitivity.", [
            _field_input("Bug Warning", "at_bugwarn", at["bugs_today_warning"]),
            _field_input("Low Completion", "at_lowcomp", at["low_completed_tasks"]),
        ], compact=True),
    ]


def _layout_html(content: str, title: str = "Admin Control Center", user_role: str = "viewer", active_path: str = "/admin") -> str:
    config = sprint_health.load_metrics_config()
    admin_only = 'style="display:none"' if user_role == "viewer" else ""
    user_mgmt_only = 'style="display:none"' if user_role != "admin" else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    :root {{
      --bg-page:      #060D1F;
      --glass-bg:     rgba(255,255,255,0.04);
      --glass-border: rgba(255,255,255,0.09);
      --glass-blur:   14px;
      --glass-shadow: 0 8px 32px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.06);
      --brand-primary: #3B82F6;
      --brand-hover:   #2563EB;
      --brand-soft:    rgba(59,130,246,0.14);
      --green:         #22C55E;
      --green-soft:    rgba(34,197,94,0.13);
      --yellow:        #FACC15;
      --yellow-soft:   rgba(250,204,21,0.13);
      --orange:        #FB923C;
      --orange-soft:   rgba(251,146,60,0.13);
      --red:           #EF4444;
      --red-soft:      rgba(239,68,68,0.13);
      --teal:          #14B8A6;
      --text-primary:   #F1F5F9;
      --text-secondary: #94A3B8;
      --text-muted:     #4E6080;
      --border:         rgba(148,163,184,0.09);
      --border-hover:   rgba(148,163,184,0.20);
      --border-focus:   rgba(59,130,246,0.50);
      --radius-sm:   6px;
      --radius-md:   10px;
      --radius-lg:   16px;
      --radius-xl:   22px;
      --radius-full: 999px;
      --shadow-card: 0 4px 24px rgba(0,0,0,0.28);
      --glow-blue:   0 0 24px rgba(59,130,246,0.22);
      --glow-green:  0 0 24px rgba(34,197,94,0.22);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
      background:
        radial-gradient(ellipse 70% 55% at 10% -5%, rgba(59,130,246,0.18) 0%, transparent 55%),
        radial-gradient(ellipse 55% 40% at 90% 5%,  rgba(20,184,166,0.14) 0%, transparent 45%),
        radial-gradient(ellipse 80% 50% at 50% 100%, rgba(99,102,241,0.08) 0%, transparent 60%),
        linear-gradient(170deg, #0C1428 0%, #060D1F 40%, #020817 100%);
      color: var(--text-primary);
      min-height: 100vh;
      display: flex;
      overflow-x: hidden;
    }}
    #admin-particles {{ position: fixed; inset: 0; z-index: 1; pointer-events: none; opacity: 0.38; }}
    .app-container {{ display: flex; width: 100%; z-index: 2; position: relative; }}
    .sidebar {{
      width: 240px;
      background: rgba(10,17,35,0.72);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-right: 1px solid var(--glass-border);
      box-shadow: 4px 0 24px rgba(0,0,0,0.30);
      height: 100vh;
      position: sticky;
      top: 0;
      padding: 32px 20px;
      display: flex;
      flex-direction: column;
      gap: 28px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
    }}
    .brand-mark {{
      width: 36px;
      height: 36px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--brand-primary), #14B8A6);
      box-shadow: 0 4px 16px rgba(59,130,246,0.40);
      flex-shrink: 0;
    }}
    .brand-name {{
      font-size: 18px;
      font-weight: 700;
      color: var(--text-primary);
      letter-spacing: -0.02em;
    }}
    .main-content {{
      flex: 1;
      padding: 32px 40px;
      max-width: 1100px;
      margin: 0 auto;
      width: 100%;
    }}
    .nav-label {{
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.10em;
      color: var(--text-muted);
      padding: 0 4px;
      margin-bottom: 4px;
    }}
    .nav-divider {{ height: 1px; background: var(--glass-border); margin: 10px 0; }}
    .nav-list {{ list-style: none; display: flex; flex-direction: column; gap: 2px; }}
    .nav-item a {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 9px 12px;
      border-radius: var(--radius-md);
      color: var(--text-muted);
      text-decoration: none;
      font-size: 13px;
      font-weight: 500;
      border-left: 2px solid transparent;
      transition: all 160ms cubic-bezier(0.16,1,0.3,1);
    }}
    .nav-item a:hover {{
      background: var(--glass-bg);
      color: var(--text-primary);
      border-left-color: rgba(148,163,184,0.20);
    }}
    .nav-item.active a {{
      background: var(--brand-soft);
      color: var(--brand-primary);
      border-left-color: var(--brand-primary);
    }}
    .nav-item a svg {{ flex-shrink: 0; opacity: 0.65; transition: opacity 160ms ease; }}
    .nav-item a:hover svg, .nav-item.active a svg {{ opacity: 1; }}
    .logout-link a {{ color: var(--red) !important; }}
    .logout-link a:hover {{ background: var(--red-soft) !important; border-left-color: var(--red) !important; }}
    .header {{
      margin-bottom: 28px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--glass-border);
    }}
    .header h1 {{
      font-size: 20px;
      font-weight: 700;
      color: var(--text-primary);
      letter-spacing: -0.02em;
    }}
    .header .header-eyebrow {{
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.10em;
      color: var(--text-muted);
      margin-bottom: 5px;
    }}
    .header p {{
      margin-top: 3px;
      font-size: 13px;
      color: var(--text-muted);
    }}
    .actions-bar {{
      display: flex;
      gap: 10px;
      margin-top: 28px;
      padding-top: 20px;
      border-top: 1px solid var(--glass-border);
      flex-wrap: wrap;
    }}
    section {{
      background: var(--glass-bg);
      backdrop-filter: blur(var(--glass-blur));
      -webkit-backdrop-filter: blur(var(--glass-blur));
      border: 1px solid var(--glass-border);
      border-radius: var(--radius-xl);
      padding: 24px 28px;
      box-shadow: var(--glass-shadow);
      margin-bottom: 20px;
      position: relative;
      overflow: hidden;
      transition: border-color 160ms ease, box-shadow 160ms ease;
    }}
    section::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.10), transparent);
      pointer-events: none;
    }}
    section:hover {{
      border-color: rgba(59,130,246,0.18);
      box-shadow: var(--glow-blue), var(--glass-shadow);
    }}
    .section-head {{ margin-bottom: 20px; }}
    .section-head h3 {{
      font-size: 11px;
      font-weight: 700;
      color: var(--text-primary);
      border-left: 2px solid var(--brand-primary);
      padding-left: 12px;
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.07em;
    }}
    .section-head p {{
      font-size: 12px;
      color: var(--text-muted);
      padding-left: 14px;
    }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }}
    .compact-grid {{ grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }}
    .field {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      padding: 12px 14px;
      background: rgba(255,255,255,0.03);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      border-radius: var(--radius-md);
      border: 1px solid var(--glass-border);
      transition: border-color 160ms ease, box-shadow 160ms ease;
    }}
    .field:focus-within {{
      border-color: var(--border-focus);
      box-shadow: 0 0 0 3px rgba(59,130,246,0.12);
    }}
    .field span {{
      font-size: 10px;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.07em;
    }}
    .field small {{
      color: var(--text-muted);
      font-size: 11px;
    }}
    input, select, textarea {{
      background: transparent;
      border: none;
      color: var(--text-primary);
      font-size: 14px;
      font-weight: 500;
      width: 100%;
      outline: none;
      font-family: inherit;
    }}
    button, .btn {{
      padding: 11px 24px;
      border-radius: var(--radius-md);
      font-weight: 600;
      cursor: pointer;
      border: none;
      transition: all 180ms cubic-bezier(0.16,1,0.3,1);
      text-decoration: none;
      display: inline-block;
      font-size: 14px;
      font-family: inherit;
    }}
    button:focus, .btn:focus, input:focus, select:focus, textarea:focus {{
      outline: none;
      box-shadow: 0 0 0 3px rgba(59,130,246,0.35) !important;
    }}
    .save {{
      background: linear-gradient(135deg, #3B82F6, #2563EB);
      color: #fff;
      border: 1px solid rgba(59,130,246,0.50);
      box-shadow: 0 4px 20px rgba(59,130,246,0.35);
    }}
    .save:hover {{
      background: linear-gradient(135deg, #4F94F8, #3B82F6);
      box-shadow: 0 6px 28px rgba(59,130,246,0.50);
      transform: translateY(-2px);
    }}
    .reset {{
      background: var(--glass-bg);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      color: var(--text-secondary);
      border: 1px solid var(--glass-border);
    }}
    .reset:hover {{
      background: rgba(255,255,255,0.07);
      transform: translateY(-2px);
      border-color: var(--border-hover);
    }}
    .banner {{
      padding: 12px 16px;
      margin-bottom: 24px;
      border-left: 3px solid transparent;
      border-radius: 0 var(--radius-md) var(--radius-md) 0;
      font-size: 13px;
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
    }}
    .banner.ok {{ background: var(--green-soft); border-left-color: var(--green); color: #4ADE80; }}
    .banner.error {{ background: var(--red-soft); border-left-color: var(--red); color: #F87171; }}
    .table-wrap {{
      background: var(--glass-bg);
      backdrop-filter: blur(var(--glass-blur));
      -webkit-backdrop-filter: blur(var(--glass-blur));
      border: 1px solid var(--glass-border);
      border-radius: var(--radius-xl);
      overflow: hidden;
      box-shadow: var(--glass-shadow);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th {{
      background: rgba(255,255,255,0.025);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.09em;
      color: var(--text-muted);
      padding: 11px 20px;
      text-align: left;
      border-bottom: 1px solid var(--glass-border);
      white-space: nowrap;
    }}
    td {{
      padding: 13px 20px;
      font-size: 13px;
      line-height: 1.45;
      color: var(--text-secondary);
      border-bottom: 1px solid rgba(148,163,184,0.06);
      vertical-align: middle;
    }}
    tbody tr:hover td {{ background: rgba(255,255,255,0.025); }}
    tbody tr:last-child td {{ border-bottom: none; }}
    .email-cell {{ font-weight: 500; color: var(--text-primary); font-size: 13px; }}
    .email-cell {{ font-weight: 500; color: var(--text-primary); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: fit-content;
      padding: 3px 8px;
      border-radius: var(--radius-full);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
    }}
    .status-active {{ background: var(--green-soft); color: var(--green); border: 1px solid rgba(34,197,94,0.22); }}
    .status-locked {{ background: var(--red-soft);   color: var(--red);   border: 1px solid rgba(239,68,68,0.22); }}
    .role-super_admin {{ background: rgba(49,46,129,0.70);  color: #A5B4FC; border: 1px solid rgba(165,180,252,0.20); }}
    .role-admin        {{ background: rgba(30,58,95,0.70);   color: #93C5FD; border: 1px solid rgba(147,197,253,0.20); }}
    .role-editor       {{ background: rgba(6,78,59,0.70);    color: #6EE7B7; border: 1px solid rgba(110,231,183,0.20); }}
    .role-user         {{ background: rgba(31,41,55,0.70);   color: #9CA3AF; border: 1px solid rgba(156,163,175,0.15); }}
    .role-viewer       {{ background: rgba(31,41,55,0.55);   color: #6B7280; border: 1px solid rgba(107,114,128,0.15); }}
    .action-card {{
      background: var(--glass-bg);
      backdrop-filter: blur(var(--glass-blur));
      -webkit-backdrop-filter: blur(var(--glass-blur));
      border: 1px solid var(--glass-border);
      border-radius: var(--radius-xl);
      padding: 20px 24px;
      margin-bottom: 12px;
      box-shadow: var(--glass-shadow);
      position: relative;
      overflow: hidden;
      transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
    }}
    .action-card::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.10), transparent);
      pointer-events: none;
    }}
    .action-card:hover {{
      transform: translateY(-2px);
      border-color: rgba(59,130,246,0.22);
      box-shadow: var(--glow-blue), var(--glass-shadow);
    }}
    .action-summary {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }}
    .avatar {{
      width: 36px;
      height: 36px;
      border-radius: 50%;
      background: var(--brand-soft);
      border: 1px solid rgba(59,130,246,0.30);
      color: var(--brand-primary);
      font-size: 13px;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 0 12px rgba(59,130,246,0.18);
      flex-shrink: 0;
    }}
    .action-row {{
      display: grid;
      grid-template-columns: 2fr 1fr 1fr 1fr;
      gap: 12px;
      align-items: end;
    }}
    .ghost-btn {{
      background: var(--glass-bg);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      border: 1px solid var(--glass-border);
      color: var(--text-primary);
    }}
    .ghost-btn:hover {{ background: rgba(255,255,255,0.07); transform: translateY(-1px); }}
    .ghost-green:hover {{ color: var(--green);        border-color: rgba(34,197,94,0.30); }}
    .ghost-yellow:hover {{ color: var(--yellow);      border-color: rgba(250,204,21,0.30); }}
    .ghost-red:hover {{ color: var(--red); background: var(--red-soft); border-color: rgba(239,68,68,0.30); }}
    .ghost-blue:hover {{ color: var(--brand-primary); border-color: rgba(59,130,246,0.30); }}
    @media (max-width: 1279px) {{
      .sidebar {{
        width: 76px;
        padding: 28px 14px;
      }}
      .brand-name, .sidebar .nav-item a {{
        font-size: 0;
      }}
      .sidebar .nav-item a::before {{
        font-size: 14px;
        content: attr(data-label);
        color: inherit;
      }}
    }}
    @media (max-width: 768px) {{
      .sidebar {{ display: none; }}
      .main-content {{ padding: 20px; }}
      .action-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="app-container">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true"></div>
        <div class="brand-name">Lumofy</div>
      </div>
      <nav>
        <div class="nav-label">Workspace</div>
        <ul class="nav-list">
          <li class="nav-item {'active' if active_path == '/' else ''}">
            <a href="/">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M2 8L8 2L14 8V14H10V10H6V14H2V8Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              </svg>
              Sprint Metrics
            </a>
          </li>
        </ul>
        <div class="nav-divider" {admin_only}></div>
        <div class="nav-label" {admin_only}>Admin</div>
        <ul class="nav-list">
          <li class="nav-item {'active' if active_path == '/admin' else ''}" {admin_only}>
            <a href="/admin">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <circle cx="8" cy="8" r="2.5" stroke="currentColor" stroke-width="1.5"/>
                <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
              Settings
            </a>
          </li>
          <li class="nav-item {'active' if active_path == '/users' else ''}" {user_mgmt_only}>
            <a href="/users">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <circle cx="6" cy="5" r="2.5" stroke="currentColor" stroke-width="1.5"/>
                <path d="M1 14c0-2.761 2.239-4 5-4s5 1.239 5 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                <path d="M11.5 7c1.1 0 2 .9 2 2M13.5 14c0-1.657-.9-3-2-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
              User Management
            </a>
          </li>
        </ul>
        <div class="nav-divider"></div>
        <ul class="nav-list">
          <li class="nav-item logout-link">
            <a href="/logout">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M6 14H2V2h4M10 11l3-3-3-3M13 8H6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              Sign out
            </a>
          </li>
        </ul>
      </nav>
    </aside>
    <main class="main-content">
      {content}
    </main>
  </div>
  <canvas id="admin-particles"></canvas>
  <script>
    (() => {{
      const storageKey = 'sprint-health-theme';
      const body = document.body;
      const applyTheme = (t) => {{ body.dataset.theme = t; localStorage.setItem(storageKey, t); }};
      applyTheme(localStorage.getItem(storageKey) || 'dark');
      window.addEventListener('storage', (e) => {{ if (e.key === storageKey) body.dataset.theme = e.newValue; }});
      
      const canvas = document.getElementById('admin-particles');
      const ctx = canvas.getContext('2d');
      let w, h; const particles = [];
      const resize = () => {{ w = canvas.width = window.innerWidth; h = canvas.height = window.innerHeight; }};
      window.addEventListener('resize', resize); resize();
      class P {{
        constructor() {{ this.reset(); }}
        reset() {{ this.x = Math.random()*w; this.y = Math.random()*h; this.v = 0.2+Math.random()*0.5; this.s = 1+Math.random()*2; this.a = 0.1+Math.random()*0.4; }}
        draw() {{
          this.y -= this.v; if (this.y < -10) this.y = h + 10;
          ctx.beginPath(); ctx.arc(this.x, this.y, this.s, 0, Math.PI*2);
          ctx.fill();
        }}
      }}
      ctx.fillStyle = 'rgba(59,130,246,0.18)';
      for(let i=0; i<{config.get('ui',{}).get('particle_density',600)}; i++) particles.push(new P());
      const animate = () => {{ ctx.clearRect(0,0,w,h); particles.forEach(p => p.draw()); requestAnimationFrame(animate); }};
      animate();
    }})();
  </script>
</body>
</html>"""


def _dashboard_html(user, message: str = "", error: str = "") -> str:
    config = sprint_health.load_metrics_config()
    saved_banner = f"<div class='banner ok'>{escape(message)}</div>" if message else ""
    error_banner = f"<div class='banner error'>{escape(error)}</div>" if error else ""
    sections = _build_sections(config)

    content = f"""
      <header class="header">
        <div class="header-eyebrow">Administration</div>
        <h1>Settings</h1>
        <p>Edit scoring thresholds, weights, and presentation controls.</p>
      </header>
      {saved_banner}{error_banner}
      <form method="post" action="/save">
        {''.join(sections)}
        <div class="actions-bar">
          <button class="save" type="submit">Save changes</button>
          <button class="reset" type="submit" formaction="/reset">Reset defaults</button>
        </div>
      </form>
    """
    return _layout_html(content, user_role=user["role"], active_path="/admin")


def _users_html(user, message: str = "", error: str = "") -> str:
    users_data = db_list_users(DB_PATH)
    banner = f"<div class='banner ok'>{escape(message)}</div>" if message else ""
    err_banner = f"<div class='banner error'>{escape(error)}</div>" if error else ""

    rows = ""
    action_cards = ""
    for u_row in users_data:
        role = str(u_row.get("role", "viewer"))
        attempts = int(u_row.get("failed_attempts", 0) or 0)
        locked = bool(u_row.get("locked_until"))
        attempts_color = "var(--text-muted)" if attempts == 0 else ("var(--yellow)" if attempts < 3 else "var(--red)")
        initials = "".join(part[:1].upper() for part in u_row["email"].split("@")[0].replace(".", " ").split()[:2]) or "U"
        rows += f"""
          <tr>
            <td class="email-cell">{escape(u_row['email'])}</td>
            <td><span class="pill role-{escape(role)}">{escape(role.replace('_', ' '))}</span></td>
            <td>{escape(str(u_row.get('last_login_at') or 'Never'))}</td>
            <td style="color:{attempts_color};">{attempts}</td>
            <td><span class="pill {'status-locked' if locked else 'status-active'}">{'Locked' if locked else 'Active'}</span></td>
          </tr>
        """
        action_cards += f"""
          <section class="action-card">
            <div class="action-summary">
              <div style="display:flex; align-items:center; gap:12px;">
                <span class="avatar">{escape(initials[:2])}</span>
                <div>
                  <div style="font-size:14px; font-weight:600; color:var(--text-primary);">{escape(u_row['email'])}</div>
                  <div style="font-size:13px; color:var(--text-muted);">Access profile</div>
                </div>
              </div>
              <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <span class="pill role-{escape(role)}">{escape(role.replace('_', ' '))}</span>
                <span class="pill {'status-locked' if locked else 'status-active'}">{'Locked' if locked else 'Active'}</span>
              </div>
            </div>
            <div class="action-row">
              <label class="field">
                <span>Account</span>
                <input type="text" value="{escape(u_row['email'])}" disabled>
              </label>
              <label class="field">
                <span>Role</span>
                <input type="text" value="{escape(role.replace('_', ' ').title())}" disabled>
              </label>
              <label class="field">
                <span>Status</span>
                <input type="text" value="{'Locked' if locked else 'Active'}" disabled>
              </label>
              <form method="post" action="/users/delete" style="margin:0;">
                <input type="hidden" name="username" value="{escape(u_row['email'])}">
                <button type="submit" class="ghost-btn ghost-red" style="width:100%;">Delete</button>
              </form>
            </div>
          </section>
        """

    content = f"""
      <header class="header">
        <div class="header-eyebrow">Administration</div>
        <h1>User Management</h1>
        <p>Manage access levels, account states, and provision new users.</p>
      </header>
      {banner}{err_banner}
      <section>
        <div class="section-head"><h3>Create Account</h3><p>Add a new user with the correct role and access level.</p></div>
        <form method="post" action="/users/add" class="grid" style="grid-template-columns:1fr 1fr 1fr">
          <div class="field"><span>Email</span><input type="text" name="new_username" required autocomplete="off"></div>
          <div class="field"><span>Password</span><input type="password" name="new_password" required autocomplete="new-password"></div>
          <div class="field"><span>Role</span><select name="new_role"><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="admin">Admin</option></select></div>
          <div style="grid-column:1/-1"><button class="save" type="submit" style="width:100%">Create user</button></div>
        </form>
      </section>
      <section>
        <div class="section-head"><h3>All Accounts</h3><p>Account summary with role, status, and failed login visibility.</p></div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Email</th><th>Role</th><th>Last Login</th><th>Failed Attempts</th><th>Status</th></tr>
            </thead>
            <tbody>{rows if rows else '<tr><td colspan="5" style="color:var(--text-muted);padding:20px;">No accounts found.</td></tr>'}</tbody>
          </table>
        </div>
      </section>
      <section>
        <div class="section-head"><h3>User Actions</h3><p>Manage roles, lock state, and account removal.</p></div>
        {action_cards if action_cards else '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">No accounts to manage.</div>'}
      </section>
    """
    return _layout_html(content, user_role=user["role"], active_path="/users")


def _login_html(error: str = "") -> str:
    error_banner = f"<div class='error'>{escape(error)}</div>" if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sprint Health — Sign In</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    :root {{
      --glass-bg:     rgba(255,255,255,0.04);
      --glass-border: rgba(255,255,255,0.09);
      --glass-blur:   14px;
      --glass-shadow: 0 8px 32px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.06);
      --brand-primary: #3B82F6;
      --brand-hover:   #2563EB;
      --teal:          #14B8A6;
      --text-primary:   #F1F5F9;
      --text-secondary: #94A3B8;
      --text-muted:     #4E6080;
      --border-focus:   rgba(59,130,246,0.50);
      --red:      #EF4444;
      --red-soft: rgba(239,68,68,0.13);
      --radius-md:   10px;
      --radius-xl:   22px;
      --radius-full: 999px;
      --glow-blue:   0 0 24px rgba(59,130,246,0.22);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
      min-height: 100vh;
      display: grid;
      grid-template-columns: 1.3fr 0.9fr;
      background:
        radial-gradient(ellipse 70% 55% at 10% -5%, rgba(59,130,246,0.18) 0%, transparent 55%),
        radial-gradient(ellipse 55% 40% at 90% 5%,  rgba(20,184,166,0.14) 0%, transparent 45%),
        radial-gradient(ellipse 80% 50% at 50% 100%, rgba(99,102,241,0.08) 0%, transparent 60%),
        linear-gradient(170deg, #0C1428 0%, #060D1F 40%, #020817 100%);
      color: var(--text-primary);
    }}
    .visual {{
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 48px;
    }}
    .visual-card {{
      width: 100%;
      min-height: 560px;
      border-radius: 28px;
      background:
        radial-gradient(circle at 30% 20%, rgba(59,130,246,0.22), transparent 40%),
        radial-gradient(circle at 70% 75%, rgba(20,184,166,0.18), transparent 35%),
        var(--glass-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--glass-border);
      box-shadow: var(--glass-shadow), var(--glow-blue);
      padding: 48px;
      position: relative;
      overflow: hidden;
    }}
    .visual-card::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent);
      pointer-events: none;
    }}
    .eyebrow {{
      font-size: 11px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
      margin-bottom: 12px;
    }}
    .visual-title {{
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.02em;
      max-width: 520px;
      line-height: 1.25;
      margin-bottom: 12px;
    }}
    .visual-sub {{
      font-size: 14px;
      line-height: 1.6;
      color: var(--text-secondary);
      max-width: 420px;
    }}
    .arc-wrap {{
      margin-top: 48px;
      display: flex;
      align-items: center;
      gap: 32px;
      flex-wrap: wrap;
    }}
    .arc {{
      width: 200px;
      height: 200px;
      border-radius: 50%;
      border: 14px solid rgba(59,130,246,0.10);
      border-top-color: var(--brand-primary);
      border-right-color: var(--teal);
      position: relative;
      box-shadow: 0 0 32px rgba(59,130,246,0.24);
      animation: spinArc 8s linear infinite;
      flex-shrink: 0;
    }}
    .arc::after {{
      content: "";
      position: absolute;
      inset: 24px;
      border-radius: 50%;
      border: 1px dashed rgba(148,163,184,0.22);
      animation: pulseRing 3s ease-in-out infinite;
    }}
    .arc-label {{ flex: 1; min-width: 160px; }}
    .arc-label .metric-eyebrow {{
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .arc-label .metric-big {{
      font-size: 32px;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: #3B82F6;
      line-height: 1;
      margin-bottom: 6px;
    }}
    .arc-label .metric-sub {{
      font-size: 13px;
      color: var(--text-secondary);
      line-height: 1.5;
    }}
    .panel {{
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 40px 16px;
    }}
    .box {{
      background: var(--glass-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--glass-border);
      box-shadow: var(--glass-shadow);
      padding: 40px;
      border-radius: var(--radius-xl);
      width: 400px;
      position: relative;
      overflow: hidden;
    }}
    .box::before {{
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.14), transparent);
      pointer-events: none;
    }}
    .login-title {{ font-size: 22px; font-weight: 700; letter-spacing: -0.02em; margin: 8px 0 6px; }}
    .login-sub {{ font-size: 14px; line-height: 1.6; color: var(--text-secondary); margin-bottom: 24px; }}
    .divider {{ height: 1px; background: var(--glass-border); margin: 20px 0 24px; }}
    label {{
      display: block;
      font-size: 11px;
      font-weight: 700;
      color: var(--text-secondary);
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    input {{
      width: 100%;
      padding: 11px 14px;
      margin-bottom: 14px;
      border-radius: var(--radius-md);
      background: rgba(255,255,255,0.03);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      border: 1px solid var(--glass-border);
      color: var(--text-primary);
      box-sizing: border-box;
      font-size: 14px;
      font-family: inherit;
      transition: border-color 160ms ease, box-shadow 160ms ease;
    }}
    input:focus {{
      outline: none;
      box-shadow: 0 0 0 3px rgba(59,130,246,0.35);
      border-color: var(--border-focus);
    }}
    button {{
      width: 100%;
      padding: 12px;
      border-radius: var(--radius-md);
      background: linear-gradient(135deg, #3B82F6, #2563EB);
      color: #fff;
      border: 1px solid rgba(59,130,246,0.50);
      cursor: pointer;
      font-weight: 600;
      font-size: 14px;
      font-family: inherit;
      box-shadow: 0 4px 20px rgba(59,130,246,0.35);
      transition: all 180ms cubic-bezier(0.16,1,0.3,1);
    }}
    button:hover {{
      background: linear-gradient(135deg, #4F94F8, #3B82F6);
      box-shadow: 0 6px 28px rgba(59,130,246,0.50);
      transform: translateY(-2px);
    }}
    .error {{
      background: var(--red-soft);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      border-left: 3px solid var(--red);
      border-radius: 0 var(--radius-md) var(--radius-md) 0;
      color: #FCA5A5;
      padding: 12px 16px;
      margin-bottom: 20px;
      font-size: 13px;
    }}
    .foot {{ margin-top: 14px; text-align: center; color: var(--text-muted); font-size: 12px; }}
    @keyframes spinArc {{
      from {{ transform: rotate(0deg); }}
      to   {{ transform: rotate(360deg); }}
    }}
    @keyframes pulseRing {{
      0%, 100% {{ transform: scale(1);    opacity: 0.9; }}
      50%       {{ transform: scale(1.05); opacity: 0.50; }}
    }}
    @media (max-width: 900px) {{
      body {{ grid-template-columns: 1fr; }}
      .visual {{ display: none; }}
      .panel {{ padding: 24px; }}
      .box {{ width: min(100%, 400px); }}
    }}
  </style>
</head>
<body>
  <div class="visual">
    <div class="visual-card">
      <div class="eyebrow">Sprint Health</div>
      <div class="visual-title">A calmer control room for sprint delivery, quality, and team flow.</div>
      <p class="visual-sub">Track delivery health, bug pressure, and workspace activity with one cohesive operational surface.</p>
      <div class="arc-wrap">
        <div class="arc" aria-hidden="true"></div>
        <div class="arc-label">
          <div class="metric-eyebrow">Workspace</div>
          <div class="metric-big">Sprint Health</div>
          <div class="metric-sub">Delivery quality, blocked time, bugs, and weekly momentum in one place.</div>
        </div>
      </div>
    </div>
  </div>
  <div class="panel">
    <div class="box">
      <div class="eyebrow">Sprint Health</div>
      <div class="login-title">Sign in to your workspace</div>
      <p class="login-sub">Track delivery health, bugs, and team activity.</p>
      <div class="divider"></div>
      {error_banner}
      <form method="post" action="/login">
        <input type="hidden" id="nextField" name="next">
        <label for="username">Email address</label>
        <input id="username" type="email" name="username" placeholder="admin@example.com" required autofocus autocomplete="email">
        <label for="password">Password</label>
        <input id="password" type="password" name="password" placeholder="Enter your password" required autocomplete="current-password">
        <button type="submit">Sign In</button>
      </form>
      <div class="foot">Secured with JWT authentication</div>
      <script>
        const urlParams = new URLSearchParams(window.location.search);
        if(urlParams.has('next')) document.getElementById('nextField').value = urlParams.get('next');
      </script>
    </div>
  </div>
</body>
</html>"""


class AdminHandler(BaseHTTPRequestHandler):
    def _get_session_user(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = c.get("session_id")
        return _get_session_user_by_id(sid.value) if sid else None

    def _get_session_id(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = c.get("session_id")
        return sid.value if sid else None

    def _send_html(self, html: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _redirect(self, target):
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def do_GET(self):
        u = self._get_session_user()
        p = urlparse(self.path)
        if p.path == "/login": return self._send_html(_login_html())
        if not u: return self._redirect(f"/login?next={escape(p.path)}")
        if p.path == "/":
            rf = Path(__file__).parent / "sprint_health_report.html"
            return self._send_html(rf.read_text(encoding="utf-8") if rf.exists() else "Report not found.")
        if p.path == "/admin":
            if u["role"] == "viewer": return self._send_html("Access Denied", 403)
            q = parse_qs(p.query)
            m = "Saved." if "saved" in q else ("Reset." if "reset" in q else "")
            return self._send_html(_dashboard_html(u, message=m))
        if p.path == "/users":
            if u["role"] != "admin": return self._send_html("Access Denied", 403)
            return self._send_html(_users_html(u))
        if p.path == "/logout":
            sid = self._get_session_id()
            if sid:
                log_audit_event(DB_PATH, event_type="LOGOUT", user_email=u.get("email", "") if u else "")
                _delete_session(sid)
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session_id=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            return

    def do_POST(self):
        l = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(l).decode("utf-8"))
        p = urlparse(self.path).path
        try:
            if p == "/login":
                un, pw = form.get("username", [""])[0], form.get("password", [""])[0]
                nxt = form.get("next", ["/"])[0] or "/"
                usr = db_authenticate(DB_PATH, un, pw)
                if usr:
                    sid = _create_session(usr["email"], usr["role"])
                    log_audit_event(DB_PATH, event_type="LOGIN_SUCCESS", user_email=usr["email"])
                    self.send_response(303)
                    self.send_header("Location", nxt)
                    self.send_header("Set-Cookie", f"session_id={sid}; Max-Age={SESSION_EXPIRY_DAYS*86400}; Path=/; HttpOnly; SameSite=Strict")
                    self.end_headers()
                else:
                    log_audit_event(DB_PATH, event_type="LOGIN_FAILED", user_email=un)
                    self._send_html(_login_html("Invalid login."), 401)
                return
            u = self._get_session_user()
            if not u:
                return self._redirect("/login")
            if p == "/save" and u["role"] in ["admin", "editor"]:
                previous = sprint_health.load_metrics_config()
                updated = sprint_health.save_metrics_config(_build_config_from_form(form))
                sprint_health.reload_metrics_config()
                changes = sprint_health.describe_config_changes(previous, updated)
                if changes:
                    send_slack_message("Config updated:\n" + "\n".join(f"- {item}" for item in changes))
                log_audit_event(DB_PATH, event_type="CONFIG_CHANGED", user_email=u.get("email", ""), details="Config saved")
                self._redirect("/admin?saved=1")
                return
            elif p == "/reset" and u["role"] in ["admin", "editor"]:
                previous = sprint_health.load_metrics_config()
                updated = sprint_health.save_metrics_config(sprint_health.DEFAULT_METRICS_CONFIG)
                sprint_health.reload_metrics_config()
                changes = sprint_health.describe_config_changes(previous, updated)
                if changes:
                    send_slack_message("Config updated:\n" + "\n".join(f"- {item}" for item in changes))
                log_audit_event(DB_PATH, event_type="CONFIG_CHANGED", user_email=u.get("email", ""), details="Config reset to defaults")
                self._redirect("/admin?reset=1")
                return
            elif p == "/users/add" and u["role"] == "admin":
                nu, np, nr = form.get("new_username", [""])[0], form.get("new_password", [""])[0], form.get("new_role", ["viewer"])[0]
                result = db_create_user(DB_PATH, email=nu, password=np, role=nr)
                if result:
                    log_audit_event(DB_PATH, event_type="USER_CREATED", user_email=u.get("email", ""), details=f"Created {nu} as {nr}")
                    self._send_html(_users_html(u, "Added."))
                else:
                    self._send_html(_users_html(u, error="Exists."))
                return
            elif p == "/users/delete" and u["role"] == "admin":
                du = form.get("username", [""])[0]
                if db_delete_user(DB_PATH, du):
                    log_audit_event(DB_PATH, event_type="USER_DELETED", user_email=u.get("email", ""), details=f"Deleted {du}")
                    self._send_html(_users_html(u, "Deleted."))
                return
            self.send_error(404)
        except ValueError as exc:
            u = self._get_session_user() or {"role": "viewer"}
            if p in {"/save", "/reset"}:
                self._send_html(_dashboard_html(u, error=str(exc)), 400)
                return
            self._send_html(str(exc), 400)


class GracefulThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server configured to shut down cleanly."""

    daemon_threads = True
    allow_reuse_address = True


def run_dashboard():
    logger.info("Admin dashboard startup host=%s port=%s env=%s", HOST, PORT, os.getenv("RAILWAY_ENVIRONMENT", "local"))
    server = GracefulThreadingHTTPServer((HOST, PORT), AdminHandler)

    def _shutdown(signum, _frame) -> None:
        logger.info("Admin dashboard stopping cleanly after signal=%s", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        logger.info("Admin dashboard shutdown complete")


if __name__ == "__main__":
    run_dashboard()
