"""Streamlit dashboard for sprint health monitoring."""

from __future__ import annotations

import html
import logging

import plotly.graph_objects as go
import requests
import streamlit as st

from app.config import load_settings


logger = logging.getLogger(__name__)

THEMES = {
    "dark": {
        "primary": "#4f8cff",
        "primary_soft": "rgba(79, 140, 255, 0.08)",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "danger": "#ef4444",
        "info": "#3b82f6",
        "bg": "#0b1020",
        "bg_alt": "#111827",
        "surface": "rgba(15, 23, 42, 0.98)",
        "surface_alt": "rgba(17, 24, 39, 0.98)",
        "border": "rgba(148, 163, 184, 0.12)",
        "text": "#ecf3ff",
        "muted": "#94a3b8",
        "shadow": "0 10px 24px rgba(2, 8, 23, 0.22)",
        "plot_bg": "rgba(0,0,0,0)",
        "grid": "rgba(148, 163, 184, 0.14)",
    },
    "light": {
        "primary": "#2563eb",
        "primary_soft": "rgba(37, 99, 235, 0.06)",
        "success": "#16a34a",
        "warning": "#d97706",
        "danger": "#dc2626",
        "info": "#0284c7",
        "bg": "#f5f7fb",
        "bg_alt": "#eef2f7",
        "surface": "rgba(255, 255, 255, 0.99)",
        "surface_alt": "rgba(248, 250, 252, 0.99)",
        "border": "rgba(15, 23, 42, 0.08)",
        "text": "#0f172a",
        "muted": "#64748b",
        "shadow": "0 10px 24px rgba(15, 23, 42, 0.08)",
        "plot_bg": "rgba(255,255,255,0)",
        "grid": "rgba(100, 116, 139, 0.14)",
    },
}


def _get_theme() -> dict:
    """Return the currently selected theme palette."""
    mode = "light" if st.session_state.get("light_mode", False) else "dark"
    return THEMES[mode]


def _health_tier_color(score: int, theme: dict) -> str:
    """Return the semantic color for a score band."""
    if score >= 70:
        return theme["success"]
    if score >= 50:
        return theme["warning"]
    return theme["danger"]


def _default_weekly_payload() -> dict:
    """Fallback weekly payload when the weekly endpoint is unavailable."""
    return {
        "range": {"start": "", "end": ""},
        "bugs_this_week": 0,
        "developers": [],
        "testers": [],
        "daily_breakdown": {},
    }


def _resolve_api_base_url() -> str:
    """Resolve backend base URL for dashboard actions."""
    settings = load_settings()
    return (settings.api_base_url or "http://api:8000").strip().rstrip("/")


def _api_headers() -> dict[str, str]:
    """Build headers for backend API requests."""
    settings = load_settings()
    token = st.session_state.get("access_token")
    headers = {"X-API-KEY": settings.api_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _load_snapshot() -> dict:
    """Load sprint health snapshot and activity payloads from backend API only."""
    base = _resolve_api_base_url()
    headers = _api_headers()
    score_url = f"{base}/health-score"
    activity_url = f"{base}/activity"
    weekly_url = f"{base}/activity/weekly"
    logger.info("Fetching dashboard data from backend API")

    score_response = requests.get(score_url, headers=headers, timeout=10)
    score_response.raise_for_status()
    logger.info("Health score API returned status=%s", score_response.status_code)
    activity_response = requests.get(activity_url, headers=headers, timeout=10)
    activity_response.raise_for_status()
    logger.info("Daily activity API returned status=%s bytes=%s", activity_response.status_code, len(activity_response.text))

    weekly_data = _default_weekly_payload()
    try:
        weekly_response = requests.get(weekly_url, headers=headers, timeout=10)
        weekly_response.raise_for_status()
        weekly_data = weekly_response.json()
        logger.info("Weekly activity API returned status=%s bytes=%s", weekly_response.status_code, len(weekly_response.text))
    except Exception as exc:
        logger.warning("Weekly activity endpoint unavailable: %s", exc)

    score_data = score_response.json()
    activity_data = activity_response.json()
    if not activity_data:
        logger.warning("Daily activity response was empty")
    if not weekly_data or weekly_data == _default_weekly_payload():
        logger.warning("Weekly activity response was empty or defaulted")

    return {
        "score": score_data["score"],
        "completion_rate": score_data["completion_rate"],
        "breakdown": score_data["breakdown"],
        "activity": activity_data,
        "weekly_activity": weekly_data,
    }


def _get_snapshot(force_refresh: bool = False) -> dict:
    """Load or reuse dashboard snapshot from session state."""
    if force_refresh or "dashboard_snapshot" not in st.session_state:
        st.session_state["dashboard_snapshot"] = _load_snapshot()
    return st.session_state["dashboard_snapshot"]


def _run_now_and_refresh() -> None:
    """Trigger backend refresh and update cached dashboard data immediately."""
    st.session_state["run_now_loading"] = True
    st.session_state.pop("run_now_success", None)
    st.session_state.pop("run_now_error", None)
    base_url = _resolve_api_base_url()
    try:
        with st.spinner("Fetching latest data from Jira..."):
            response = requests.post(f"{base_url}/run", headers=_api_headers(), timeout=10)
            response.raise_for_status()
            logger.info("Run endpoint returned status=%s", response.status_code)
            st.session_state.pop("dashboard_snapshot", None)
            st.session_state["force_snapshot_refresh"] = True
            st.cache_data.clear()
        st.session_state["run_now_success"] = "Report updated successfully"
        st.rerun()
    except (requests.Timeout, requests.ConnectionError, requests.RequestException) as exc:
        logger.warning("Run Now failed: %s", exc)
        st.session_state["run_now_error"] = "Failed to update report"
    finally:
        st.session_state["run_now_loading"] = False


def _inject_base_styles(theme: dict) -> None:
    """Apply the design system and layout rules."""
    st.markdown(
        f"""
        <style>
            :root {{
                --primary: {theme["primary"]};
                --primary-soft: {theme["primary_soft"]};
                --success: {theme["success"]};
                --warning: {theme["warning"]};
                --danger: {theme["danger"]};
                --info: {theme["info"]};
                --bg: {theme["bg"]};
                --bg-alt: {theme["bg_alt"]};
                --surface: {theme["surface"]};
                --surface-alt: {theme["surface_alt"]};
                --border: {theme["border"]};
                --text: {theme["text"]};
                --muted: {theme["muted"]};
                --shadow: {theme["shadow"]};
            }}
            .stApp {{
                background: linear-gradient(180deg, var(--bg-alt) 0%, var(--bg) 100%);
                color: var(--text);
            }}
            .block-container {{
                max-width: 1320px;
                padding-top: 24px;
                padding-bottom: 56px;
                padding-left: 24px;
                padding-right: 24px;
            }}
            header[data-testid="stHeader"],
            div[data-testid="stToolbar"] {{
                background: transparent;
            }}
            div[data-testid="stButton"] > button,
            div[data-testid="stDownloadButton"] > button {{
                min-height: 44px;
                border-radius: 14px;
                border: 1px solid var(--border);
                background: var(--surface);
                color: var(--text);
                font-weight: 600;
                box-shadow: none;
                transition: border-color 120ms ease, color 120ms ease, background 120ms ease;
            }}
            div[data-testid="stButton"] > button:hover,
            div[data-testid="stDownloadButton"] > button:hover {{
                border-color: var(--primary);
                color: var(--primary);
            }}
            div[data-testid="stButton"] > button[kind="primary"] {{
                background: var(--primary);
                color: white;
                border-color: var(--primary);
            }}
            div[data-testid="stButton"] > button[kind="primary"]:hover {{
                background: var(--info);
                color: white;
            }}
            div[data-testid="stToggle"] label,
            div[data-testid="stToggle"] p {{
                color: var(--text) !important;
                font-weight: 500;
            }}
            .hero-card {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 18px;
                padding: 24px;
                box-shadow: var(--shadow);
                margin-top: 8px;
            }}
            .hero-overline {{
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.12em;
                font-weight: 700;
                color: var(--muted);
                margin-bottom: 8px;
            }}
            .hero-title {{
                font-size: 20px;
                font-weight: 700;
                color: var(--text);
                margin-bottom: 8px;
            }}
            .hero-subtitle {{
                max-width: 700px;
                font-size: 14px;
                line-height: 1.55;
                color: var(--muted);
            }}
            .hero-score {{
                margin: 20px 0 10px 0;
                font-size: clamp(56px, 8vw, 92px);
                line-height: 0.92;
                letter-spacing: -0.05em;
                font-weight: 800;
                color: var(--text);
            }}
            .hero-badge {{
                display: inline-flex;
                align-items: center;
                padding: 7px 12px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 700;
                margin-top: 12px;
            }}
            .section-heading {{
                margin: 40px 0 14px 0;
            }}
            .section-heading h2 {{
                margin: 0 0 6px 0;
                font-size: 18px;
                font-weight: 700;
                color: var(--text);
            }}
            .section-heading p {{
                margin: 0;
                max-width: 720px;
                font-size: 13px;
                line-height: 1.5;
                color: var(--muted);
            }}
            .section-divider {{
                height: 1px;
                background: var(--border);
                margin: 0 0 16px 0;
            }}
            .metric-card,
            .content-card {{
                height: 100%;
                min-height: 100%;
                padding: 18px;
                border-radius: 18px;
                border: 1px solid var(--border);
                background: var(--surface);
                box-shadow: var(--shadow);
            }}
            .metric-label {{
                margin-bottom: 10px;
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--muted);
            }}
            .metric-value {{
                margin-bottom: 8px;
                font-size: 32px;
                line-height: 1.05;
                letter-spacing: -0.03em;
                font-weight: 800;
                color: var(--text);
            }}
            .metric-subtext {{
                font-size: 13px;
                line-height: 1.55;
                color: var(--muted);
            }}
            .table-title {{
                margin-bottom: 14px;
                font-size: 15px;
                font-weight: 700;
                color: var(--text);
            }}
            .saas-table {{
                width: 100%;
                border-collapse: collapse;
            }}
            .saas-table th {{
                padding-bottom: 12px;
                text-align: left;
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--muted);
                border-bottom: 1px solid var(--border);
            }}
            .saas-table td {{
                padding: 14px 0;
                font-size: 14px;
                color: var(--text);
                border-bottom: 1px solid rgba(148, 163, 184, 0.10);
                vertical-align: top;
            }}
            .saas-table tr:last-child td {{
                border-bottom: none;
            }}
            .empty-state {{
                padding-top: 4px;
                font-size: 14px;
                line-height: 1.6;
                color: var(--muted);
            }}
            .insight-list {{
                margin: 0;
                padding-left: 18px;
            }}
            .insight-list li {{
                margin-bottom: 10px;
                line-height: 1.55;
                color: var(--muted);
            }}
            .context-chip {{
                display: inline-flex;
                align-items: center;
                padding: 6px 10px;
                border-radius: 999px;
                border: 1px solid var(--border);
                background: transparent;
                color: var(--muted);
                font-size: 12px;
                font-weight: 700;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_section_header(title: str, subtitle: str) -> None:
    """Render a section heading with a divider."""
    st.markdown(
        f"""
        <div class="section-heading">
            <h2>{html.escape(title)}</h2>
            <p>{html.escape(subtitle)}</p>
        </div>
        <div class="section-divider"></div>
        """,
        unsafe_allow_html=True,
    )


def _metric_card_html(label: str, value: str, subtext: str, accent: str) -> str:
    """Render a metric card."""
    return f"""
    <div class="metric-card">
        <div class="metric-label">{html.escape(label)}</div>
        <div class="metric-value" style="color:{accent};">{value}</div>
        <div class="metric-subtext">{html.escape(subtext)}</div>
    </div>
    """


def _breakdown_card_html(title: str, value: int, theme: dict) -> str:
    """Render one breakdown score card."""
    return _metric_card_html(title, str(value), "Signal health score", _health_tier_color(value, theme))


def _table_card_html(title: str, rows: list[dict], columns: list[tuple[str, str]]) -> str:
    """Render a styled table card."""
    if not rows:
        return f"""
        <div class="content-card">
            <div class="table-title">{html.escape(title)}</div>
            <div class="empty-state">No activity recorded for this section yet.</div>
        </div>
        """

    head_html = "".join(f"<th>{html.escape(label)}</th>" for _, label in columns)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(key, 0)))}</td>" for key, _ in columns) + "</tr>"
        for row in rows
    )
    return f"""
    <div class="content-card">
        <div class="table-title">{html.escape(title)}</div>
        <table class="saas-table">
            <thead><tr>{head_html}</tr></thead>
            <tbody>{body_html}</tbody>
        </table>
    </div>
    """


def _insights_card_html(insights: list[str]) -> str:
    """Render the insights panel."""
    if not insights:
        return """
        <div class="content-card">
            <div class="table-title">Activity insights</div>
            <div class="empty-state">No insights available yet.</div>
        </div>
        """

    items = "".join(f"<li>{html.escape(item)}</li>" for item in insights)
    return f"""
    <div class="content-card">
        <div class="table-title">Activity insights</div>
        <ul class="insight-list">{items}</ul>
    </div>
    """


def _summary_card_html(title: str, value: str, subtext: str) -> str:
    """Render a compact summary card."""
    return f"""
    <div class="content-card">
        <div class="table-title">{html.escape(title)}</div>
        <div class="metric-value" style="font-size:28px;">{value}</div>
        <div class="metric-subtext">{html.escape(subtext)}</div>
    </div>
    """


def _build_breakdown_plotly(breakdown: dict, theme: dict) -> go.Figure:
    """Create the themed breakdown chart."""
    labels = ["Commitment", "Carryover", "Cycle time", "Bug ratio"]
    values = [int(breakdown["commitment"]), int(breakdown["carryover"]), int(breakdown["cycle_time"]), int(breakdown["bug_ratio"])]
    colors = [_health_tier_color(value, theme) for value in values]

    fig = go.Figure(
        data=[
            go.Bar(
                x=labels,
                y=values,
                marker=dict(color=colors, line=dict(color=theme["border"], width=1)),
                text=[str(value) for value in values],
                textposition="outside",
                textfont=dict(color=theme["text"], size=12),
                hovertemplate="%{x}: %{y}<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        paper_bgcolor=theme["plot_bg"],
        plot_bgcolor=theme["plot_bg"],
        font=dict(color=theme["muted"], family="Inter, Segoe UI, sans-serif"),
        margin=dict(l=20, r=20, t=24, b=24),
        height=340,
        showlegend=False,
        xaxis=dict(showgrid=False, tickfont=dict(color=theme["muted"])),
        yaxis=dict(range=[0, 105], gridcolor=theme["grid"], zeroline=False, tickfont=dict(color=theme["muted"])),
    )
    return fig


def _build_weekly_plotly(weekly: dict, theme: dict) -> go.Figure | None:
    """Build the weekly trend chart when data exists."""
    breakdown = weekly.get("daily_breakdown") or {}
    if not breakdown:
        return None

    days = list(breakdown.keys())
    bugs = [int((breakdown.get(day) or {}).get("bugs_created", 0)) for day in days]
    worked = [int((breakdown.get(day) or {}).get("tasks_worked", 0)) for day in days]
    completed = [int((breakdown.get(day) or {}).get("tasks_completed", 0)) for day in days]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=days,
            y=bugs,
            name="Bugs",
            marker_color=theme["danger"],
            opacity=0.82,
            hovertemplate="%{x}: %{y} bugs<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=days,
            y=worked,
            name="Tasks worked",
            mode="lines+markers",
            line=dict(color=theme["info"], width=2.5),
            marker=dict(size=7),
            hovertemplate="%{x}: %{y} tasks worked<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=days,
            y=completed,
            name="Tasks completed",
            mode="lines+markers",
            line=dict(color=theme["success"], width=2.5),
            marker=dict(size=7),
            hovertemplate="%{x}: %{y} tasks completed<extra></extra>",
        )
    )
    fig.update_layout(
        barmode="group",
        paper_bgcolor=theme["plot_bg"],
        plot_bgcolor=theme["plot_bg"],
        font=dict(color=theme["muted"], family="Inter, Segoe UI, sans-serif"),
        margin=dict(l=20, r=20, t=24, b=24),
        height=360,
        legend=dict(orientation="h", y=1.1, x=0, bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=False, tickfont=dict(color=theme["muted"])),
        yaxis=dict(gridcolor=theme["grid"], zeroline=False, tickfont=dict(color=theme["muted"])),
    )
    return fig


def _render_login_screen():
    theme = _get_theme()
    _inject_base_styles(theme)
    st.markdown("<h2 style='text-align: center; margin-top: 5rem; color: var(--text);'>Sprint Health Login</h2>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.form("login_form"):
            email = st.text_input("Email", placeholder="admin@lumofy.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", type="primary", use_container_width=True)
            if submitted:
                if not email or not password:
                    st.error("Please enter email and password.")
                    return
                base = _resolve_api_base_url()
                try:
                    resp = requests.post(f"{base}/auth/login", json={"email": email, "password": password}, timeout=5)
                    if resp.status_code == 200:
                        st.session_state["access_token"] = resp.json()["access_token"]
                        st.rerun()
                    else:
                        st.error("Invalid credentials or account locked.")
                except requests.RequestException:
                    st.error("Cannot reach authentication service.")


def main() -> None:
    """Render sprint health dashboard UI."""
    st.set_page_config(page_title="Sprint Health Dashboard", layout="wide")
    
    if "access_token" not in st.session_state:
        _render_login_screen()
        return
    if not st.session_state.get("dashboard_initialized"):
        logger.info("Dashboard session initialized")
        st.session_state["dashboard_initialized"] = True
    st.session_state.setdefault("light_mode", False)
    st.session_state.setdefault("run_now_loading", False)

    theme = _get_theme()
    _inject_base_styles(theme)

    top_left, top_middle, top_right = st.columns([5, 1.4, 1], gap="large")
    with top_left:
        st.markdown('<div class="context-chip">Sprint analytics workspace</div>', unsafe_allow_html=True)
    with top_middle:
        if st.button(
            "Run now",
            type="primary",
            use_container_width=True,
            disabled=bool(st.session_state.get("run_now_loading")),
        ):
            _run_now_and_refresh()
    with top_right:
        st.toggle("Light mode", key="light_mode")
        if st.button("Logout"):
            base = _resolve_api_base_url()
            token = st.session_state.get("access_token")
            if token:
                try:
                    requests.post(f"{base}/auth/logout", headers={"Authorization": f"Bearer {token}"}, timeout=5)
                except Exception:
                    pass
            st.session_state.pop("access_token", None)
            st.rerun()

    theme = _get_theme()
    _inject_base_styles(theme)

    if st.session_state.get("run_now_success"):
        st.success(str(st.session_state["run_now_success"]))
    if st.session_state.get("run_now_error"):
        st.error(str(st.session_state["run_now_error"]))

    try:
        force_refresh = bool(st.session_state.pop("force_snapshot_refresh", False))
        if force_refresh or "dashboard_snapshot" not in st.session_state:
            with st.spinner("Loading dashboard..."):
                snapshot = _get_snapshot(force_refresh=True)
        else:
            snapshot = _get_snapshot()
    except Exception as exc:
        st.error(f"Could not load sprint health data: {exc}")
        return

    score = int(snapshot["score"])
    completion_rate = float(snapshot["completion_rate"])
    breakdown = snapshot["breakdown"]
    activity = snapshot.get("activity") or {
        "developers": [],
        "testers": [],
        "bugs_today": 0,
        "top_developer": {"name": "", "completed": 0},
        "top_tester": {"name": "", "bugs_closed": 0},
        "insights": [],
    }
    weekly = snapshot.get("weekly_activity") or _default_weekly_payload()
    api_base_url = _resolve_api_base_url()

    status_color = _health_tier_color(score, theme)
    if score >= 70:
        status_text = "Healthy sprint"
    elif score >= 50:
        status_text = "Needs attention"
    else:
        status_text = "At risk"

    st.markdown(
        f"""
        <div class="hero-card">
            <div class="hero-overline">Sprint Health Platform</div>
            <div class="hero-title">A focused view of delivery quality, execution pace, and team activity.</div>
            <div class="hero-score" style="color:{status_color};">{score}<span style="font-size:34px;color:{theme["muted"]};">/100</span></div>
            <div class="hero-subtitle">
                Track how the sprint is progressing across commitment, carryover, bugs, and execution trends with a clean operational dashboard.
            </div>
            <div class="hero-badge" style="background:{theme["primary_soft"]};color:{status_color};border:1px solid {theme["border"]};">
                {status_text}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(f"API source: {api_base_url}")
    if not activity.get("developers") and not activity.get("testers") and int(activity.get("bugs_today", 0) or 0) == 0:
        st.info("Activity API returned no current daily activity data.")
    if not weekly.get("daily_breakdown"):
        st.info("Weekly activity API returned no current weekly data.")

    _render_section_header("Metrics Overview", "Key operational indicators for the sprint and the current activity window.")
    weekly_bugs = int(weekly.get("bugs_this_week", 0) or 0)
    metrics_cols = st.columns(3, gap="large")
    with metrics_cols[0]:
        st.markdown(
            _metric_card_html("Completion rate", f"{completion_rate:.1f}%", "Scope delivered versus committed", theme["primary"]),
            unsafe_allow_html=True,
        )
    with metrics_cols[1]:
        st.markdown(
            _metric_card_html("Bugs today", str(int(activity.get("bugs_today", 0))), "New bugs created in the current day", theme["danger"]),
            unsafe_allow_html=True,
        )
    with metrics_cols[2]:
        st.markdown(
            _metric_card_html("Weekly bugs", str(weekly_bugs), "Bugs created across the active work week", theme["warning"]),
            unsafe_allow_html=True,
        )

    _render_section_header("Score Breakdown", "The four scoring signals that shape the overall health score.")
    first_breakdown_row = st.columns(2, gap="large")
    second_breakdown_row = st.columns(2, gap="large")
    with first_breakdown_row[0]:
        st.markdown(_breakdown_card_html("Commitment", int(breakdown["commitment"]), theme), unsafe_allow_html=True)
    with first_breakdown_row[1]:
        st.markdown(_breakdown_card_html("Carryover", int(breakdown["carryover"]), theme), unsafe_allow_html=True)
    with second_breakdown_row[0]:
        st.markdown(_breakdown_card_html("Bug ratio", int(breakdown["bug_ratio"]), theme), unsafe_allow_html=True)
    with second_breakdown_row[1]:
        st.markdown(_breakdown_card_html("Cycle time", int(breakdown["cycle_time"]), theme), unsafe_allow_html=True)

    st.plotly_chart(_build_breakdown_plotly(breakdown, theme), use_container_width=True, config={"displayModeBar": False})

    _render_section_header("Daily Activity", "Developer and QA activity for the current day.")
    daily_cols = st.columns(3, gap="large")
    top_dev = activity.get("top_developer") or {}
    top_tester = activity.get("top_tester") or {}
    with daily_cols[0]:
        st.markdown(
            _summary_card_html("Bugs today", str(int(activity.get("bugs_today", 0))), "Strictly counts bugs created today in the configured local timezone."),
            unsafe_allow_html=True,
        )
        st.markdown(
            _summary_card_html(
                "Top developer",
                html.escape(str(top_dev.get("name") or "No standout yet")),
                f"{int(top_dev.get('completed', 0) or 0)} tasks completed today",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            _summary_card_html(
                "Top tester",
                html.escape(str(top_tester.get("name") or "No standout yet")),
                f"{int(top_tester.get('bugs_closed', 0) or 0)} bugs closed today",
            ),
            unsafe_allow_html=True,
        )
    with daily_cols[1]:
        st.markdown(
            _table_card_html(
                "Developer activity",
                activity.get("developers") or [],
                [("name", "Developer"), ("tasks", "Tasks worked"), ("completed", "Completed")],
            ),
            unsafe_allow_html=True,
        )
    with daily_cols[2]:
        st.markdown(
            _table_card_html(
                "QA activity",
                activity.get("testers") or [],
                [("name", "Tester"), ("bugs_logged", "Bugs logged"), ("bugs_closed", "Bugs closed")],
            ),
            unsafe_allow_html=True,
        )

    st.markdown(_insights_card_html(activity.get("insights") or []), unsafe_allow_html=True)

    _render_section_header("Weekly Activity", "Work-week trends and throughput across the current Sunday to Thursday window.")
    weekly_range = weekly.get("range") or {}
    range_note = "Weekly activity endpoint unavailable."
    if weekly_range.get("start") and weekly_range.get("end"):
        range_note = f"Current work week: {weekly_range['start']} to {weekly_range['end']}"
    st.markdown(f'<div class="context-chip">{html.escape(range_note)}</div>', unsafe_allow_html=True)

    weekly_summary_cols = st.columns(3, gap="large")
    weekly_developers = weekly.get("developers") or []
    weekly_testers = weekly.get("testers") or []
    with weekly_summary_cols[0]:
        st.markdown(
            _summary_card_html("Bugs this week", str(weekly_bugs), "Created during the active work week only."),
            unsafe_allow_html=True,
        )
    with weekly_summary_cols[1]:
        st.markdown(
            _summary_card_html(
                "Developer throughput",
                str(sum(int(row.get("completed", 0)) for row in weekly_developers)),
                "Total completed tasks this work week.",
            ),
            unsafe_allow_html=True,
        )
    with weekly_summary_cols[2]:
        st.markdown(
            _summary_card_html(
                "QA throughput",
                str(sum(int(row.get("bugs_closed", 0)) for row in weekly_testers)),
                "Total bugs verified or closed this work week.",
            ),
            unsafe_allow_html=True,
        )

    weekly_chart = _build_weekly_plotly(weekly, theme)
    if weekly_chart is not None:
        st.plotly_chart(weekly_chart, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown(
            """
            <div class="content-card">
                <div class="table-title">Weekly trend</div>
                <div class="empty-state">Weekly activity data is not available yet. The layout stays ready and will populate automatically once the weekly endpoint is online.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    weekly_table_cols = st.columns(2, gap="large")
    with weekly_table_cols[0]:
        st.markdown(
            _table_card_html(
                "Weekly developer stats",
                weekly_developers,
                [("name", "Developer"), ("tasks", "Tasks worked"), ("completed", "Completed")],
            ),
            unsafe_allow_html=True,
        )
    with weekly_table_cols[1]:
        st.markdown(
            _table_card_html(
                "Weekly QA stats",
                weekly_testers,
                [("name", "Tester"), ("bugs_logged", "Bugs logged"), ("bugs_closed", "Bugs closed")],
            ),
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
