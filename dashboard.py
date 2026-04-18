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


def _health_label(score: int) -> str:
    """Return the dashboard label for a score band."""
    if score >= 85:
        return "Healthy"
    if score >= 70:
        return "Stable"
    if score >= 50:
        return "Watch"
    return "At Risk"


def _health_css_var(score: int) -> str:
    """Return the design-token CSS variable name for a score band."""
    if score >= 85:
        return "var(--score-green)"
    if score >= 70:
        return "var(--score-blue)"
    if score >= 50:
        return "var(--score-yellow)"
    return "var(--score-red)"


def _delta_badge_html(delta_text: str, positive: bool | None = None) -> str:
    """Render a compact delta badge."""
    if not delta_text:
        return ""
    if positive is None:
        positive = not delta_text.strip().startswith("-")
    css_class = "delta-positive" if positive else "delta-negative"
    arrow = "▲" if positive else "▼"
    return f'<span class="delta-badge {css_class}">{arrow} {html.escape(delta_text)}</span>'


def _role_badge_html(role: str) -> str:
    """Render the user role badge."""
    role_key = str(role or "viewer").strip().lower()
    label = _role_label(role_key)
    return f'<span class="role-pill role-{html.escape(role_key)}">{html.escape(label)}</span>'


def _status_badge_html(locked: bool) -> str:
    """Render account status badge."""
    css_class = "status-locked" if locked else "status-active"
    label = "Locked" if locked else "Active"
    return f'<span class="status-pill {css_class}">{label}</span>'


def _sanitize_metric_value(value: object, decimals: int = 1) -> str:
    """Format metric values without noisy precision."""
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return str(int(value))
        return f"{float(value):.{decimals}f}"
    return str(value)


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
                --bg-page: {"#0A0F1E" if not st.session_state.get("light_mode", False) else "#F5F7FB"};
                --bg-surface: {"#111827" if not st.session_state.get("light_mode", False) else "#FFFFFF"};
                --bg-elevated: {"#1A2235" if not st.session_state.get("light_mode", False) else "#EEF2F7"};
                --bg-overlay: {"#243049" if not st.session_state.get("light_mode", False) else "#DCE6F2"};
                --brand-primary: #3B82F6;
                --brand-hover: #2563EB;
                --brand-soft: rgba(59,130,246,0.12);
                --green: #22C55E;
                --green-soft: rgba(34,197,94,0.12);
                --yellow: #FACC15;
                --yellow-soft: rgba(250,204,21,0.12);
                --orange: #FB923C;
                --orange-soft: rgba(251,146,60,0.12);
                --red: #EF4444;
                --red-soft: rgba(239,68,68,0.12);
                --teal: #14B8A6;
                --teal-soft: rgba(20,184,166,0.12);
                --text-primary: {"#F1F5F9" if not st.session_state.get("light_mode", False) else "#0F172A"};
                --text-secondary: {"#94A3B8" if not st.session_state.get("light_mode", False) else "#475569"};
                --text-muted: {"#475569" if not st.session_state.get("light_mode", False) else "#64748B"};
                --text-inverse: #0A0F1E;
                --border: {"rgba(148,163,184,0.10)" if not st.session_state.get("light_mode", False) else "rgba(15,23,42,0.10)"};
                --border-hover: {"rgba(148,163,184,0.22)" if not st.session_state.get("light_mode", False) else "rgba(15,23,42,0.20)"};
                --border-focus: rgba(59,130,246,0.50);
                --score-green: #22C55E;
                --score-blue: #3B82F6;
                --score-yellow: #FACC15;
                --score-red: #EF4444;
                --radius-sm: 6px;
                --radius-md: 10px;
                --radius-lg: 16px;
                --radius-xl: 22px;
                --radius-full: 999px;
                --shadow-card: 0 4px 24px rgba(0,0,0,0.30);
                --shadow-modal: 0 12px 48px rgba(0,0,0,0.50);
                --grid: {theme["grid"]};
            }}
            .stApp {{
                background:
                    radial-gradient(circle at top left, rgba(59,130,246,0.12), transparent 30%),
                    radial-gradient(circle at top right, rgba(20,184,166,0.10), transparent 26%),
                    linear-gradient(180deg, var(--bg-overlay) 0%, var(--bg-page) 18%, var(--bg-page) 100%);
                color: var(--text-primary);
            }}
            .block-container {{
                max-width: 1300px;
                padding-top: 24px;
                padding-bottom: 64px;
                padding-left: 32px;
                padding-right: 32px;
            }}
            header[data-testid="stHeader"],
            div[data-testid="stToolbar"] {{
                background: transparent;
            }}
            [data-testid="stSidebar"] {{
                background: var(--bg-surface);
                border-right: 1px solid var(--border);
            }}
            [data-testid="stSidebar"] * {{
                color: var(--text-primary);
            }}
            div[data-testid="stButton"] > button,
            div[data-testid="stDownloadButton"] > button {{
                min-height: 44px;
                border-radius: var(--radius-md);
                border: 1px solid var(--border-hover);
                background: transparent;
                color: var(--text-primary);
                font-weight: 600;
                font-size: 14px;
                box-shadow: none;
                transition: all 160ms ease;
            }}
            div[data-testid="stButton"] > button:hover,
            div[data-testid="stDownloadButton"] > button:hover {{
                border-color: var(--brand-primary);
                background: var(--bg-elevated);
                color: var(--text-primary);
                transform: translateY(-2px);
            }}
            div[data-testid="stButton"] > button[kind="primary"] {{
                background: var(--brand-primary);
                color: white;
                border-color: var(--brand-primary);
            }}
            div[data-testid="stButton"] > button[kind="primary"]:hover {{
                background: var(--brand-hover);
                color: white;
            }}
            div[data-testid="stButton"] > button:focus,
            div[data-testid="stDownloadButton"] > button:focus,
            input:focus,
            textarea:focus,
            select:focus {{
                outline: none !important;
                box-shadow: 0 0 0 3px rgba(59,130,246,0.40) !important;
            }}
            div[data-baseweb="input"] > div,
            div[data-baseweb="select"] > div,
            div[data-baseweb="textarea"] > div {{
                background: var(--bg-elevated) !important;
                border: 1px solid var(--border) !important;
                border-radius: var(--radius-md) !important;
                color: var(--text-primary) !important;
            }}
            label, .stMarkdown, p, span {{
                color: inherit;
            }}
            [data-testid="stMetric"] {{
                background: var(--bg-surface);
                border: 1px solid var(--border);
                border-radius: var(--radius-lg);
                padding: 16px 18px;
                box-shadow: var(--shadow-card);
            }}
            [data-testid="stMetricLabel"] {{
                color: var(--text-muted) !important;
                font-size: 11px !important;
                text-transform: uppercase;
                letter-spacing: 0.08em;
            }}
            [data-testid="stMetricValue"] {{
                color: var(--text-primary) !important;
            }}
            [data-testid="stMetricDelta"] {{
                color: var(--text-secondary) !important;
            }}
            div[data-testid="stToggle"] label,
            div[data-testid="stToggle"] p {{
                color: var(--text-primary) !important;
                font-weight: 500;
            }}
            div[data-baseweb="tab-list"] {{
                gap: 16px;
                border-bottom: 1px solid var(--border);
            }}
            button[data-baseweb="tab"] {{
                color: var(--text-muted) !important;
                font-size: 14px !important;
                font-weight: 500 !important;
                padding: 12px 20px 14px !important;
                border-bottom: 2px solid transparent !important;
            }}
            button[data-baseweb="tab"][aria-selected="true"] {{
                color: var(--text-primary) !important;
                border-bottom-color: var(--brand-primary) !important;
            }}
            [data-testid="stForm"] {{
                background: var(--bg-surface);
                border: 1px solid var(--border);
                border-radius: var(--radius-xl);
                padding: 40px;
                box-shadow: var(--shadow-card);
            }}
            .hero-card {{
                background: var(--bg-surface);
                border: 1px solid var(--border);
                border-radius: var(--radius-xl);
                padding: 28px 30px 22px;
                box-shadow: var(--shadow-card);
                margin-top: 8px;
            }}
            .hero-overline {{
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                font-weight: 700;
                color: var(--text-muted);
                margin-bottom: 8px;
            }}
            .hero-title {{
                font-size: 24px;
                font-weight: 700;
                color: var(--text-primary);
                margin-bottom: 6px;
            }}
            .hero-subtitle {{
                max-width: 700px;
                font-size: 14px;
                line-height: 1.55;
                color: var(--text-secondary);
            }}
            .hero-score {{
                margin: 20px 0 6px 0;
                font-size: clamp(56px, 8vw, 88px);
                line-height: 0.92;
                letter-spacing: -0.04em;
                font-weight: 800;
            }}
            .hero-badge {{
                display: inline-flex;
                align-items: center;
                padding: 4px 10px;
                border-radius: var(--radius-full);
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.06em;
            }}
            .hero-progress {{
                width: 100%;
                height: 4px;
                margin-top: 18px;
                background: var(--bg-elevated);
                border-radius: var(--radius-full);
                overflow: hidden;
            }}
            .hero-progress-fill {{
                height: 100%;
                border-radius: var(--radius-full);
                transition: width 800ms cubic-bezier(0.16,1,0.3,1);
            }}
            .section-heading {{
                margin: 40px 0 14px 0;
            }}
            .section-heading h2 {{
                margin: 0 0 6px 0;
                font-size: 14px;
                font-weight: 700;
                color: var(--text-muted);
                text-transform: uppercase;
                letter-spacing: 0.08em;
            }}
            .section-heading p {{
                margin: 0;
                max-width: 720px;
                font-size: 12px;
                line-height: 1.5;
                color: var(--text-muted);
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
                padding: 20px 24px;
                border-radius: var(--radius-lg);
                border: 1px solid var(--border);
                background: var(--bg-surface);
                box-shadow: var(--shadow-card);
            }}
            .metric-label {{
                margin-bottom: 10px;
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--text-muted);
            }}
            .metric-value {{
                margin-bottom: 8px;
                font-size: 28px;
                line-height: 1.05;
                letter-spacing: -0.03em;
                font-weight: 700;
                color: var(--text-primary);
            }}
            .metric-subtext {{
                font-size: 14px;
                line-height: 1.55;
                color: var(--text-secondary);
            }}
            .metric-support {{
                margin-top: 10px;
            }}
            .delta-badge {{
                display: inline-flex;
                align-items: center;
                gap: 6px;
                border-radius: var(--radius-full);
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.04em;
            }}
            .delta-positive {{
                background: var(--green-soft);
                color: var(--green);
            }}
            .delta-negative {{
                background: var(--red-soft);
                color: var(--red);
            }}
            .table-title {{
                margin-bottom: 14px;
                font-size: 15px;
                font-weight: 700;
                color: var(--text-primary);
            }}
            .saas-table {{
                width: 100%;
                border-collapse: collapse;
                overflow: hidden;
            }}
            .saas-table th {{
                padding: 12px 0;
                text-align: left;
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--text-muted);
                border-bottom: 1px solid var(--border);
            }}
            .saas-table td {{
                padding: 14px 0;
                font-size: 14px;
                color: var(--text-secondary);
                border-bottom: 1px solid rgba(148, 163, 184, 0.10);
                vertical-align: top;
            }}
            .saas-table tbody tr:nth-child(even) td {{
                background: rgba(26,34,53,0.35);
            }}
            .saas-table tr:last-child td {{
                border-bottom: none;
            }}
            .saas-table td:first-child {{
                font-weight: 500;
                color: var(--text-primary);
            }}
            .empty-state {{
                padding-top: 4px;
                font-size: 14px;
                line-height: 1.6;
                color: var(--text-muted);
            }}
            .insight-list,
            .activity-insight-list {{
                margin: 0;
                padding: 0;
                list-style: none;
            }}
            .insight-list li {{
                display: flex;
                align-items: flex-start;
                gap: 10px;
                margin-bottom: 8px;
                padding: 10px 14px;
                border-left: 2px solid var(--yellow);
                background: rgba(250,204,21,0.06);
                color: var(--text-secondary);
                font-size: 13px;
                line-height: 1.55;
            }}
            .activity-insight-list li {{
                display: flex;
                align-items: flex-start;
                gap: 10px;
                margin-bottom: 10px;
                color: var(--text-secondary);
                line-height: 1.6;
            }}
            .activity-insight-list li::before {{
                content: "";
                width: 8px;
                height: 8px;
                margin-top: 7px;
                border-radius: 50%;
                background: var(--brand-primary);
                flex: 0 0 8px;
            }}
            .insight-icon {{
                color: var(--yellow);
                font-size: 16px;
                line-height: 1;
                margin-top: 1px;
            }}
            .context-chip {{
                display: inline-flex;
                align-items: center;
                padding: 6px 10px;
                border-radius: var(--radius-full);
                border: 1px solid var(--border);
                background: var(--bg-elevated);
                color: var(--text-muted);
                font-size: 12px;
                font-weight: 500;
            }}
            .summary-banner {{
                border-left: 3px solid var(--brand-primary);
                background: rgba(59,130,246,0.06);
                color: var(--text-secondary);
                padding: 14px 18px;
                font-size: 14px;
                line-height: 1.6;
            }}
            .breakdown-bar {{
                width: 100%;
                height: 8px;
                margin-top: 14px;
                background: var(--bg-elevated);
                border-radius: var(--radius-full);
                overflow: hidden;
            }}
            .breakdown-bar-fill {{
                height: 100%;
                border-radius: var(--radius-full);
            }}
            .role-pill,
            .status-pill {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 3px 8px;
                border-radius: var(--radius-full);
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }}
            .role-super_admin {{
                background: #312E81;
                color: #A5B4FC;
            }}
            .role-admin {{
                background: #1E3A5F;
                color: #93C5FD;
            }}
            .role-editor {{
                background: #064E3B;
                color: #6EE7B7;
            }}
            .role-user {{
                background: #1F2937;
                color: #9CA3AF;
            }}
            .role-viewer {{
                background: #1F2937;
                color: #6B7280;
            }}
            .status-active {{
                background: var(--green-soft);
                color: var(--green);
            }}
            .status-locked {{
                background: var(--red-soft);
                color: var(--red);
            }}
            .user-card {{
                background: var(--bg-surface);
                border: 1px solid var(--border);
                border-radius: var(--radius-xl);
                padding: 20px 24px;
                margin-bottom: 12px;
            }}
            .user-summary {{
                display: flex;
                align-items: center;
                gap: 12px;
            }}
            .user-avatar {{
                width: 36px;
                height: 36px;
                border-radius: 50%;
                background: var(--bg-elevated);
                border: 1px solid var(--border-hover);
                color: var(--brand-primary);
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 13px;
                font-weight: 600;
            }}
            .login-shell {{
                min-height: 100vh;
                display: grid;
                grid-template-columns: 1.3fr 0.9fr;
                gap: 32px;
                align-items: stretch;
            }}
            .login-visual {{
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 48px;
            }}
            .login-visual-card {{
                width: 100%;
                height: 100%;
                min-height: 560px;
                background:
                    radial-gradient(circle at 30% 20%, rgba(59,130,246,0.20), transparent 35%),
                    radial-gradient(circle at 70% 70%, rgba(20,184,166,0.16), transparent 30%),
                    var(--bg-surface);
                border: 1px solid var(--border);
                border-radius: 28px;
                box-shadow: var(--shadow-card);
                padding: 48px;
                position: relative;
                overflow: hidden;
            }}
            .login-arc {{
                width: 260px;
                height: 260px;
                border-radius: 50%;
                border: 18px solid rgba(59,130,246,0.10);
                border-top-color: var(--brand-primary);
                border-right-color: var(--teal);
                position: relative;
                box-shadow: inset 0 0 0 1px var(--border);
            }}
            .login-arc::after {{
                content: "";
                position: absolute;
                inset: 30px;
                border-radius: 50%;
                border: 1px dashed rgba(148,163,184,0.24);
                animation: pulseRing 3s ease-in-out infinite;
            }}
            .login-panel {{
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 40px 16px;
            }}
            .login-error-banner {{
                margin: 0 0 16px 0;
                background: var(--red-soft);
                border-left: 3px solid var(--red);
                color: #FCA5A5;
                padding: 12px 16px;
                font-size: 13px;
            }}
            .metric-grid-note {{
                font-size: 12px;
                color: var(--text-muted);
            }}
            @keyframes pulseRing {{
                0%, 100% {{ transform: scale(1); opacity: 0.9; }}
                50% {{ transform: scale(1.05); opacity: 0.55; }}
            }}
            @media (max-width: 960px) {{
                .login-shell {{
                    grid-template-columns: 1fr;
                }}
                .login-visual {{
                    display: none;
                }}
                .block-container {{
                    padding-left: 18px;
                    padding-right: 18px;
                }}
            }}
            @media (max-width: 768px) {{
                .hero-title {{
                    font-size: 20px;
                }}
                .hero-score {{
                    font-size: 56px;
                }}
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
    fill = _health_css_var(value)
    return f"""
    <div class="metric-card">
        <div class="metric-label">{html.escape(title)}</div>
        <div class="metric-value" style="color:{fill};">{int(value)}</div>
        <div class="metric-subtext">Signal health score</div>
        <div class="breakdown-bar"><div class="breakdown-bar-fill" style="width:{max(0, min(100, int(value)))}%; background:{fill};"></div></div>
    </div>
    """


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

    items = "".join(f'<li><span class="insight-icon" title="Insight">▲</span><span>{html.escape(item)}</span></li>' for item in insights)
    return f"""
    <div class="content-card">
        <div class="table-title">Activity insights</div>
        <ul class="activity-insight-list">{items}</ul>
    </div>
    """


def _summary_card_html(title: str, value: str, subtext: str, delta_html: str = "", accent: str | None = None) -> str:
    """Render a compact summary card."""
    return f"""
    <div class="content-card">
        <div class="metric-label">{html.escape(title)}</div>
        <div class="metric-value" style="font-size:28px; color:{accent or 'var(--text-primary)'};">{value}</div>
        <div class="metric-support">{delta_html}</div>
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
    error_message = st.session_state.pop("login_error", "")
    st.markdown(
        """
        <div class="login-shell">
            <div class="login-visual">
                <div class="login-visual-card">
                    <div class="hero-overline">Sprint Health</div>
                    <div class="hero-title" style="max-width:520px;">A sharper way to watch delivery health, bugs, and execution flow.</div>
                    <p class="hero-subtitle" style="margin-top:10px; max-width:460px;">
                        Bring sprint quality, throughput, and team activity into one operational workspace with a clean score-first view.
                    </p>
                    <div style="margin-top:48px; display:flex; align-items:center; gap:32px; flex-wrap:wrap;">
                        <div class="login-arc" aria-label="Sprint health illustration" title="Sprint health illustration"></div>
                        <div>
                            <div class="metric-label">Workspace</div>
                            <div class="metric-value" style="color:var(--score-blue); font-size:40px;">Sprint Health</div>
                            <div class="metric-subtext">Delivery quality, blocked time, bugs, and weekly momentum in one place.</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="login-panel">
                <div style="width:min(100%, 400px);">
        """,
        unsafe_allow_html=True,
    )
    with st.form("login_form"):
        st.markdown('<div class="hero-overline">Sprint Health</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-title" style="font-size:22px;">Sign in to your workspace</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-subtitle" style="margin:8px 0 24px 0;">Track delivery health, bugs, and team activity.</div>', unsafe_allow_html=True)
        st.markdown('<div class="section-divider" style="margin-bottom:24px;"></div>', unsafe_allow_html=True)
        if error_message:
            st.markdown(f'<div class="login-error-banner">{html.escape(error_message)}</div>', unsafe_allow_html=True)
        email = st.text_input("Email address", placeholder="admin@lumofy.com")
        password = st.text_input("Password", type="password", placeholder="Enter your password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            if not email or not password:
                st.session_state["login_error"] = "Please enter email and password."
                st.rerun()
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
                    st.session_state.pop("login_error", None)
                    st.rerun()
                st.session_state["login_error"] = "Invalid credentials or account locked."
                st.rerun()
            except requests.RequestException:
                st.session_state["login_error"] = "Cannot reach authentication service."
                st.rerun()
    st.markdown(
        """
                <p class="metric-grid-note" style="text-align:center; margin-top:14px;">Secured with JWT authentication</p>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_admin_dashboard(user: dict):
    if user.get("role") not in ["admin", "super_admin"]:
        st.error("Access denied")
        st.stop()
    
    st.markdown("<h2 style='color: var(--text-primary);'>Admin Control Panel</h2>", unsafe_allow_html=True)
    st.caption("Review accounts, roles, login health, and access permissions.")
    _show_admin_feedback()

    try:
        users = _fetch_users()
    except Exception as exc:
        st.error(f"Cannot fetch users: {exc}")
        return

    st.markdown(
        """
        <div class="content-card" style="margin-bottom:20px;">
            <div class="table-title">Users Summary</div>
            <div class="metric-subtext">Access levels, account health, and recent login activity.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    table_rows = []
    for user_row in users:
        locked = _is_locked_account(user_row)
        attempts = int(user_row.get("failed_attempts", 0) or 0)
        attempt_color = "var(--text-muted)" if attempts == 0 else ("var(--yellow)" if attempts < 3 else "var(--red)")
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(user_row.get('email', '')))}</td>"
            f"<td>{_role_badge_html(str(user_row.get('role', 'viewer')))}</td>"
            f"<td>{html.escape(_format_timestamp(user_row.get('last_login_at')))}</td>"
            f"<td style='text-align:right; color:{attempt_color};'>{attempts}</td>"
            f"<td>{_status_badge_html(locked)}</td>"
            "</tr>"
        )
    st.markdown(
        f"""
        <div class="content-card">
            <table class="saas-table">
                <thead>
                    <tr>
                        <th>Email</th>
                        <th>Role</th>
                        <th>Last Login</th>
                        <th style="text-align:right;">Failed Attempts</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>{''.join(table_rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if user.get("role") != "super_admin":
        st.info("Admins can view users only. Only super_admin can create, delete, change roles, or lock accounts.")
        return

    with st.form("create_user_form", clear_on_submit=True):
        st.markdown('<div class="content-card" style="margin-bottom:16px;"><div class="table-title">Create Account</div><div class="metric-subtext">Provision a new workspace user with the correct role and access level.</div></div>', unsafe_allow_html=True)
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

        initials = "".join(part[:1].upper() for part in account_email.split("@")[0].replace(".", " ").split()[:2]) or "U"
        st.markdown(
            f"""
            <div class="user-card">
                <div class="user-summary">
                    <span class="user-avatar">{html.escape(initials[:2])}</span>
                    <div>
                        <div style="font-size:14px; font-weight:600; color:var(--text-primary);">{html.escape(account_email)}</div>
                        <div style="font-size:12px; color:var(--text-muted);">{html.escape(_format_timestamp(user_row.get("last_login_at")))}</div>
                    </div>
                    <div style="margin-left:auto; display:flex; gap:8px; flex-wrap:wrap;">{_role_badge_html(account_role)}{_status_badge_html(account_locked)}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.container():
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

    st.markdown("<h2 style='color: var(--text-primary);'>Metrics Override Center</h2>", unsafe_allow_html=True)
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
    st.markdown('<div class="content-card" style="margin-bottom:16px;"><div class="table-title">Editable Metrics</div><div class="metric-subtext">Adjust presentation-layer override values while keeping the underlying calculations intact.</div></div>', unsafe_allow_html=True)
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
    status_var = _health_css_var(score)
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
            <div style="display:flex; justify-content:space-between; gap:16px; align-items:flex-start; flex-wrap:wrap;">
                <div>
                    <div class="hero-overline">Sprint Health Platform</div>
                    <div class="hero-title">A focused view of delivery quality, execution pace, and team activity.</div>
                    <div class="hero-subtitle" style="margin-top:6px;">
                        Current sprint view
                    </div>
                    <div class="hero-score" style="color:{status_var};">{score}<span style="font-size:22px;color:var(--text-muted); font-weight:500;">/100</span></div>
                    <div class="hero-subtitle">
                        Track how the sprint is progressing across commitment, carryover, bugs, and execution trends with a clean operational dashboard.
                    </div>
                </div>
                <div class="hero-badge" style="background:{theme["primary_soft"]};color:{status_var};border:1px solid var(--border);">
                    {score} · {_health_label(score)}
                </div>
            </div>
            <div class="hero-progress"><div class="hero-progress-fill" style="width:{max(0, min(100, score))}%; background:linear-gradient(90deg, {status_var}, {status_var});"></div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(f"API source: {api_base_url}")
    if not activity.get("developers") and not activity.get("testers") and int(activity.get("bugs_today", 0) or 0) == 0:
        st.info("No team activity has been recorded for today yet.")
    if not weekly.get("daily_breakdown"):
        st.info("No weekly activity data found for the current period.")
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
            st.markdown(
                _summary_card_html(
                    "Sprint Score",
                    f"{score}/100",
                    status_text,
                    delta_html=_delta_badge_html(_health_label(score), positive=score >= 70),
                    accent=status_var,
                ),
                unsafe_allow_html=True,
            )
        with metric_cols[1]:
            next_health = int(prediction.get("next_sprint_health", score) or score)
            st.markdown(
                _summary_card_html(
                    "Next Sprint",
                    str(next_health),
                    f"Confidence: {prediction.get('confidence', 'low')}",
                    delta_html=_delta_badge_html(str(prediction.get("trend") or "stable"), positive=str(prediction.get("trend") or "").lower() != "declining"),
                ),
                unsafe_allow_html=True,
            )
        with metric_cols[2]:
            st.markdown(
                _summary_card_html(
                    "Blocked %",
                    f"{blocked_ratio:.1f}%",
                    "Time spent in blocked status",
                    delta_html=_delta_badge_html("High" if blocked_ratio > 20 else "Normal", positive=blocked_ratio <= 20),
                    accent="var(--score-red)" if blocked_ratio > 20 else "var(--text-primary)",
                ),
                unsafe_allow_html=True,
            )
        with metric_cols[3]:
            st.markdown(
                _summary_card_html(
                    "Completion %",
                    f"{completion_rate:.1f}%",
                    "Delivered versus committed scope",
                    delta_html=_delta_badge_html("On target" if completion_rate >= 70 else "Below target", positive=completion_rate >= 70),
                ),
                unsafe_allow_html=True,
            )

        if summary:
            st.markdown(f'<div class="summary-banner">{html.escape(summary)}</div>', unsafe_allow_html=True)

        if insights:
            _render_section_header("Insights", "Deterministic root-cause analysis for the current sprint outcome.")
            insight_html = "".join(
                f'<li><span class="insight-icon" aria-label="warning" title="warning">▲</span><span>{html.escape(insight)}</span></li>'
                for insight in insights
            )
            st.markdown(f'<ul class="insight-list">{insight_html}</ul>', unsafe_allow_html=True)

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
            st.markdown(_summary_card_html("Story Cycle", _sanitize_metric_value(cycle_time_metrics.get("story"), 1), "Median days per story"), unsafe_allow_html=True)
        with signal_cols[1]:
            st.markdown(_summary_card_html("Bug Cycle", _sanitize_metric_value(cycle_time_metrics.get("bug"), 1), "Median days per bug"), unsafe_allow_html=True)
        with signal_cols[2]:
            st.markdown(_summary_card_html("Task Cycle", _sanitize_metric_value(cycle_time_metrics.get("task"), 1), "Median days per task"), unsafe_allow_html=True)
        with signal_cols[3]:
            st.markdown(_summary_card_html("Avg Bugs/Story", _sanitize_metric_value(bug_metrics.get("avg_per_story", 0.0), 1), "Average story-related bug load"), unsafe_allow_html=True)

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
            trend_chart = px.area(
                history_df,
                x="sprint",
                y="health_score",
                markers=True,
            )
            marker_colors = [_health_tier_color(int(value), theme) for value in history_df["health_score"].tolist()]
            trend_chart.update_traces(
                line=dict(color="#3B82F6", width=3),
                fillcolor="rgba(59,130,246,0.08)",
                marker=dict(size=8, color=marker_colors, line=dict(color="#ffffff", width=1)),
            )
            trend_chart.add_hline(y=70, line_dash="dash", line_color="#FACC15", annotation_text="Target")
            trend_chart.add_hline(y=85, line_dash="dash", line_color="#22C55E", annotation_text="Excellent")
            trend_chart.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=340,
                margin=dict(l=20, r=20, t=20, b=20),
                font=dict(color=theme["muted"]),
                xaxis=dict(tickangle=30, showgrid=False),
                yaxis=dict(gridcolor=theme["grid"], zeroline=False),
            )
            st.plotly_chart(trend_chart, use_container_width=True, config={"displayModeBar": False})
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
