"""Streamlit dashboard for sprint health monitoring."""

from __future__ import annotations

import logging

import plotly.graph_objects as go
import requests
import streamlit as st

from app.config import load_settings
from app.service import calculate_health_snapshot


logger = logging.getLogger(__name__)

# Dark theme palette
_BG = "#0f1419"
_SURFACE = "#1a2332"
_SURFACE_ELEVATED = "#243044"
_TEXT_PRIMARY = "#e8eef7"
_TEXT_MUTED = "#94a3b8"
_ACCENT = "#38bdf8"
_GREEN = "#22c55e"
_YELLOW = "#eab308"
_RED = "#ef4444"


def _health_tier_color(score: int) -> str:
    """Return hex color for score band: green >=70, yellow 50–69, red <50."""
    if score >= 70:
        return _GREEN
    if score >= 50:
        return _YELLOW
    return _RED


def _load_snapshot() -> dict:
    """Load health snapshot from API or in-process Jira calculation."""
    settings = load_settings()
    base = (settings.api_base_url or "").strip().rstrip("/")
    if base:
        url = f"{base}/health-score"
        logger.info("Fetching dashboard data from %s", url)
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        data = response.json()
        return {
            "score": data["score"],
            "completion_rate": data["completion_rate"],
            "breakdown": data["breakdown"],
        }
    return calculate_health_snapshot(settings)


def _inject_base_styles() -> None:
    """Apply global dark theme CSS (background, typography, spacing)."""
    st.markdown(
        f"""
        <style>
            .stApp {{
                background: linear-gradient(165deg, {_BG} 0%, #151c28 45%, {_BG} 100%);
                color: {_TEXT_PRIMARY};
            }}
            .block-container {{
                padding-top: 1.25rem;
                padding-bottom: 2rem;
                max-width: 1200px;
            }}
            header[data-testid="stHeader"] {{
                background: transparent;
            }}
            div[data-testid="stToolbar"] {{
                background: transparent;
            }}
            .dash-header {{
                margin-bottom: 1.75rem;
            }}
            .dash-header h1 {{
                font-size: 1.75rem;
                font-weight: 700;
                letter-spacing: -0.02em;
                color: {_TEXT_PRIMARY};
                margin: 0 0 0.35rem 0;
            }}
            .dash-header p {{
                color: {_TEXT_MUTED};
                font-size: 0.95rem;
                margin: 0;
            }}
            .metric-card {{
                background: {_SURFACE};
                border: 1px solid rgba(148, 163, 184, 0.12);
                border-radius: 14px;
                padding: 1.25rem 1.35rem;
                height: 100%;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
            }}
            .metric-card-label {{
                font-size: 0.72rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: {_TEXT_MUTED};
                margin-bottom: 0.5rem;
            }}
            .metric-card-value {{
                font-size: 2.1rem;
                font-weight: 700;
                line-height: 1.15;
                color: {_TEXT_PRIMARY};
            }}
            .metric-card-unit {{
                font-size: 1rem;
                font-weight: 600;
                color: {_TEXT_MUTED};
            }}
            .metric-pill {{
                display: inline-block;
                margin-top: 0.75rem;
                padding: 0.35rem 0.65rem;
                border-radius: 999px;
                font-size: 0.75rem;
                font-weight: 600;
                letter-spacing: 0.02em;
            }}
            .section-title {{
                font-size: 1.05rem;
                font-weight: 600;
                color: {_TEXT_PRIMARY};
                margin: 2rem 0 1rem 0;
                padding-bottom: 0.5rem;
                border-bottom: 1px solid rgba(148, 163, 184, 0.15);
            }}
            .breakdown-grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 0.85rem;
            }}
            @media (max-width: 900px) {{
                .breakdown-grid {{ grid-template-columns: repeat(2, 1fr); }}
            }}
            .breakdown-item {{
                background: {_SURFACE_ELEVATED};
                border: 1px solid rgba(148, 163, 184, 0.1);
                border-radius: 12px;
                padding: 1rem 1rem;
                text-align: center;
            }}
            .breakdown-item-label {{
                font-size: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                color: {_TEXT_MUTED};
                margin-bottom: 0.4rem;
            }}
            .breakdown-item-value {{
                font-size: 1.45rem;
                font-weight: 700;
                color: {_TEXT_PRIMARY};
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _metric_card_html(label: str, main_value: str, sub_line: str, accent_hex: str) -> str:
    """Build a single metric card with label, value, and status pill."""
    return f"""
    <div class="metric-card">
        <div class="metric-card-label">{label}</div>
        <div class="metric-card-value" style="color: {accent_hex};">{main_value}</div>
        <div class="metric-pill" style="background: rgba(56, 189, 248, 0.12); color: {_ACCENT}; border: 1px solid rgba(56, 189, 248, 0.25);">
            {sub_line}
        </div>
    </div>
    """


def _breakdown_item_html(title: str, value: int, tier_color: str) -> str:
    """Single breakdown cell with tier-based value color."""
    return f"""
    <div class="breakdown-item">
        <div class="breakdown-item-label">{title}</div>
        <div class="breakdown-item-value" style="color: {tier_color};">{value}</div>
    </div>
    """


def _signal_tier_color(value: int) -> str:
    """Color per signal using same bands as overall health."""
    return _health_tier_color(value)


def _build_breakdown_plotly(breakdown: dict) -> go.Figure:
    """Clean dark-themed bar chart for signal scores."""
    labels = ["Commitment", "Carryover", "Cycle time", "Bug ratio"]
    keys = ["commitment", "carryover", "cycle_time", "bug_ratio"]
    values = [int(breakdown[k]) for k in keys]
    colors = [_signal_tier_color(v) for v in values]

    fig = go.Figure(
        data=[
            go.Bar(
                x=labels,
                y=values,
                marker_color=colors,
                text=[str(v) for v in values],
                textposition="outside",
                textfont=dict(color=_TEXT_PRIMARY, size=12),
            )
        ]
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_TEXT_MUTED, family="system-ui, sans-serif"),
        title=dict(text="Signal scores (0–100)", font=dict(size=14, color=_TEXT_PRIMARY)),
        margin=dict(l=48, r=24, t=48, b=48),
        yaxis=dict(range=[0, 105], gridcolor="rgba(148,163,184,0.15)", zeroline=False),
        xaxis=dict(showgrid=False),
        showlegend=False,
        height=380,
    )
    return fig


def main() -> None:
    """Render sprint health dashboard UI."""
    st.set_page_config(page_title="Sprint Health Dashboard", layout="wide")
    _inject_base_styles()

    try:
        snapshot = _load_snapshot()
    except Exception as exc:
        st.error(f"Could not load sprint health data: {exc}")
        return

    score = int(snapshot["score"])
    completion_rate = float(snapshot["completion_rate"])
    breakdown = snapshot["breakdown"]

    tier_hex = _health_tier_color(score)
    if score >= 70:
        status_text = "On track"
    elif score >= 50:
        status_text = "Needs attention"
    else:
        status_text = "At risk"

    # Header
    st.markdown(
        f"""
        <div class="dash-header">
            <h1>Sprint health</h1>
            <p>Live snapshot of delivery signals and quality indicators.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Metrics row
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown(
            _metric_card_html(
                "Health score",
                f"{score}<span class='metric-card-unit'>/100</span>",
                f'<span style="color:{tier_hex};">{status_text}</span>',
                tier_hex,
            ),
            unsafe_allow_html=True,
        )
    with c2:
        cr_tier = _health_tier_color(int(round(completion_rate)))
        st.markdown(
            _metric_card_html(
                "Completion rate",
                f"{completion_rate:.1f}<span class='metric-card-unit'>%</span>",
                f'<span style="color:{cr_tier};">Scope delivered vs committed</span>',
                cr_tier,
            ),
            unsafe_allow_html=True,
        )

    # Breakdown section (styled grid, no raw JSON)
    st.markdown('<div class="section-title">Score breakdown</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="breakdown-grid">
            {_breakdown_item_html("Commitment", int(breakdown["commitment"]), _signal_tier_color(int(breakdown["commitment"])))}
            {_breakdown_item_html("Carryover", int(breakdown["carryover"]), _signal_tier_color(int(breakdown["carryover"])))}
            {_breakdown_item_html("Cycle time", int(breakdown["cycle_time"]), _signal_tier_color(int(breakdown["cycle_time"])))}
            {_breakdown_item_html("Bug ratio", int(breakdown["bug_ratio"]), _signal_tier_color(int(breakdown["bug_ratio"])))}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Charts section
    st.markdown('<div class="section-title">Charts</div>', unsafe_allow_html=True)
    fig = _build_breakdown_plotly(breakdown)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


if __name__ == "__main__":
    main()
