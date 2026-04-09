import hashlib
import json
import os
import uuid
import threading
from datetime import datetime, timedelta
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

import sprint_health_2 as sprint_health
import bcrypt

load_dotenv()

# Use 0.0.0.0 for production (Railway, Heroku, etc), 127.0.0.1 for local
DEFAULT_HOST = "0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT") else "127.0.0.1"
HOST = os.getenv("ADMIN_DASHBOARD_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST

# Railway sets PORT env var, default to 8765 for local
PORT = int(os.getenv("PORT", os.getenv("ADMIN_DASHBOARD_PORT", "8765")))

# Use the persistent DATA_DIR from the core logic script
AUTH_FILE = sprint_health.DATA_DIR / "auth_users.json"
SESSION_EXPIRY_DAYS = 30

# Simple In-Memory Rate Limiting: {ip: {count: N, reset_time: timestamp}}
LOGIN_ATTEMPTS = {}
RATE_LIMIT_LOGIN = 5 # 5 attempts per 60s
RATE_LIMIT_WINDOW = 60
ATTEMPTS_LOCK = threading.Lock()



class AuthManager:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.data = self._load()

    def _load(self):
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            # Bootstrap: If no users exist, create a default admin
            if not data.get("users"):
                init_pw = os.getenv("INITIAL_ADMIN_PASSWORD", "admin1234")
                data["users"] = {
                    "admin@lumofy.com": {
                        "password": self.hash_password(init_pw),
                        "role": "admin",
                        "created_at": datetime.now().isoformat()
                    }
                }
                self.file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data
        except:
            # Bootstrap for missing file
            init_pw = os.getenv("INITIAL_ADMIN_PASSWORD", "admin1234")
            initial_data = {
                "users": {
                    "admin@lumofy.com": {
                        "password": self.hash_password(init_pw),
                        "role": "admin",
                        "created_at": datetime.now().isoformat()
                    }
                },
                "sessions": {}
            }
            if not self.file_path.parent.exists():
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.file_path.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")
            return initial_data

    def save(self):
        self.file_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def authenticate(self, username, password):
        user = self.data["users"].get(username)
        if not user:
            return None
        # Verify using bcrypt
        try:
            stored = user["password"]
            if bcrypt.checkpw(password.encode(), stored.encode()):
                return user
        except Exception:
            return None
        return None

    def create_session(self, username):
        session_id = str(uuid.uuid4())
        expiry = (datetime.now() + timedelta(days=SESSION_EXPIRY_DAYS)).isoformat()
        self.data["sessions"][session_id] = {"username": username, "expires": expiry}
        self.save()
        return session_id

    def get_user_from_session(self, session_id):
        session = self.data["sessions"].get(session_id)
        if not session:
            return None
        try:
            if datetime.fromisoformat(session["expires"]) < datetime.now():
                del self.data["sessions"][session_id]
                self.save()
                return None
        except:
            return None
        return self.data["users"].get(session["username"])

    def delete_session(self, session_id):
        if session_id in self.data["sessions"]:
            del self.data["sessions"][session_id]
            self.save()

    def add_user(self, username, password, role):
        if username in self.data["users"]:
            return False
        self.data["users"][username] = {
            "password": self.hash_password(password),
            "role": role,
            "created_at": datetime.now().isoformat(),
        }
        self.save()
        return True

    def delete_user(self, username):
        if username in self.data["users"]:
            del self.data["users"][username]
            to_del = [sid for sid, s in self.data["sessions"].items() if s["username"] == username]
            for sid in to_del:
                del self.data["sessions"][sid]
            self.save()
            return True
        return False


auth = AuthManager(AUTH_FILE)


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
    w, p, c, co, ct, br, bd, st, l, fs, ap, j, b, u = (
        config["weights"], config["points"], config["commitment"], config["carryover"],
        config["cycle_time"], config["bug_ratio"], config["burndown"], config["stale_thresholds"],
        config["labels"], config["final_score"], config["activity_people"], config["jira"],
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
    :root {{
      --bg: #06090f; --sidebar: #0b1220; --card-bg: rgba(20, 30, 50, 0.7);
      --input-bg: rgba(0, 0, 0, 0.25); --glass-border: rgba(255, 255, 255, 0.08);
      --ant-primary-500: #1677ff; --text-main: #f0f5ff; --text-soft: #8c98ae;
      --success-main: #52c41a; --error-main: #ff4d4f; --warning-main: #faad14;
      --shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }}
    body[data-theme="light"] {{
      --bg: #f4f7fa; --sidebar: #ffffff; --card-bg: #ffffff;
      --input-bg: #f9fbfd; --glass-border: rgba(0, 0, 0, 0.06);
      --text-main: #19314f; --text-soft: #637d92;
      --shadow: 0 10px 30px rgba(90, 121, 163, 0.08);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg); color: var(--text-main); min-height: 100vh;
      display: flex; overflow-x: hidden; transition: background 0.3s ease;
    }}
    #admin-particles {{ position: fixed; inset: 0; z-index: 1; pointer-events: none; opacity: 0.6; }}
    .app-container {{ display: flex; width: 100%; z-index: 2; }}
    .sidebar {{
      width: 260px; background: var(--sidebar); border-right: 1px solid var(--glass-border);
      height: 100vh; position: sticky; top: 0; padding: 40px 24px; display: flex; flex-direction: column; gap: 32px;
    }}
    .main-content {{ flex: 1; width: 100%; transition: all 0.3s ease; position: relative; }}
    .main-content {{ padding: 20px 40px; }}
    .main-content.full-page {{ padding: 0 !important; max-width: none !important; height: 100vh; overflow: hidden; }}
    .nav-list {{ list-style: none; display: flex; flex-direction: column; gap: 8px; }}
    .nav-item a {{
      display: block; padding: 12px 16px; border-radius: 12px; color: var(--text-soft);
      text-decoration: none; font-size: 14px; font-weight: 600; transition: all 0.2s;
    }}
    .nav-item a:hover, .nav-item.active a {{ background: rgba(22, 119, 255, 0.1); color: var(--ant-primary-500); }}
    .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }}
    .header h1 {{ font-size: 24px; font-weight: 900; }}
    section {{ background: var(--card-bg); border: 1px solid var(--glass-border); border-radius: 20px; padding: 24px; box-shadow: var(--shadow); margin-bottom: 24px; }}
    .section-head {{ margin-bottom: 20px; }}
    .section-head h3 {{ font-size: 16px; font-weight: 800; border-left: 3px solid var(--ant-primary-500); padding-left: 10px; margin-bottom: 4px; }}
    .section-head p {{ font-size: 11px; color: var(--text-soft); padding-left: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 12px; }}
    .field {{ display: flex; flex-direction: column; gap: 4px; padding: 12px; background: var(--input-bg); border-radius: 12px; border: 1px solid var(--glass-border); }}
    .field span {{ font-size: 10px; font-weight: 800; color: var(--text-soft); text-transform: uppercase; letter-spacing: 0.5px; }}
    input, select, textarea {{ background: transparent; border: none; color: var(--text-main); font-size: 14px; font-weight: 600; width: 100%; outline: none; }}
    button, .btn {{ padding: 12px 24px; border-radius: 12px; font-weight: 700; cursor: pointer; border: none; transition: 0.2s; text-decoration: none; display: inline-block; }}
    .save {{ background: var(--ant-primary-500); color: #fff; }}
    .reset {{ background: rgba(0,0,0,0.05); color: var(--text-main); border: 1px solid var(--glass-border); }}
    .banner {{ padding: 14px 20px; border-radius: 14px; margin-bottom: 24px; border: 1px solid transparent; }}
    .banner.ok {{ background: rgba(82, 196, 26, 0.1); color: var(--success-main); }}
    .banner.error {{ background: rgba(255, 77, 79, 0.1); color: var(--error-main); }}
    @media (max-width: 800px) {{ .sidebar {{ display: none; }} .main-content {{ padding: 20px; }} }}
  </style>
</head>
<body>
  <div class="app-container">
    <aside class="sidebar">
      <h2 style="font-size:18px; margin-bottom:20px;">Lumofy Platform</h2>
      <nav>
        <ul class="nav-list">
          <li class="nav-item {'active' if active_path == '/' else ''}"><a href="/">Main Report</a></li>
          <li class="nav-item {'active' if active_path == '/admin' else ''}" {admin_only}><a href="/admin">Settings</a></li>
          <li class="nav-item {'active' if active_path == '/users' else ''}" {user_mgmt_only}><a href="/users">User Management</a></li>
          <li class="nav-item"><a href="/logout" style="color:var(--error-main)">Logout</a></li>
        </ul>
      </nav>
    </aside>
    <main class="main-content {{'full-page' if active_path == '/' else ''}}">
      {content}
    </main>
  </div>
  <canvas id="admin-particles"></canvas>
  <script>
    (() => {{
      const storageKey = 'sprint-health-theme';
      const body = document.body;
      const applyTheme = (t) => {{ body.dataset.theme = t; localStorage.setItem(storageKey, t); }};
      applyTheme(localStorage.getItem(storageKey) || 'light');
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
      const theme = body.dataset.theme;
      ctx.fillStyle = theme === 'light' ? 'rgba(22,119,255,0.2)' : 'rgba(255,255,255,0.2)';
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
        <div><h1>Control Center</h1><p>Edit platform logic and visual settings.</p></div>
      </header>
      {saved_banner}{error_banner}
      <form method="post" action="/save">
        {''.join(sections)}
        <div style="display:flex; gap:16px; margin-top:20px">
          <button class="save" type="submit">Save Changes</button>
          <button class="reset" type="submit" formaction="/reset">Reset Defaults</button>
        </div>
      </form>
    """
    return _layout_html(content, user_role=user["role"], active_path="/admin")


def _users_html(user, message: str = "", error: str = "") -> str:
    users_data = auth.data["users"]
    banner = f"<div class='banner ok'>{escape(message)}</div>" if message else ""
    err_banner = f"<div class='banner error'>{escape(error)}</div>" if error else ""

    rows = ""
    for username, info in users_data.items():
        rows += f"""<div class="field" style="flex-direction:row; justify-content:space-between; align-items:center;">
          <div><strong>{escape(username)}</strong><br><small>{escape(info['role'].capitalize())}</small></div>
          <form method="post" action="/users/delete" style="margin:0">
            <input type="hidden" name="username" value="{escape(username)}">
            <button type="submit" style="background:var(--error-main); padding:6px 12px; font-size:11px; color:#fff">Delete</button>
          </form>
        </div>"""

    content = f"""
      <h1>User Management</h1>
      <p style="color:var(--text-soft); margin-bottom:30px;">Manage access levels.</p>
      {banner}{err_banner}
      <section>
        <div class="section-head"><h3>Add Account</h3></div>
        <form method="post" action="/users/add" class="grid" style="grid-template-columns:1fr 1fr 1fr">
          <div class="field"><span>Email</span><input type="text" name="new_username" required></div>
          <div class="field"><span>Password</span><input type="password" name="new_password" required></div>
          <div class="field"><span>Role</span><select name="new_role"><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="admin">Admin</option></select></div>
          <div style="grid-column:1/-1"><button class="save" type="submit" style="width:100%">Add User</button></div>
        </form>
      </section>
      <section>
        <div class="section-head"><h3>Existing Accounts</h3></div>
        <div style="display:flex; flex-direction:column; gap:8px;">{rows}</div>
      </section>
    """
    return _layout_html(content, user_role=user["role"], active_path="/users")


def _login_html(error: str = "") -> str:
    error_banner = f"<div class='error'>{escape(error)}</div>" if error else ""
    return f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: sans-serif; background: #06090f; color: #fff; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
    .box {{ background: #0b1220; padding: 40px; border-radius: 20px; border: 1px solid rgba(255,255,255,0.08); width: 340px; }}
    input {{ width: 100%; padding: 12px; margin-bottom: 12px; border-radius: 10px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; box-sizing: border-box; }}
    button {{ width: 100%; padding: 12px; border-radius: 10px; background: #1677ff; color: #fff; border: none; cursor: pointer; font-weight: 700; }}
    .error {{ background: rgba(255,77,79,0.1); color: #ff7875; padding: 10px; border-radius: 8px; margin-bottom: 20px; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="box">
    <h2 style="margin:0 0 20px">Platform Login</h2>
    {error_banner}
    <form method="post" action="/login">
      <input type="hidden" id="nextField" name="next">
      <input type="text" name="username" placeholder="Email" required autofocus>
      <input type="password" name="password" placeholder="Password" required>
      <button type="submit">Sign In</button>
    </form>
    <script>
      const urlParams = new URLSearchParams(window.location.search);
      if(urlParams.has('next')) document.getElementById('nextField').value = urlParams.get('next');
    </script>
  </div>
</body>
</html>"""


class AdminHandler(BaseHTTPRequestHandler):
    def _get_session_user(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = c.get("session_id")
        return auth.get_user_from_session(sid.value) if sid else None

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
            iframe_html = f"""
            <div style="height: 100vh; width: 100%; overflow: hidden; background: var(--page-bg);">
                <iframe src="/raw-report?embedded=1" style="width: 100%; height: 100%; border: none;" id="reportFrame"></iframe>
            </div>
            """
            return self._send_html(_layout_html(iframe_html, title="Main Report", user_role=u["role"], active_path="/"))

        if p.path == "/raw-report":
            rf = sprint_health.DATA_DIR / "sprint_health_report.html"
            if not rf.exists():
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
            sid = cookies.SimpleCookie(self.headers.get("Cookie", "")).get("session_id")
            if sid: auth.delete_session(sid.value)
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session_id=; Max-Age=0; Path=/")
            self.end_headers()
            return

    def do_POST(self):
        l = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(l).decode("utf-8"))
        p = urlparse(self.path).path
        if p == "/login":
            un, pw = form.get("username", [""])[0], form.get("password", [""])[0]
            nxt = form.get("next", ["/"])[0] or "/"
            
            # Rate Limiting Logic
            ip = self.client_address[0]
            with ATTEMPTS_LOCK:
                rec = LOGIN_ATTEMPTS.get(ip, {"count": 0, "reset": 0})
                if datetime.now().timestamp() > rec["reset"]:
                    rec = {"count": 0, "reset": datetime.now().timestamp() + RATE_LIMIT_WINDOW}
                
                if rec["count"] >= RATE_LIMIT_LOGIN:
                    return self._send_html(_login_html("Too many attempts. Wait a minute."), 429)
                
                rec["count"] += 1
                LOGIN_ATTEMPTS[ip] = rec

            usr = auth.authenticate(un, pw)
            if usr:
                # Reset rate limit on success
                with ATTEMPTS_LOCK: 
                    if ip in LOGIN_ATTEMPTS: del LOGIN_ATTEMPTS[ip]
                    
                sid = auth.create_session(un)
                self.send_response(303)
                self.send_header("Location", nxt)
                # Secure Flag Added
                sc = f"session_id={sid}; Max-Age={SESSION_EXPIRY_DAYS*86400}; Path=/; HttpOnly; SameSite=Lax"
                if os.getenv("RAILWAY_ENVIRONMENT"):
                    sc += "; Secure"
                self.send_header("Set-Cookie", sc)
                self.end_headers()
            else: self._send_html(_login_html("Invalid login."), 401)
            return
        u = self._get_session_user()
        if not u: return self._redirect("/login")
        if p == "/save" and u["role"] in ["admin", "editor"]:
            sprint_health.save_metrics_config(_build_config_from_form(form))
            sprint_health.reload_metrics_config()
            # Trigger immediate background refresh via global flag AND trigger file
            sprint_health.FORCE_REFRESH_REQUESTED = True
            try: sprint_health.TRIGGER_FILE.touch()
            except: pass
            self._redirect("/admin?saved=1")
            return
        elif p == "/reset" and u["role"] in ["admin", "editor"]:
            sprint_health.save_metrics_config(sprint_health.DEFAULT_METRICS_CONFIG)
            sprint_health.reload_metrics_config()
            # Trigger immediate background refresh via global flag AND trigger file
            sprint_health.FORCE_REFRESH_REQUESTED = True
            try: sprint_health.TRIGGER_FILE.touch()
            except: pass
            self._redirect("/admin?reset=1")
            return
        elif p == "/users/add" and u["role"] == "admin":
            nu, np, nr = form.get("new_username", [""])[0], form.get("new_password", [""])[0], form.get("new_role", ["viewer"])[0]
            if auth.add_user(nu, np, nr): self._send_html(_users_html(u, "Added."))
            else: self._send_html(_users_html(u, error="Exists."))
            return
        elif p == "/users/delete" and u["role"] == "admin":
            du = form.get("username", [""])[0]
            if auth.delete_user(du): self._send_html(_users_html(u, "Deleted."))
            return
        else: self.send_error(404)


def run_dashboard():
    # Start the background reporter thread
    bg_thread = threading.Thread(
        target=sprint_health.run_watch,
        kwargs={"interval_seconds": 600}, # Check every 10 mins normally
        daemon=True
    )
    bg_thread.start()
    
    print(f"[admin] Server ready at http://{HOST}:{PORT}")
    print(f"[admin] Environment: {os.getenv('RAILWAY_ENVIRONMENT', 'local')}")
    print(f"[admin] Persistence Dir: {sprint_health.DATA_DIR}")
    ThreadingHTTPServer((HOST, PORT), AdminHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
