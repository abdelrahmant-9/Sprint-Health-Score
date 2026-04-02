import hashlib
import os
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

import sprint_health_2 as sprint_health

load_dotenv()

HOST = os.getenv("ADMIN_DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("ADMIN_DASHBOARD_PORT", "8765"))
PASSWORD = os.getenv("ADMIN_DASHBOARD_PASSWORD", "").strip()
COOKIE_NAME = "sprint_health_admin"
COOKIE_VALUE = hashlib.sha256(PASSWORD.encode("utf-8")).hexdigest() if PASSWORD else ""


def _float_value(params: dict, key: str) -> float:
    raw = (params.get(key, [""])[0] or "").strip()
    return float(raw)


def _int_value(params: dict, key: str) -> int:
    raw = (params.get(key, [""])[0] or "").strip()
    return int(float(raw))


def _text_value(params: dict, key: str) -> str:
    return (params.get(key, [""])[0] or "").strip()


def _bool_value(params: dict, key: str) -> bool:
    return (params.get(key, [""])[0] or "").strip().lower() in {"1", "true", "on", "yes"}


def _list_values(params: dict, key: str) -> list[str]:
    raw = (params.get(key, [""])[0] or "").strip()
    if not raw:
        return []
    parts = []
    for line in raw.replace(",", "\n").splitlines():
        item = line.strip()
        if item:
            parts.append(item)
    return parts


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
        f"<label class='field field-wide'>"
        f"<span>{escape(label)}</span>"
        f"<textarea name='{escape(name)}' rows='{rows}' required>{escape(value)}</textarea>"
        f"{hint_html}"
        f"</label>"
    )


def _field_checkbox(label: str, name: str, checked: bool, hint: str = "") -> str:
    hint_html = f"<small>{escape(hint)}</small>" if hint else ""
    return (
        f"<label class='toggle'>"
        f"<span class='toggle-copy'>"
        f"<strong>{escape(label)}</strong>"
        f"{hint_html}"
        f"</span>"
        f"<input type='checkbox' name='{escape(name)}' value='1' {'checked' if checked else ''}>"
        f"</label>"
    )


def _section(title: str, description: str, fields: list[str], compact: bool = False) -> str:
    grid_class = "grid compact-grid" if compact else "grid"
    description_html = f"<p>{escape(description)}</p>" if description else ""
    return (
        f"<section class='card'>"
        f"<div class='section-head'><h2>{escape(title)}</h2>{description_html}</div>"
        f"<div class='{grid_class}'>{''.join(fields)}</div>"
        f"</section>"
    )


def _build_config_from_form(params: dict) -> dict:
    current = sprint_health.load_metrics_config()
    config = sprint_health._deep_copy_config(current)

    config["weights"]["commitment"] = _float_value(params, "weight_commitment")
    config["weights"]["carryover"] = _float_value(params, "weight_carryover")
    config["weights"]["cycle_time"] = _float_value(params, "weight_cycle_time")
    config["weights"]["bug_ratio"] = _float_value(params, "weight_bug_ratio")

    config["points"]["excellent"] = _int_value(params, "point_excellent")
    config["points"]["good"] = _int_value(params, "point_good")
    config["points"]["warning"] = _int_value(params, "point_warning")
    config["points"]["poor"] = _int_value(params, "point_poor")
    config["points"]["neutral"] = _int_value(params, "point_neutral")

    config["commitment"]["ideal_min_pct"] = _float_value(params, "commitment_ideal_min_pct")
    config["commitment"]["ideal_max_pct"] = _float_value(params, "commitment_ideal_max_pct")
    config["commitment"]["good_min_pct"] = _float_value(params, "commitment_good_min_pct")
    config["commitment"]["warning_min_pct"] = _float_value(params, "commitment_warning_min_pct")
    config["commitment"]["extended_cap_score"] = _int_value(params, "commitment_extended_cap_score")

    config["carryover"]["excellent_lt_pct"] = _float_value(params, "carryover_excellent_lt_pct")
    config["carryover"]["good_lte_pct"] = _float_value(params, "carryover_good_lte_pct")
    config["carryover"]["warning_lte_pct"] = _float_value(params, "carryover_warning_lte_pct")
    config["carryover"]["extended_penalty"] = _int_value(params, "carryover_extended_penalty")

    config["cycle_time"]["stable_abs_pct"] = _float_value(params, "cycle_time_stable_abs_pct")
    config["cycle_time"]["good_increase_pct"] = _float_value(params, "cycle_time_good_increase_pct")
    config["cycle_time"]["warning_increase_pct"] = _float_value(params, "cycle_time_warning_increase_pct")

    config["bug_ratio"]["excellent_lt_pct"] = _float_value(params, "bug_ratio_excellent_lt_pct")
    config["bug_ratio"]["good_lte_pct"] = _float_value(params, "bug_ratio_good_lte_pct")
    config["bug_ratio"]["warning_lte_pct"] = _float_value(params, "bug_ratio_warning_lte_pct")

    config["burndown"]["done_bonus"] = _int_value(params, "burndown_done_bonus")
    config["burndown"]["on_track_bonus"] = _int_value(params, "burndown_on_track_bonus")
    config["burndown"]["behind_small_max"] = _int_value(params, "burndown_behind_small_max")
    config["burndown"]["behind_medium_max"] = _int_value(params, "burndown_behind_medium_max")
    config["burndown"]["behind_medium_penalty"] = _int_value(params, "burndown_behind_medium_penalty")
    config["burndown"]["behind_large_penalty"] = _int_value(params, "burndown_behind_large_penalty")

    config["stale_thresholds"]["bug_days"] = _int_value(params, "stale_bug_days")
    config["stale_thresholds"]["subtask_days"] = _int_value(params, "stale_subtask_days")
    config["stale_thresholds"]["story_no_points_days"] = _int_value(params, "stale_story_no_points_days")
    config["stale_thresholds"]["story_small_max_points"] = _float_value(params, "stale_story_small_max_points")
    config["stale_thresholds"]["story_small_days"] = _int_value(params, "stale_story_small_days")
    config["stale_thresholds"]["story_medium_max_points"] = _float_value(params, "stale_story_medium_max_points")
    config["stale_thresholds"]["story_medium_days"] = _int_value(params, "stale_story_medium_days")
    config["stale_thresholds"]["story_large_days"] = _int_value(params, "stale_story_large_days")
    config["stale_thresholds"]["default_days"] = _int_value(params, "stale_default_days")

    config["labels"]["green_min_score"] = _int_value(params, "label_green_min_score")
    config["labels"]["yellow_min_score"] = _int_value(params, "label_yellow_min_score")
    config["labels"]["orange_min_score"] = _int_value(params, "label_orange_min_score")

    config["final_score"]["custom_formula"] = _text_value(params, "final_score_custom_formula")
    config["final_score"]["round_result"] = _bool_value(params, "final_score_round_result")
    config["final_score"]["min_score"] = _int_value(params, "final_score_min_score")
    config["final_score"]["max_score"] = _int_value(params, "final_score_max_score")
    config.setdefault("activity_people", {})
    config["activity_people"]["qa_names"] = _list_values(params, "activity_qa_names")
    config["activity_people"]["developer_names"] = _list_values(params, "activity_developer_names")

    total_weight = sum(config["weights"].values())
    if round(total_weight, 4) <= 0:
        raise ValueError("Total weights must be greater than zero.")

    return config


def _build_sections(config: dict) -> list[str]:
    weights = config["weights"]
    points = config["points"]
    commitment = config["commitment"]
    carryover = config["carryover"]
    cycle_time = config["cycle_time"]
    bug_ratio = config["bug_ratio"]
    burndown = config["burndown"]
    stale = config["stale_thresholds"]
    labels = config["labels"]
    final_score = config["final_score"]
    activity_people = config.get("activity_people", {"qa_names": [], "developer_names": []})

    return [
        _section(
            "Weights",
            "Main contribution of each signal in the final sprint score.",
            [
                _field_input("Commitment", "weight_commitment", weights["commitment"], "0.01"),
                _field_input("Carryover", "weight_carryover", weights["carryover"], "0.01"),
                _field_input("Cycle Time", "weight_cycle_time", weights["cycle_time"], "0.01"),
                _field_input("Bug Ratio", "weight_bug_ratio", weights["bug_ratio"], "0.01"),
            ],
            compact=True,
        ),
        _section(
            "Points",
            "Base score values reused by all scoring rules.",
            [
                _field_input("Excellent", "point_excellent", points["excellent"]),
                _field_input("Good", "point_good", points["good"]),
                _field_input("Warning", "point_warning", points["warning"]),
                _field_input("Poor", "point_poor", points["poor"]),
                _field_input("Neutral", "point_neutral", points["neutral"]),
            ],
        ),
        _section(
            "Commitment",
            "How completed scope is translated into a signal score.",
            [
                _field_input("Ideal min %", "commitment_ideal_min_pct", commitment["ideal_min_pct"], "0.1"),
                _field_input("Ideal max %", "commitment_ideal_max_pct", commitment["ideal_max_pct"], "0.1"),
                _field_input("Good min %", "commitment_good_min_pct", commitment["good_min_pct"], "0.1"),
                _field_input("Warning min %", "commitment_warning_min_pct", commitment["warning_min_pct"], "0.1"),
                _field_input("Extended cap score", "commitment_extended_cap_score", commitment["extended_cap_score"]),
            ],
        ),
        _section(
            "Carryover",
            "How unfinished scope impacts the signal.",
            [
                _field_input("Excellent below %", "carryover_excellent_lt_pct", carryover["excellent_lt_pct"], "0.1"),
                _field_input("Good up to %", "carryover_good_lte_pct", carryover["good_lte_pct"], "0.1"),
                _field_input("Warning up to %", "carryover_warning_lte_pct", carryover["warning_lte_pct"], "0.1"),
                _field_input("Extended penalty", "carryover_extended_penalty", carryover["extended_penalty"]),
            ],
        ),
        _section(
            "Cycle Time",
            "Compares current sprint average cycle time against recent history.",
            [
                _field_input("Stable abs %", "cycle_time_stable_abs_pct", cycle_time["stable_abs_pct"], "0.1"),
                _field_input("Good increase %", "cycle_time_good_increase_pct", cycle_time["good_increase_pct"], "0.1"),
                _field_input("Warning increase %", "cycle_time_warning_increase_pct", cycle_time["warning_increase_pct"], "0.1"),
            ],
            compact=True,
        ),
        _section(
            "Bug Ratio",
            "Thresholds for newly created bugs during the sprint.",
            [
                _field_input("Excellent below %", "bug_ratio_excellent_lt_pct", bug_ratio["excellent_lt_pct"], "0.1"),
                _field_input("Good up to %", "bug_ratio_good_lte_pct", bug_ratio["good_lte_pct"], "0.1"),
                _field_input("Warning up to %", "bug_ratio_warning_lte_pct", bug_ratio["warning_lte_pct"], "0.1"),
            ],
            compact=True,
        ),
        _section(
            "Burndown Nudge",
            "Bonus or penalty applied after the main signal calculation.",
            [
                _field_input("Done bonus", "burndown_done_bonus", burndown["done_bonus"]),
                _field_input("On-track bonus", "burndown_on_track_bonus", burndown["on_track_bonus"]),
                _field_input("Behind small max", "burndown_behind_small_max", burndown["behind_small_max"]),
                _field_input("Behind medium max", "burndown_behind_medium_max", burndown["behind_medium_max"]),
                _field_input("Behind medium penalty", "burndown_behind_medium_penalty", burndown["behind_medium_penalty"]),
                _field_input("Behind large penalty", "burndown_behind_large_penalty", burndown["behind_large_penalty"]),
            ],
        ),
        _section(
            "Stale Thresholds",
            "Days without movement before an issue is considered stale.",
            [
                _field_input("Bug days", "stale_bug_days", stale["bug_days"]),
                _field_input("Sub-task days", "stale_subtask_days", stale["subtask_days"]),
                _field_input("Story no-points days", "stale_story_no_points_days", stale["story_no_points_days"]),
                _field_input("Story small max points", "stale_story_small_max_points", stale["story_small_max_points"], "0.1"),
                _field_input("Story small days", "stale_story_small_days", stale["story_small_days"]),
                _field_input("Story medium max points", "stale_story_medium_max_points", stale["story_medium_max_points"], "0.1"),
                _field_input("Story medium days", "stale_story_medium_days", stale["story_medium_days"]),
                _field_input("Story large days", "stale_story_large_days", stale["story_large_days"]),
                _field_input("Default days", "stale_default_days", stale["default_days"]),
            ],
        ),
        _section(
            "Health Bands",
            "Labels shown in the report based on the final score.",
            [
                _field_input("Green min score", "label_green_min_score", labels["green_min_score"]),
                _field_input("Yellow min score", "label_yellow_min_score", labels["yellow_min_score"]),
                _field_input("Orange min score", "label_orange_min_score", labels["orange_min_score"]),
            ],
            compact=True,
        ),
        _section(
            "Final Score Formula",
            "Edit the final combination logic without touching code.",
            [
                _field_textarea(
                    "Custom formula",
                    "final_score_custom_formula",
                    final_score["custom_formula"],
                    "Available names: commitment, carryover, cycle_time, bug_ratio, burndown, weight_commitment, weight_carryover, weight_cycle_time, weight_bug_ratio, weighted_commitment, weighted_carryover, weighted_cycle_time, weighted_bug_ratio, min, max, abs, round.",
                    rows=4,
                ),
                _field_checkbox("Round result", "final_score_round_result", final_score["round_result"], "Round the formula result before saving the final score."),
                _field_input("Minimum score", "final_score_min_score", final_score["min_score"]),
                _field_input("Maximum score", "final_score_max_score", final_score["max_score"]),
            ],
        ),
        _section(
            "People Filters",
            "Optional filters for activity sections. Leave empty to show everyone.",
            [
                _field_textarea(
                    "QA names",
                    "activity_qa_names",
                    "\n".join(activity_people.get("qa_names", [])),
                    "One name per line (or comma-separated). Only these names appear in Today's QA Activity.",
                    rows=4,
                ),
                _field_textarea(
                    "Developer names",
                    "activity_developer_names",
                    "\n".join(activity_people.get("developer_names", [])),
                    "One name per line (or comma-separated). Only these names appear in Today's Developer Activity.",
                    rows=4,
                ),
            ],
        ),
    ]


def _dashboard_html(message: str = "", error: str = "") -> str:
    config = sprint_health.load_metrics_config()
    saved_banner = f"<div class='banner ok'>{escape(message)}</div>" if message else ""
    error_banner = f"<div class='banner error'>{escape(error)}</div>" if error else ""
    sections = _build_sections(config)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sprint Health Admin</title>
  <style>
    :root {{
      --bg: #08111f;
      --panel: #101b2f;
      --panel-2: #0c1628;
      --border: #29456d;
      --border-soft: rgba(83, 122, 170, 0.35);
      --text: #eef4ff;
      --muted: #9bb2cf;
      --accent: #3381ff;
      --accent-2: #1b5fd1;
      --success-bg: #123524;
      --success-text: #9ff0c0;
      --error-bg: #3c1717;
      --error-text: #ffb3b3;
      --shadow: 0 18px 45px rgba(0, 0, 0, 0.22);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 28px 18px 48px;
      font-family: "Segoe UI", Tahoma, Arial, sans-serif;
      background:
        radial-gradient(circle at top right, rgba(51, 129, 255, 0.12), transparent 26%),
        linear-gradient(180deg, #091120 0%, #060d19 100%);
      color: var(--text);
    }}
    .wrap {{ max-width: 1180px; margin: 0 auto; }}
    .hero {{
      padding: 28px;
      margin-bottom: 20px;
      border: 1px solid var(--border-soft);
      border-radius: 24px;
      background: linear-gradient(180deg, rgba(16, 27, 47, 0.98), rgba(10, 19, 34, 0.98));
      box-shadow: var(--shadow);
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: 34px; letter-spacing: -0.03em; }}
    .hero p {{ margin: 0; color: var(--muted); line-height: 1.7; }}
    .banner {{
      border-radius: 14px;
      padding: 14px 16px;
      margin-bottom: 14px;
      border: 1px solid transparent;
    }}
    .banner.ok {{ background: var(--success-bg); color: var(--success-text); }}
    .banner.error {{ background: var(--error-bg); color: var(--error-text); }}
    form {{ display: flex; flex-direction: column; gap: 18px; }}
    .card {{
      background: linear-gradient(180deg, rgba(16, 27, 47, 0.96), rgba(13, 23, 40, 0.96));
      border: 1px solid var(--border-soft);
      border-radius: 22px;
      padding: 22px;
      box-shadow: var(--shadow);
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 18px;
    }}
    .section-head h2 {{ margin: 0; font-size: 18px; }}
    .section-head p {{
      margin: 0;
      max-width: 560px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
    }}
    .compact-grid {{
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    }}
    .field,
    .toggle {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 14px;
      border-radius: 16px;
      background: rgba(7, 14, 26, 0.48);
      border: 1px solid rgba(67, 102, 146, 0.22);
    }}
    .field-wide {{ grid-column: 1 / -1; }}
    .field span,
    .toggle-copy strong {{
      color: var(--text);
      font-size: 14px;
      font-weight: 600;
    }}
    .field small,
    .toggle-copy small {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }}
    input,
    textarea {{
      width: 100%;
      background: var(--panel-2);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 14px;
      font: inherit;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }}
    input:focus,
    textarea:focus {{
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(51, 129, 255, 0.16);
    }}
    textarea {{
      resize: vertical;
      min-height: 110px;
      line-height: 1.6;
    }}
    .toggle {{
      flex-direction: row;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }}
    .toggle input {{
      width: 20px;
      height: 20px;
      margin: 0;
      accent-color: #1597b8;
    }}
    .toggle-copy {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 4px;
    }}
    button,
    .logout {{
      border: 0;
      border-radius: 14px;
      padding: 13px 20px;
      font: inherit;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      transition: transform 0.18s ease, opacity 0.18s ease, background 0.18s ease;
    }}
    button:hover,
    .logout:hover {{
      transform: translateY(-1px);
    }}
    .save {{
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white;
    }}
    .reset {{
      background: #233754;
      color: var(--text);
    }}
    .logout {{
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--border);
    }}
    .tip {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }}
    @media (max-width: 760px) {{
      body {{ padding: 18px 12px 36px; }}
      .hero {{ padding: 22px; }}
      .hero h1 {{ font-size: 28px; }}
      .section-head {{
        flex-direction: column;
      }}
      .toggle {{
        align-items: flex-start;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>Sprint Health Admin Dashboard</h1>
      <p>Adjust scoring logic, thresholds, and formula behavior from one place. Changes are written to <strong>{escape(str(sprint_health.METRICS_CONFIG_PATH))}</strong> and used on the next report run.</p>
    </div>
    {saved_banner}
    {error_banner}
    <form method="post" action="/save">
      {''.join(sections)}
      <div class="actions">
        <button class="save" type="submit">Save Config</button>
      </div>
    </form>
    <form method="post" action="/reset" style="margin-top:12px;">
      <div class="actions">
        <button class="reset" type="submit">Reset Defaults</button>
        <a class="logout" href="/logout">Logout</a>
      </div>
    </form>
    <div class="tip">Local only on {escape(HOST)}:{PORT}. Inputs removed from this dashboard were not deleted from the backend; only non-useful UI controls were hidden to keep the screen focused.</div>
  </div>
</body>
</html>"""


def _login_html(error: str = "") -> str:
    error_banner = f"<div class='error'>{escape(error)}</div>" if error else ""
    password_note = ""
    if not PASSWORD:
        password_note = "<p class='warn'>Set ADMIN_DASHBOARD_PASSWORD in .env first, then restart the dashboard.</p>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin Login</title>
  <style>
    body {{ font-family: "Segoe UI", Tahoma, Arial, sans-serif; background:#0b1220; color:#e6eef8; display:grid; place-items:center; min-height:100vh; margin:0; }}
    .box {{ width:min(420px,92vw); background:#121c2f; border:1px solid #233552; border-radius:18px; padding:24px; }}
    h1 {{ margin:0 0 8px; }}
    p {{ color:#9fb3c8; line-height:1.6; }}
    .error {{ background:#3c1717; color:#ffb3b3; border-radius:10px; padding:10px 12px; margin:12px 0; }}
    .warn {{ color:#ffd27a; }}
    input {{ width:100%; box-sizing:border-box; background:#0b1220; color:#e6eef8; border:1px solid #31486d; border-radius:10px; padding:12px; margin:10px 0 14px; }}
    button {{ width:100%; border:0; border-radius:10px; padding:12px; background:#1a6bff; color:white; font-weight:700; cursor:pointer; }}
  </style>
</head>
<body>
  <form class="box" method="post" action="/login">
    <h1>Private Dashboard</h1>
    <p>Runs on localhost only. Login required before editing the metrics config.</p>
    {password_note}
    {error_banner}
    <input type="password" name="password" placeholder="Dashboard password" required>
    <button type="submit">Login</button>
  </form>
</body>
</html>"""


class AdminHandler(BaseHTTPRequestHandler):
    def _parse_cookies(self) -> dict:
        header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        jar.load(header)
        return {key: morsel.value for key, morsel in jar.items()}

    def _is_authenticated(self) -> bool:
        if not PASSWORD:
            return False
        return self._parse_cookies().get(COOKIE_NAME) == COOKIE_VALUE

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie_header: str = "") -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.end_headers()

    def _read_form(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        return parse_qs(payload, keep_blank_values=True)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/logout":
            self._redirect("/login", f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly")
            return

        if path == "/login":
            self._send_html(_login_html())
            return

        if not self._is_authenticated():
            self._redirect("/login")
            return

        message = "Saved successfully." if "saved=1" in parsed.query else ""
        reset_message = "Defaults restored." if "reset=1" in parsed.query else ""
        self._send_html(_dashboard_html(message or reset_message))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        form = self._read_form()

        if path == "/login":
            if not PASSWORD:
                self._send_html(_login_html("Set ADMIN_DASHBOARD_PASSWORD in .env first."), status=400)
                return
            password = (form.get("password", [""])[0] or "").strip()
            if password != PASSWORD:
                self._send_html(_login_html("Wrong password."), status=401)
                return
            self._redirect("/", f"{COOKIE_NAME}={COOKIE_VALUE}; Path=/; HttpOnly; SameSite=Strict")
            return

        if not self._is_authenticated():
            self._redirect("/login")
            return

        if path == "/save":
            try:
                sprint_health.save_metrics_config(_build_config_from_form(form))
                sprint_health.reload_metrics_config()
            except Exception as e:
                self._send_html(_dashboard_html(error=str(e)), status=400)
                return
            self._redirect("/?saved=1")
            return

        if path == "/reset":
            sprint_health.save_metrics_config(sprint_health.DEFAULT_METRICS_CONFIG)
            sprint_health.reload_metrics_config()
            self._redirect("/?reset=1")
            return

        self.send_error(404)


def run_dashboard() -> None:
    if HOST != "127.0.0.1":
        print(f"[warn] Dashboard host is {HOST}. For private access, keep it on 127.0.0.1.")
    server = ThreadingHTTPServer((HOST, PORT), AdminHandler)
    print(f"[admin] Dashboard running at http://{HOST}:{PORT}")
    print(f"[admin] Metrics config: {sprint_health.METRICS_CONFIG_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    run_dashboard()
