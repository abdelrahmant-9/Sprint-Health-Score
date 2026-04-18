"""Streamlit dashboard for sprint health monitoring."""

from __future__ import annotations

import base64
import html
import json
import logging
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components

from app.config import load_settings


logger = logging.getLogger(__name__)

def _decode_jwt(token: str) -> dict:
    try:
        b64 = token.split(".")[1]
        b64 += "=" * ((4 - len(b64) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(b64))
    except Exception:
        return {}

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

USER_ROLE_OPTIONS = [
    ("super_admin", "Super Admin"),
    ("admin", "Admin"),
    ("editor", "Editor"),
    ("user", "User"),
    ("viewer", "Viewer"),
]


def _get_theme() -> dict:
    """Return the currently selected theme palette."""
    mode = "light" if st.session_state.get("light_mode", False) else "dark"
    return THEMES[mode]


def _health_tier_color(score: int, theme: dict) -> str:
    """Return the semantic color for a score band."""
    if score >= 85:
        return theme["success"]
    if score >= 70:
        return theme["info"]
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
    user_state = st.session_state.get("user") or {}
    token = user_state.get("token") or st.session_state.get("access_token")
    headers = {"X-API-KEY": settings.api_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_request(method: str, path: str, **kwargs) -> requests.Response:
    """Send an authenticated API request to the backend."""
    headers = kwargs.pop("headers", {})
    merged_headers = _api_headers()
    merged_headers.update(headers)
    return requests.request(
        method,
        f"{_resolve_api_base_url()}{path}",
        headers=merged_headers,
        timeout=10,
        **kwargs,
    )


def _extract_api_error(response: requests.Response) -> str:
    """Return the most useful error message from an API response."""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, list):
            messages: list[str] = []
            for item in detail:
                if isinstance(item, dict):
                    messages.append(str(item.get("msg") or item))
                else:
                    messages.append(str(item))
            if messages:
                return "; ".join(messages)
        if detail:
            return str(detail)
        if payload.get("message"):
            return str(payload["message"])

    body = response.text.strip()
    if body:
        return body
    return f"Request failed with status {response.status_code}"


def _role_label(role: str) -> str:
    """Return a human-friendly label for a role value."""
    for value, label in USER_ROLE_OPTIONS:
        if value == role:
            return label
    return role.replace("_", " ").title()


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp safely."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_locked_account(user_row: dict) -> bool:
    """Return True when the account is currently locked."""
    locked_until = _parse_timestamp(user_row.get("locked_until"))
    return bool(locked_until and locked_until > datetime.now(timezone.utc))


def _format_timestamp(value: str | None) -> str:
    """Format timestamps for the admin UI."""
    parsed = _parse_timestamp(value)
    if not parsed:
        return "Never"
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fetch_users() -> list[dict]:
    """Load users from the backend API."""
    response = _api_request("GET", "/auth/users")
    if not response.ok:
        raise RuntimeError(_extract_api_error(response))
    payload = response.json()
    if isinstance(payload, dict):
        return list(payload.get("users", []))
    return list(payload)


def _set_admin_feedback(kind: str, message: str) -> None:
    """Persist a one-shot admin panel flash message."""
    st.session_state["admin_feedback"] = {"kind": kind, "message": message}


def _show_admin_feedback() -> None:
    """Render and clear any pending admin panel flash message."""
    feedback = st.session_state.pop("admin_feedback", None)
    if not feedback:
        return
    kind = str(feedback.get("kind") or "info").lower()
    message = str(feedback.get("message") or "")
    if kind == "success":
        st.success(message)
    elif kind == "error":
        st.error(message)
    elif kind == "warning":
        st.warning(message)
    else:
        st.info(message)


def _submit_user_create(email: str, password: str, role: str) -> None:
    """Create a new user via the backend API."""
    response = _api_request(
        "POST",
        "/auth/users",
        json={"email": email, "password": password, "role": role},
    )
    if not response.ok:
        raise RuntimeError(_extract_api_error(response))
    payload = response.json()
    _set_admin_feedback("success", str(payload.get("message") or f"Created {email}"))


def _submit_user_role_update(user_id: int, role: str) -> None:
    """Update a user's role via the backend API."""
    response = _api_request("PUT", f"/auth/users/{user_id}/role", json={"role": role})
    if not response.ok:
        raise RuntimeError(_extract_api_error(response))
    payload = response.json()
    _set_admin_feedback("success", str(payload.get("message") or "User role updated"))


def _submit_user_lock_change(user_id: int, *, locked: bool) -> None:
    """Lock or unlock a user account via the backend API."""
    action = "unlock" if locked else "lock"
    response = _api_request("PUT", f"/auth/users/{user_id}/{action}")
    if not response.ok:
        raise RuntimeError(_extract_api_error(response))
    payload = response.json()
    _set_admin_feedback("success", str(payload.get("message") or f"User {action}ed"))


def _submit_user_delete(user_id: int) -> None:
    """Delete a user account via the backend API."""
    response = _api_request("DELETE", f"/auth/users/{user_id}")
    if not response.ok:
        raise RuntimeError(_extract_api_error(response))
    payload = response.json()
    _set_admin_feedback("success", str(payload.get("message") or "User deleted"))


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_metrics_catalog_cached(base_url: str, api_key: str, token: str | None) -> list[dict]:
    """Return editable metrics with a short-lived cache for admin workflows."""
    headers = {"X-API-KEY": api_key, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(f"{base_url}/metrics?format=json", headers=headers, timeout=10)
    response.raise_for_status()
    payload = response.json()
    return list(payload if isinstance(payload, list) else payload.get("metrics", []))


def _fetch_metrics_catalog() -> list[dict]:
    """Fetch editable metrics for the current user session."""
    settings = load_settings()
    user_state = st.session_state.get("user") or {}
    token = user_state.get("token") or st.session_state.get("access_token")
    return _fetch_metrics_catalog_cached(_resolve_api_base_url(), settings.api_key, token)


def _update_metric_value(metric_name: str, value: float) -> None:
    """Persist a metric override and invalidate dashboard caches."""
    response = _api_request("PUT", f"/metrics/{metric_name}", json={"value": float(value)})
    if not response.ok:
        raise RuntimeError(_extract_api_error(response))
    payload = response.json()
    _fetch_metrics_catalog_cached.clear()
    st.session_state.pop("dashboard_snapshot", None)
    st.session_state["force_snapshot_refresh"] = True
    _set_admin_feedback("success", f"{payload.get('metric_name', metric_name)} updated")


def _enable_auto_refresh(interval_seconds: int = 30) -> None:
    """Inject a lightweight timed page reload to keep data fresh."""
    components.html(
        f"""
        <script>
        setTimeout(function() {{
            window.parent.location.reload();
        }}, {int(interval_seconds * 1000)});
        </script>
        """,
        height=0,
        width=0,
    )


def _build_trend_dataframe(weekly: dict) -> pd.DataFrame:
    """Build a dataframe for Plotly trend charts."""
    rows: list[dict] = []
    for day, payload in (weekly.get("daily_breakdown") or {}).items():
        rows.append({"date": day, "metric": "Bugs Created", "value": int((payload or {}).get("bugs_created", 0) or 0)})
        rows.append({"date": day, "metric": "Tasks Worked", "value": int((payload or {}).get("tasks_worked", 0) or 0)})
        rows.append({"date": day, "metric": "Tasks Completed", "value": int((payload or {}).get("tasks_completed", 0) or 0)})
    return pd.DataFrame(rows)


def _build_health_history_dataframe(history: list[dict]) -> pd.DataFrame:
    """Build a dataframe for sprint health trend charts."""
    rows: list[dict] = []
    for entry in history or []:
        rows.append(
            {
                "sprint": str(entry.get("sprint_name") or "Sprint"),
                "health_score": int(entry.get("health_score", 0) or 0),
                "commitment_score": int(entry.get("commitment_score", 0) or 0),
                "carryover_score": int(entry.get("carryover_score", 0) or 0),
                "cycle_time_score": int(entry.get("cycle_time_score", 0) or 0),
                "bug_score": int(entry.get("bug_score", 0) or 0),
            }
        )
    return pd.DataFrame(rows)


def _load_snapshot() -> dict:
    """Load sprint health snapshot and activity payloads from backend API only."""
    base = _resolve_api_base_url()
    headers = _api_headers()
    score_url = f"{base}/health-score"
    activity_url = f"{base}/activity"
    weekly_url = f"{base}/activity/weekly"
    metrics_url = f"{base}/metrics?format=json"
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

    metrics_catalog: list[dict] = []
    try:
        metrics_response = requests.get(metrics_url, headers={**headers, "Accept": "application/json"}, timeout=10)
        metrics_response.raise_for_status()
        metrics_catalog = metrics_response.json()
        logger.info("Editable metrics API returned status=%s items=%s", metrics_response.status_code, len(metrics_catalog))
    except Exception as exc:
        logger.warning("Editable metrics endpoint unavailable: %s", exc)

    score_data = score_response.json()
    activity_data = activity_response.json()
    if not activity_data:
        logger.warning("Daily activity response was empty")
    if not weekly_data or weekly_data == _default_weekly_payload():
        logger.warning("Weekly activity response was empty or defaulted")

    return {
        "score": score_data["score"],
        "health_score": score_data.get("health_score", score_data["score"]),
        "health_status": score_data.get("health_status", ""),
        "completion_rate": score_data["completion_rate"],
        "breakdown": score_data["breakdown"],
        "commitment_score": score_data.get("commitment_score", score_data["breakdown"].get("commitment", 0)),
        "carryover_score": score_data.get("carryover_score", score_data["breakdown"].get("carryover", 0)),
        "cycle_time_score": score_data.get("cycle_time_score", score_data["breakdown"].get("cycle_time", 0)),
        "bug_score": score_data.get("bug_score", score_data["breakdown"].get("bug_ratio", 0)),
        "cycle_time": score_data.get("cycle_time") or {},
        "blocked_ratio": float(score_data.get("blocked_ratio", 0.0) or 0.0),
        "bugs": score_data.get("bugs") or {},
        "insights": score_data.get("insights") or [],
        "summary": score_data.get("summary") or "",
        "prediction": score_data.get("prediction") or {},
        "history": score_data.get("history") or [],
        "activity": activity_data,
        "weekly_activity": weekly_data,
        "metrics_catalog": metrics_catalog,
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
            [data-testid="stMetric"] {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 18px;
                padding: 16px;
                box-shadow: var(--shadow);
            }}
            [data-testid="stMetricLabel"],
            [data-testid="stMetricValue"],
            [data-testid="stMetricDelta"] {{
                color: var(--text) !important;
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
                        token = resp.json()["access_token"]
                        payload = _decode_jwt(token)
                        try:
                            user_id = int(payload.get("sub"))
                        except (TypeError, ValueError):
                            user_id = None
                        st.session_state["user"] = {
                            "id": user_id,
                            "email": payload.get("email"),
                            "role": payload.get("role"),
                            "token": token
                        }
                        st.session_state.pop("access_token", None)
                        st.rerun()
                    else:
                        st.error("Invalid credentials or account locked.")
                except requests.RequestException:
                    st.error("Cannot reach authentication service.")


def _render_admin_dashboard(user: dict):
    if user.get("role") not in ["admin", "super_admin"]:
        st.error("Access denied")
        st.stop()
    
    st.markdown("<h2 style='color: var(--text);'>Admin Control Panel</h2>", unsafe_allow_html=True)
    st.caption("Review accounts, roles, login health, and access permissions.")
    _show_admin_feedback()

    try:
        users = _fetch_users()
    except Exception as exc:
        st.error(f"Cannot fetch users: {exc}")
        return

    display_rows = []
    for user_row in users:
        locked = _is_locked_account(user_row)
        display_rows.append(
            {
                "Email": user_row.get("email", ""),
                "Role": _role_label(str(user_row.get("role", ""))),
                "Last Login": _format_timestamp(user_row.get("last_login_at")),
                "Failed Attempts": int(user_row.get("failed_attempts", 0) or 0),
                "Locked": "Yes" if locked else "No",
                "Locked Until": _format_timestamp(user_row.get("locked_until")) if locked else "Active",
            }
        )

    st.subheader("Users Table")
    st.dataframe(display_rows, use_container_width=True, hide_index=True)

    if user.get("role") != "super_admin":
        st.info("Admins can view users only. Only super_admin can create, delete, change roles, or lock accounts.")
        return

    with st.form("create_user_form", clear_on_submit=True):
        st.subheader("Create User")
        create_cols = st.columns(3)
        with create_cols[0]:
            email = st.text_input("Email", placeholder="user@example.com")
        with create_cols[1]:
            password = st.text_input("Password", type="password", placeholder="At least 6 characters")
        with create_cols[2]:
            role = st.selectbox(
                "Role",
                [value for value, _ in USER_ROLE_OPTIONS],
                index=3,
                format_func=_role_label,
            )
        submitted = st.form_submit_button("Create User", type="primary", use_container_width=True)
        if submitted:
            try:
                _submit_user_create(email, password, role)
            except Exception as exc:
                _set_admin_feedback("error", str(exc))
            st.rerun()

    st.subheader("User Actions")
    current_user_id = user.get("id")
    role_options = [value for value, _ in USER_ROLE_OPTIONS]
    for user_row in users:
        account_id = int(user_row["id"])
        account_email = str(user_row.get("email", ""))
        account_role = str(user_row.get("role", "user"))
        account_locked = _is_locked_account(user_row)
        is_current_user = current_user_id == account_id
        default_role_index = role_options.index(account_role) if account_role in role_options else 0

        with st.container(border=True):
            summary_cols = st.columns([2.2, 1.2, 1.4, 1.1, 1.1])
            with summary_cols[0]:
                st.markdown(f"**{account_email}**")
                if is_current_user:
                    st.caption("Current session account")
            with summary_cols[1]:
                st.caption("Role")
                st.write(_role_label(account_role))
            with summary_cols[2]:
                st.caption("Last Login")
                st.write(_format_timestamp(user_row.get("last_login_at")))
            with summary_cols[3]:
                st.caption("Failed Attempts")
                st.write(int(user_row.get("failed_attempts", 0) or 0))
            with summary_cols[4]:
                st.caption("Status")
                st.write("Locked" if account_locked else "Active")

            action_cols = st.columns([2.2, 1.2, 1.2, 1.2])
            with action_cols[0]:
                selected_role = st.selectbox(
                    "Change Role",
                    role_options,
                    index=default_role_index,
                    format_func=_role_label,
                    key=f"user-role-{account_id}",
                    disabled=is_current_user,
                )
            with action_cols[1]:
                if st.button(
                    "Update Role",
                    key=f"user-role-update-{account_id}",
                    use_container_width=True,
                    disabled=is_current_user or selected_role == account_role,
                ):
                    try:
                        _submit_user_role_update(account_id, selected_role)
                    except Exception as exc:
                        _set_admin_feedback("error", str(exc))
                    st.rerun()
            with action_cols[2]:
                if st.button(
                    "Unlock" if account_locked else "Lock",
                    key=f"user-lock-toggle-{account_id}",
                    use_container_width=True,
                    disabled=is_current_user,
                ):
                    try:
                        _submit_user_lock_change(account_id, locked=account_locked)
                    except Exception as exc:
                        _set_admin_feedback("error", str(exc))
                    st.rerun()
            with action_cols[3]:
                if st.button(
                    "Delete",
                    key=f"user-delete-{account_id}",
                    use_container_width=True,
                    disabled=is_current_user,
                ):
                    try:
                        _submit_user_delete(account_id)
                    except Exception as exc:
                        _set_admin_feedback("error", str(exc))
                    st.rerun()


def _render_admin_metrics_dashboard(user: dict) -> None:
    """Render the editable metrics override view for admin users."""
    if user.get("role") not in ["admin", "super_admin"]:
        st.error("Access denied")
        st.stop()

    st.markdown("<h2 style='color: var(--text);'>Metrics Override Center</h2>", unsafe_allow_html=True)
    st.caption("Edit live metric overrides on top of the existing calculations in app/metrics.py.")
    _show_admin_feedback()

    try:
        metrics = _fetch_metrics_catalog()
    except Exception as exc:
        st.error(f"Cannot fetch editable metrics: {exc}")
        return

    if not metrics:
        st.info("No editable metrics are available yet.")
        return

    metric_df = pd.DataFrame(metrics)
    editable_df = metric_df[["metric_name", "value", "base_value", "override_value", "updated_at"]].copy()
    st.dataframe(metric_df, use_container_width=True, hide_index=True)
    edited_df = st.data_editor(
        editable_df,
        use_container_width=True,
        hide_index=True,
        disabled=["metric_name", "base_value", "override_value", "updated_at"],
        column_config={
            "metric_name": st.column_config.TextColumn("Metric", disabled=True),
            "value": st.column_config.NumberColumn("Effective Value", step=0.1),
            "base_value": st.column_config.NumberColumn("Base Value", disabled=True),
            "override_value": st.column_config.NumberColumn("Override Value", disabled=True),
            "updated_at": st.column_config.TextColumn("Updated At", disabled=True),
        },
    )

    st.caption("Admins and super_admins can change metric override values. Updates propagate into the dashboard snapshot after save.")
    if st.button("Save Metric Changes", type="primary", use_container_width=True):
        changed_rows = []
        for original_row, edited_row in zip(metric_df.to_dict("records"), edited_df.to_dict("records")):
            try:
                original_value = float(original_row.get("value") or 0.0)
                edited_value = float(edited_row.get("value") or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(original_value - edited_value) > 1e-9:
                changed_rows.append((str(original_row["metric_name"]), edited_value))

        if not changed_rows:
            _set_admin_feedback("info", "No metric changes detected.")
            st.rerun()

        errors: list[str] = []
        for metric_name, value in changed_rows:
            try:
                _update_metric_value(metric_name, value)
            except Exception as exc:
                errors.append(f"{metric_name}: {exc}")

        if errors:
            _set_admin_feedback("error", "; ".join(errors))
        else:
            _set_admin_feedback("success", "Metrics updated successfully.")
        st.rerun()

def main() -> None:
    """Render sprint health dashboard UI."""
    st.set_page_config(page_title="Sprint Health Dashboard", layout="wide")
    
    user = st.session_state.get("user")
    
    if not user and "access_token" not in st.session_state:
        _render_login_screen()
        return

    st.session_state.setdefault("current_view", "main")
    st.sidebar.title("Navigation")
    st.sidebar.caption(f"Signed in as {user.get('email', 'unknown')} ({_role_label(str(user.get('role', 'user')))})")
    
    if st.sidebar.button("Sprint Metrics", use_container_width=True):
        st.session_state["current_view"] = "main"
        
    if user and user.get("role") in ["admin", "super_admin"]:
        if st.sidebar.button("User Management", use_container_width=True):
            st.session_state["current_view"] = "admin"
        if st.sidebar.button("Admin Metrics", use_container_width=True):
            st.session_state["current_view"] = "admin_metrics"
    st.sidebar.toggle("Auto refresh (30s)", key="auto_refresh_dashboard")
            
    if st.session_state["current_view"] == "admin":
        _render_admin_dashboard(user)
        return
    if st.session_state["current_view"] == "admin_metrics":
        _render_admin_metrics_dashboard(user)
        return

    if not st.session_state.get("dashboard_initialized"):
        logger.info("Dashboard session initialized")
        st.session_state["dashboard_initialized"] = True
    st.session_state.setdefault("light_mode", False)
    st.session_state.setdefault("run_now_loading", False)

    theme = _get_theme()
    _inject_base_styles(theme)
    if st.session_state.get("auto_refresh_dashboard"):
        _enable_auto_refresh(30)

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
            user_state = st.session_state.get("user") or {}
            token = user_state.get("token") or st.session_state.get("access_token")
            if token:
                try:
                    requests.post(f"{base}/auth/logout", headers={"Authorization": f"Bearer {token}"}, timeout=5)
                except Exception:
                    pass
            st.session_state.pop("access_token", None)
            st.session_state.pop("user", None)
            st.session_state.pop("current_view", None)
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

    score = int(snapshot["health_score"])
    health_status = str(snapshot.get("health_status") or "")
    completion_rate = float(snapshot["completion_rate"])
    breakdown = snapshot["breakdown"]
    insights = snapshot.get("insights") or []
    summary = str(snapshot.get("summary") or "")
    prediction = snapshot.get("prediction") or {}
    history = snapshot.get("history") or []
    cycle_time_metrics = snapshot.get("cycle_time") or {}
    blocked_ratio = float(snapshot.get("blocked_ratio", 0.0) or 0.0)
    bug_metrics = snapshot.get("bugs") or {}
    activity = snapshot.get("activity") or {
        "developers": [],
        "testers": [],
        "bugs_today": 0,
        "top_developer": {"name": "", "completed": 0},
        "top_tester": {"name": "", "bugs_closed": 0},
        "insights": [],
    }
    weekly = snapshot.get("weekly_activity") or _default_weekly_payload()
    metrics_catalog = snapshot.get("metrics_catalog") or []
    api_base_url = _resolve_api_base_url()

    status_color = _health_tier_color(score, theme)
    if health_status == "Green":
        status_text = "Healthy sprint"
    elif health_status == "Yellow":
        status_text = "Watch closely"
    elif health_status == "Orange":
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
                {health_status or status_text}
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
    weekly_bugs = int(weekly.get("bugs_this_week", 0) or 0)
    weekly_developers = weekly.get("developers") or []
    weekly_testers = weekly.get("testers") or []
    top_dev = activity.get("top_developer") or {}
    top_tester = activity.get("top_tester") or {}
    metrics_by_name = {str(row.get("metric_name")): row for row in metrics_catalog}
    focus_filter = st.selectbox(
        "Team Filter",
        ["All Teams", "Developers", "QA", "Bugs"],
        index=0,
        help="Switch the dashboard lens without changing the underlying data.",
    )

    overview_tab, trends_tab, operations_tab = st.tabs(["Overview", "Trends", "Operations"])

    with overview_tab:
        metric_cols = st.columns(4, gap="large")
        with metric_cols[0]:
            st.metric("Sprint Score", f"{score}/100", delta=health_status or status_text)
        with metric_cols[1]:
            st.metric("Next Sprint", int(prediction.get("next_sprint_health", score) or score), delta=str(prediction.get("trend") or "stable"))
        with metric_cols[2]:
            st.metric("Blocked Ratio", f"{blocked_ratio:.1f}%")
        with metric_cols[3]:
            st.metric("Completion Rate", f"{completion_rate:.1f}%")

        if summary:
            st.info(summary)

        if insights:
            _render_section_header("AI Insights", "Deterministic root-cause analysis for the current sprint outcome.")
            for insight in insights:
                st.warning(insight)

        if metrics_catalog:
            _render_section_header("Editable Metric Layer", "Current effective metric values after applying database overrides.")
            editable_cols = st.columns(min(4, len(metrics_catalog[:4])) or 1, gap="large")
            for column, metric_row in zip(editable_cols, metrics_catalog[:4]):
                with column:
                    base_value = metric_row.get("base_value")
                    effective_value = metric_row.get("value")
                    delta_text = None
                    if base_value is not None and effective_value is not None:
                        try:
                            delta_text = f"{float(effective_value) - float(base_value):+.1f} override"
                        except (TypeError, ValueError):
                            delta_text = None
                    st.metric(
                        metric_row.get("metric_name", "metric").replace("_", " ").title(),
                        effective_value,
                        delta=delta_text,
                    )

        _render_section_header("Score Breakdown", "Power BI-style score cards and the weighted health distribution.")
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

        _render_section_header("Engineering Signals", "Advanced metrics behind the sprint health score.")
        signal_cols = st.columns(4, gap="large")
        with signal_cols[0]:
            st.metric("Story Cycle", cycle_time_metrics.get("story") if cycle_time_metrics.get("story") is not None else "N/A")
        with signal_cols[1]:
            st.metric("Bug Cycle", cycle_time_metrics.get("bug") if cycle_time_metrics.get("bug") is not None else "N/A")
        with signal_cols[2]:
            st.metric("Task Cycle", cycle_time_metrics.get("task") if cycle_time_metrics.get("task") is not None else "N/A")
        with signal_cols[3]:
            st.metric("Avg Bugs / Story", bug_metrics.get("avg_per_story", 0.0))

    with trends_tab:
        _render_section_header("Trend Analysis", "Weekly movement of bugs, delivery throughput, and sprint health.")
        weekly_range = weekly.get("range") or {}
        range_note = "Weekly activity endpoint unavailable."
        if weekly_range.get("start") and weekly_range.get("end"):
            range_note = f"Current work week: {weekly_range['start']} to {weekly_range['end']}"
        st.markdown(f'<div class="context-chip">{html.escape(range_note)}</div>', unsafe_allow_html=True)

        history_df = _build_health_history_dataframe(history)
        if not history_df.empty:
            st.subheader("Sprint Health Trend")
            st.line_chart(history_df.set_index("sprint")[["health_score"]])
            st.dataframe(
                history_df.rename(
                    columns={
                        "sprint": "Sprint",
                        "health_score": "Health",
                        "commitment_score": "Commitment",
                        "carryover_score": "Carryover",
                        "cycle_time_score": "Cycle Time",
                        "bug_score": "Bug",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        trend_df = _build_trend_dataframe(weekly)
        if not trend_df.empty:
            trend_fig = px.line(
                trend_df,
                x="date",
                y="value",
                color="metric",
                markers=True,
                line_shape="linear",
            )
            trend_fig.update_layout(
                paper_bgcolor=theme["plot_bg"],
                plot_bgcolor=theme["plot_bg"],
                font=dict(color=theme["muted"], family="Inter, Segoe UI, sans-serif"),
                margin=dict(l=20, r=20, t=20, b=20),
                legend=dict(orientation="h", y=1.1, x=0),
                xaxis=dict(showgrid=False),
                yaxis=dict(gridcolor=theme["grid"], zeroline=False),
            )
            st.plotly_chart(trend_fig, use_container_width=True, config={"displayModeBar": False})

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

    with operations_tab:
        _render_section_header("Operational Lens", "Switch the focus between developers, QA, bugs, and overall delivery.")
        daily_cols = st.columns(3, gap="large")
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
        if focus_filter in {"All Teams", "Developers"}:
            with daily_cols[1]:
                st.markdown(
                    _table_card_html(
                        "Developer activity",
                        activity.get("developers") or [],
                        [("name", "Developer"), ("tasks", "Tasks worked"), ("completed", "Completed")],
                    ),
                    unsafe_allow_html=True,
                )
        if focus_filter in {"All Teams", "QA"}:
            with daily_cols[2]:
                st.markdown(
                    _table_card_html(
                        "QA activity",
                        activity.get("testers") or [],
                        [("name", "Tester"), ("bugs_logged", "Bugs logged"), ("bugs_closed", "Bugs closed")],
                    ),
                    unsafe_allow_html=True,
                )
        if focus_filter == "Bugs":
            st.markdown(
                _summary_card_html("Bug Ratio Score", str(int(breakdown["bug_ratio"])), "Override-aware scoring input used by the health model."),
                unsafe_allow_html=True,
            )

        bug_classification = bug_metrics.get("classification") or {}
        advanced_cols = st.columns(3, gap="large")
        with advanced_cols[0]:
            st.markdown(
                _summary_card_html(
                    "Top bug engineer",
                    html.escape(str(bug_metrics.get("top_bug_engineer") or "N/A")),
                    "Engineer with the highest assigned bug count in the sprint.",
                ),
                unsafe_allow_html=True,
            )
        with advanced_cols[1]:
            st.markdown(
                _summary_card_html(
                    "Story bug owner",
                    html.escape(str(bug_metrics.get("most_story_bug_engineer") or "N/A")),
                    "Engineer with the most bugs related to sprint stories.",
                ),
                unsafe_allow_html=True,
            )
        with advanced_cols[2]:
            st.markdown(
                _summary_card_html(
                    "Bug sources",
                    f"{int(bug_classification.get('from_current_sprint_stories', 0) or 0)} linked / {int(bug_classification.get('external_bugs', 0) or 0)} external",
                    "Story-linked versus external bug intake for the sprint.",
                ),
                unsafe_allow_html=True,
            )

        st.markdown(_insights_card_html(activity.get("insights") or []), unsafe_allow_html=True)

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
