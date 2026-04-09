import json
from html import escape
from pathlib import Path
from datetime import datetime, timezone

# --- UI CONSTANTS ---

ALL_ISSUE_TYPES = {
    "Story": ("STRY", "#1a6bff"),
    "Bug": ("BUG", "#ff4757"),
    "Task": ("TSK", "#fbbf24"),
    "Sub-task": ("SUB", "#00d4aa"),
    "Enhancement": ("ENH", "#8a7dff"),
    "Feature Bug": ("FBUG", "#a78bfa"),
}
DEFAULT_ISSUE_ICON = ("ITEM", "#4a90d9")

# --- UI HELPERS ---

def _format_decimal(value: float, places: int = 2) -> str:
    try:
        if value is None: return "0"
        f_val = float(value)
        if places == 0: return f"{int(round(f_val)):,}"
        return f"{f_val:,.{places}f}"
    except (ValueError, TypeError):
        return str(value)

def format_duration_hours(hours_value: float | int | None) -> str:
    if hours_value is None or hours_value <= 0: return "0m"
    total_minutes = int(hours_value * 60)
    days = total_minutes // (24 * 60)
    remaining_min = total_minutes % (24 * 60)
    hours = remaining_min // 60
    minutes = remaining_min % 60
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0 or not parts: parts.append(f"{minutes}m")
    return " ".join(parts)

def _person_avatar_html(name: str, avatar_url: str | None, class_name: str = "qa-tester-avatar") -> str:
    if avatar_url and "http" in avatar_url:
        return f'<div class="{class_name}"><img src="{avatar_url}" alt="{escape(name)}" loading="lazy"></div>'
    initials = "".join([n[0] for n in (name or "U").split() if n])[:2].upper()
    return f'<div class="{class_name}-fallback {class_name}">{escape(initials)}</div>'

# --- UI BUILDERS ---

def _build_burndown_svg(bd: dict) -> str:
    if not bd or not bd.get("actual_line"):
        return "<p style='color:#4a5568;font-style:italic'>No burndown data available.</p>"
    W, H = 820, 320
    PAD_L, PAD_R, PAD_T, PAD_B = 54, 28, 20, 48
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    actual, ideal = bd["actual_line"], bd["ideal_line"]
    max_y = max(bd["total_issues"], 1)
    def cx(day, total): return round(PAD_L + day / total * plot_w, 2)
    def cy(val): return round(PAD_T + (1 - val / max_y) * plot_h, 2)
    ideal_pts = " ".join(f"{cx(d, bd['total_days'])},{cy(v)}" for d, v in enumerate(ideal))
    actual_pts = " ".join(f"{cx(d, bd['total_days'])},{cy(v)}" for d, v in enumerate(actual))
    grid_lines = ""
    for pct in [0, 20, 40, 60, 80, 100]:
        val = max_y * pct / 100; y = cy(val)
        grid_lines += (
            f'<line x1="{PAD_L}" y1="{y}" x2="{W-PAD_R}" y2="{y}" stroke="#1e3a5f" stroke-width="1"/>'
            f'<text x="{PAD_L-6}" y="{y+4}" text-anchor="end" font-size="10" fill="#4a90d9">{round(max_y*pct/100)}</text>'
        )
    x_labels = ""
    label_list = bd.get("ideal_labels", [])
    step = max(1, len(label_list) // 6)
    for idx in range(0, len(label_list), step):
        x = cx(idx, bd["total_days"])
        x_labels += f'<text x="{x}" y="{H-PAD_B+16}" text-anchor="middle" font-size="10" fill="#4a90d9">{label_list[idx]}</text>'
    today_x = cx(min(bd["elapsed_days"], bd["total_days"]), bd["total_days"])
    today_line = (
        f'<line x1="{today_x}" y1="{PAD_T}" x2="{today_x}" y2="{H-PAD_B}" stroke="#1a6bff" stroke-width="1.5" stroke-dasharray="4,3"/>'
        f'<text x="{today_x+4}" y="{PAD_T+12}" font-size="10" fill="#1a6bff">Today</text>'
    )
    track_color = "#00d4aa" if bd.get("on_track") else "#ff4757"
    return f"""<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">
  {grid_lines}
  <line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{H-PAD_B}" stroke="#1e3a5f" stroke-width="1.5"/>
  <line x1="{PAD_L}" y1="{H-PAD_B}" x2="{W-PAD_R}" y2="{H-PAD_B}" stroke="#1e3a5f" stroke-width="1.5"/>
  <polyline points="{ideal_pts}" fill="none" stroke="#2d5a8e" stroke-width="2" stroke-dasharray="6,4"/>
  <polyline points="{actual_pts}" fill="none" stroke="{track_color}" stroke-width="2.5"/>
  {today_line}{x_labels}
  <line x1="{PAD_L+8}" y1="{H-6}" x2="{PAD_L+24}" y2="{H-6}" stroke="#2d5a8e" stroke-width="2" stroke-dasharray="4,3"/>
  <text x="{PAD_L+28}" y="{H-2}" font-size="10" fill="#4a90d9">Ideal</text>
  <line x1="{PAD_L+72}" y1="{H-6}" x2="{PAD_L+88}" y2="{H-6}" stroke="{track_color}" stroke-width="2.5"/>
  <text x="{PAD_L+92}" y="{H-2}" font-size="10" fill="#4a90d9">Actual</text>
</svg>"""

def _build_burndown_explainer_html(bd: dict) -> str:
    if not bd: return ""
    return """
    <div class="burndown-explainer">
      <div class="burndown-explainer-title">What This Workload Burndown Shows</div>
      <p class="burndown-explainer-copy">The burndown compares how much scope should be left each day versus how much scope is actually still open.</p>
      <div class="burndown-scope-note">Burndown scope here tracks Stories only. The Remaining Scope Breakdown below still shows all remaining work types.</div>
      <div class="burndown-legend">
        <span><i class="ideal"></i> Ideal line: the expected pace to finish on time</span>
        <span><i class="actual"></i> Actual line: the real remaining scope day by day</span>
        <span><i class="today"></i> Today marker: where the sprint stands right now</span>
      </div>
      <p class="burndown-explainer-copy">If the actual line stays above the ideal line, the sprint is burning slower than planned.</p>
    </div>"""

def _build_remaining_scope_breakdown_html(bd: dict) -> str:
    if not bd: return ""
    remaining_breakdown = bd.get("remaining_breakdown") or []
    if not remaining_breakdown: return "<div class='details-empty'>No remaining scope.</div>"
    max_val = max([float(item.get("scope", 0)) for item in remaining_breakdown])
    max_val = max(1.0, max_val)
    rows = []
    for item in remaining_breakdown:
        label = str(item.get("type", "Other"))
        scope = float(item.get("scope", 0.0))
        pct = (scope / max_val) * 100
        cls = label.lower().replace(" ", "-")
        rows.append(f"""
        <div class='scope-breakdown-row {cls}'>
          <div class='scope-breakdown-row-top'><span>{escape(label)}</span><strong>{_format_decimal(scope, 0)} scope</strong></div>
          <div class='scope-breakdown-bar-bg'><div class='scope-breakdown-bar-fill' style='width:{pct}%'></div></div>
        </div>""")
    return f"""
    <div class="burndown-breakdown-under">
      <div class="scope-breakdown">
        <div class="scope-breakdown-title">Remaining Scope Breakdown</div>
        {''.join(rows)}
      </div>
    </div>"""

def _build_burndown_takeaways_html(bd: dict) -> str:
    if not bd: return ""
    remaining_breakdown = bd.get("remaining_breakdown") or []
    story_item = next((item for item in remaining_breakdown if str(item.get("type", "")).strip().lower() == "story"), None)
    top_item = story_item or (remaining_breakdown[0] if remaining_breakdown else None)
    top_type = top_item["type"] if top_item else "N/A"
    top_scope = float(top_item["scope"]) if top_item else 0.0
    total_remaining = max(float(bd.get("current_remaining", 0.0) or 0.0), 1.0)
    top_share = round((top_scope / total_remaining) * 100, 1) if top_item else 0.0
    behind_by = max(0.0, float(bd.get("behind_by", 0.0) or 0.0))
    risk_label = "Critical drift" if bd.get("is_extended") else ("Healthy pace" if bd.get("on_track") else "Needs catch-up")
    risk_class = "green" if bd.get("on_track") and not bd.get("is_extended") else "red"
    return f"""
    <div class="burndown-takeaways">
      <div class="burndown-takeaways-title">Burndown Snapshot</div>
      <div class="burndown-takeaway-grid">
        <div class="burndown-takeaway"><strong>{escape(top_type)}</strong><span>Largest remaining work type</span></div>
        <div class="burndown-takeaway"><strong>{top_share}%</strong><span>Of remaining scope comes from {escape(top_type)}</span></div>
        <div class="burndown-takeaway"><strong>{_format_decimal(behind_by, 0)} scope</strong><span>Extra scope above ideal pace</span></div>
        <div class="burndown-takeaway"><strong class="{risk_class}">{escape(risk_label)}</strong><span>Burndown risk signal right now</span></div>
      </div>
    </div>"""

def _build_progress_donut_svg(completed: int, total: int) -> str:
    total = max(int(total or 0), 1)
    completed = max(0, min(int(completed or 0), total))
    remaining = total - completed
    pct = round((completed / total) * 100)
    r = 42
    c = 2 * 3.14159 * r
    completed_len = round(c * (completed / total), 2)
    remaining_len = round(c * (remaining / total), 2)
    return f"""
    <svg viewBox="0 0 120 120" class="details-donut" xmlns="http://www.w3.org/2000/svg">
      <circle cx="60" cy="60" r="{r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="14"/>
      <circle cx="60" cy="60" r="{r}" fill="none" stroke="#00d4aa" stroke-width="14" stroke-linecap="round" stroke-dasharray="{completed_len} {c}" transform="rotate(-90 60 60)"/>
      <circle cx="60" cy="60" r="{r}" fill="none" stroke="#1a6bff" stroke-width="14" stroke-linecap="round" stroke-dasharray="{remaining_len} {c}" stroke-dashoffset="{-completed_len}" transform="rotate(-90 60 60)"/>
      <circle cx="60" cy="60" r="28" fill="#101d34"/>
      <text x="60" y="55" text-anchor="middle" class="details-donut-value">{pct}%</text>
      <text x="60" y="71" text-anchor="middle" class="details-donut-label">Complete</text>
    </svg>"""

def _build_age_distribution_chart_html(age_buckets: dict) -> str:
    if not age_buckets: return "<div class='details-empty'>No unfinished issues yet.</div>"
    max_val = max(1, max(age_buckets.values()))
    bars = []
    colors = ["#00d4aa", "#1a6bff", "#fbbf24", "#4a90d9"]
    for idx, (label, value) in enumerate(age_buckets.items()):
        height = max(10, round((value / max_val) * 140)) if value else 6
        bars.append(f"<div class='age-bar-col'><div class='age-bar-value'>{value}</div><div class='age-bar' style='height:{height}px;background:{colors[idx % len(colors)]}'></div><div class='age-bar-label'>{escape(label)}</div></div>")
    return f"<div class='age-chart'>{''.join(bars)}</div>"

def _build_issue_type_breakdown_panel(issue_type_counts: dict, total: int) -> str:
    if not issue_type_counts: return "<div class='details-empty'>No issue type data.</div>"
    colors = {"Sub-task": "#00d4aa", "Bug": "#ff4757", "Story": "#1a6bff", "Enhancement": "#4a90d9", "Feature-Bug": "#a78bfa", "Task": "#fbbf24"}
    total = max(int(total or 0), 1)
    rows, donut_segments = [], []
    offset, circumference = 0.0, 2 * 3.14159 * 42
    for issue_type, count in list(issue_type_counts.items())[:6]:
        pct = round((count / total) * 100, 1)
        color = colors.get(issue_type, "#4a90d9")
        seg = round((count / total) * circumference, 2)
        donut_segments.append(f"<circle cx='60' cy='60' r='42' fill='none' stroke='{color}' stroke-width='14' stroke-dasharray='{seg} {circumference}' stroke-dashoffset='{-offset}' transform='rotate(-90 60 60)'/>")
        offset += seg
        rows.append(f"<div class='issue-type-row'><div class='issue-type-name'><i style='background:{color}'></i>{escape(issue_type)}</div><div class='issue-type-count'>{count}</div><div class='issue-type-pct'>{pct}%</div><div class='issue-type-track'><span style='width:{pct}%;background:{color}'></span></div></div>")
    donut = f"<svg viewBox='0 0 120 120' class='details-donut' xmlns='http://www.w3.org/2000/svg'><circle cx='60' cy='60' r='42' fill='none' stroke='rgba(255,255,255,.07)' stroke-width='14'/>{''.join(donut_segments)}<circle cx='60' cy='60' r='28' fill='#101d34'/><text x='60' y='64' text-anchor='middle' class='details-donut-value'>{total}</text></svg>"
    return f"<div class='issue-type-layout'><div class='issue-type-donut-wrap'>{donut}</div><div class='issue-type-list'>{''.join(rows)}</div></div>"

def _build_assignee_workload_panel(assignee_counts: dict, total: int) -> str:
    if not assignee_counts: return "<div class='details-empty'>No assignee workload data.</div>"
    total = max(1, total)
    rows = []
    for assignee, count in list(assignee_counts.items())[:8]:
        share = round((count / total) * 100, 1)
        primary = min(100, round(share * 0.55, 1))
        secondary = min(100 - primary, round(share * 0.30, 1))
        tertiary = min(100 - primary - secondary, round(share * 0.15, 1))
        rows.append(f"<div class='assignee-row'><div class='assignee-name'>{escape(assignee)}</div><div class='assignee-load'><span class='seg seg-a' style='width:{primary}%'></span><span class='seg seg-b' style='width:{secondary}%'></span><span class='seg seg-c' style='width:{tertiary}%'></span></div><div class='assignee-count'>{count}</div></div>")
    return f"<div class='assignee-list'>{''.join(rows)}</div>"

def _build_cycle_time_medians_panel_html(medians: dict) -> str:
    if not medians: return "<div style='color:#8ab4d9;font-size:13px;padding:20px'>No cycle times yet.</div>"
    max_median = max(1.0, max(medians.values()))
    overall = round(sum(medians.values()) / len(medians), 1)
    rows = []
    for t_name, days in sorted(medians.items(), key=lambda x: -x[1]):
        _, color = ALL_ISSUE_TYPES.get(t_name, DEFAULT_ISSUE_ICON)
        pct = round((days / max_median) * 100)
        rows.append(f'<div class="issue-type-row" style="grid-template-columns: minmax(0, 1.4fr) 42px 1fr;"><div class="issue-type-name"><i style="background:{color}"></i>{escape(t_name)}</div><div class="issue-type-count">{round(days, 1)}d</div><div class="issue-type-track"><span style="width:{pct}%;background:{color}"></span></div></div>')
    return f'<div class="details-big-metric" style="margin-bottom: 20px;">Overall Median: {overall} Days</div><div class="issue-type-list">{"".join(rows)}</div>'

def _build_blocked_time_ratio_panel_html(bottlenecks: dict) -> str:
    if not bottlenecks: return "<div style='color:#8ab4d9;font-size:13px;padding:20px'>No bottleneck data.</div>"
    ratio_pct, top = bottlenecks.get("blocked_ratio_pct", 0.0), bottlenecks.get("top_bottlenecks", [])
    circumference = 263.89
    dash_array = f"{(ratio_pct / 100.0) * circumference} {circumference}"
    legend_html, colors = "", ["#fbbf24", "#ff4757", "#a78bfa"]
    for i, t in enumerate(top):
        legend_name = f'{t["name"]} (Bugs only)' if str(t.get("name", "")).strip().lower() == "open" else t["name"]
        legend_html += f'<div><i style="background:{colors[i % len(colors)]}"></i>{escape(legend_name)} <span style="color:#8ab4d9; font-size: 11px;">({round(t["pct"])}%)</span></div>'
    bottleneck_html = ""
    if bottlenecks.get("worst_bottleneck_name"):
        worst = bottlenecks["worst_bottleneck_name"]
        worst_label = f'{worst} (for Bugs only)' if str(worst).strip().lower() == "open" else worst
        bottleneck_html = f'<div style="margin-top: 18px; font-size: 12px; line-height: 1.5; color: #8ab4d9; background: rgba(255,255,255,.03); padding: 12px 14px; border-radius: 10px; border: 1px solid rgba(26,107,255,.12);"><strong style="color:#e0eaff;">Top Bottleneck:</strong> Tickets sit longest in <em style="color:#fbbf24;">"{escape(worst_label)}"</em> status, costing an average of {round(bottlenecks.get("worst_bottleneck_days",0),1)} days per blocked issue.</div>'
    return f'<div class="details-progress-layout" style="margin-top: 14px;"><svg viewBox="0 0 120 120" class="details-donut" xmlns="http://www.w3.org/2000/svg"><circle cx="60" cy="60" r="42" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="14" /><circle cx="60" cy="60" r="42" fill="none" stroke="#fbbf24" stroke-width="14" stroke-linecap="round" stroke-dasharray="{dash_array}" transform="rotate(-90 60 60)" /><circle cx="60" cy="60" r="30" fill="#101d34" /><text x="60" y="56" text-anchor="middle" class="details-donut-value" style="font-size:15px;">{round(ratio_pct, 1)}%</text><text x="60" y="69" text-anchor="middle" class="details-donut-label" style="font-size:7.5px;">Blocked</text></svg><div class="details-legend">{legend_html}</div></div>{bottleneck_html}'

def _issue_row_html(iss: dict, show_rft: bool = True) -> str:
    _, color = ALL_ISSUE_TYPES.get(iss["type"], DEFAULT_ISSUE_ICON)
    icon = ALL_ISSUE_TYPES.get(iss["type"], DEFAULT_ISSUE_ICON)[0]
    done_style = "opacity:0.6;text-decoration:line-through;" if iss.get("is_done") else ""
    stale_tag = f'<span class="issue-stale-tag">🔴 Stale ({iss["active_days"]}d / {iss["stale_threshold"]}d)</span>' if iss.get("is_stale") else ""
    active_tag = f'<span class="issue-active-tag">Active {iss["active_days"]}d</span>' if iss.get("active_days", 0) > 1 and not iss.get("is_stale") else ""
    pts_tag = f'<span class="issue-pts-tag">{iss["story_points"]} pts</span>' if iss.get("story_points") else ""
    rft_tag = f'<span class="issue-active-tag">🕐 {format_duration_hours(iss["time_in_rft"])} in testing</span>' if show_rft and iss.get("time_in_rft", 0) > 0 else ""
    trans_html = "".join(f'<span class="issue-status-tag">{escape(tr)}</span>' for tr in (iss.get("transitions_today") or []))
    return f'<div class="dev-issue {"stale" if iss.get("is_stale") else ""}"><span class="issue-icon" style="color:{color}">{icon}</span><div class="issue-body"><a href="{iss["url"]}" target="_blank" class="issue-key">{iss["key"]}</a><span class="issue-summary" style="{done_style}">{escape(iss["summary"][:70])}{"…" if len(iss["summary"])>70 else ""}</span><div class="issue-tags"><span class="issue-status-tag">{escape(iss["status"])}</span>{pts_tag}{active_tag}{rft_tag}{stale_tag}{"✓ Done" if iss.get("is_done") else ""}{trans_html}</div></div></div>'

    return f'<div class="dev-issue {"stale" if iss.get("is_stale") else ""}"><span class="issue-icon" style="color:{color}">{icon}</span><div class="issue-body"><a href="{iss["url"]}" target="_blank" class="issue-key">{iss["key"]}</a><span class="issue-summary" style="{done_style}">{escape(iss["summary"][:70])}{"…" if len(iss["summary"])>70 else ""}</span><div class="issue-tags"><span class="issue-status-tag">{escape(iss["status"])}</span>{pts_tag}{active_tag}{rft_tag}{stale_tag}{"✓ Done" if iss.get("is_done") else ""}{trans_html}</div></div></div>'

def _person_avatar_html(name: str, avatar_url: str | None, class_name: str = "qa-tester-avatar") -> str:
    if avatar_url and "http" in avatar_url:
        return f'<div class="{class_name}"><img src="{avatar_url}" alt="{escape(name)}" loading="lazy"></div>'
    initials = "".join([n[0] for n in (name or "U").split() if n])[:2].upper()
    return f'<div class="{class_name}-fallback {class_name}">{escape(initials)}</div>'

def format_slack_message(r: dict) -> str:
    score      = r["health_score"]
    health_dot = "🟢" if score >= 85 else "🟡" if score >= 70 else "🟠" if score >= 50 else "🔴"
    filled     = round(score / 10)
    bar        = "█" * filled + "░" * (10 - filled)

    def sig_dot(s): return "🟢" if s >= 85 else "🟡" if s >= 70 else "🟠" if s >= 50 else "🔴"
    def nd(k): return " _— no data yet_" if r["signals"][k].get("no_data") else ""

    sigs    = r["signals"]
    fb      = r["formula_breakdown"]
    weights = r["weights"]

    sig_rows = (
        f"{sig_dot(sigs['commitment']['score'])}  *Commitment*  {sigs['commitment']['raw']}  →  *{sigs['commitment']['score']} pts*{nd('commitment')}\n"
        f"{sig_dot(sigs['carryover']['score'])}  *Carryover*   {sigs['carryover']['raw']}  →  *{sigs['carryover']['score']} pts*{nd('carryover')}\n"
        f"{sig_dot(sigs['cycle_time']['score'])}  *Cycle Time*  {sigs['cycle_time']['raw']}  →  *{sigs['cycle_time']['score']} pts*{nd('cycle_time')}\n"
        f"{sig_dot(sigs['bug_ratio']['score'])}  *Bug Ratio*   {sigs['bug_ratio']['raw']}  →  *{sigs['bug_ratio']['score']} pts*{nd('bug_ratio')}\n"
        f"🐛  *New Bugs*  {r['new_bugs']} created ({r['new_bugs_done']} resolved)   |   📦 *Carried* {r['carried_bugs']}"
    )

    bd = r.get("burndown", {})
    bd_line = ""
    if bd:
        track_icon = "✅" if bd.get("on_track") else ("⚠️" if not bd.get("is_extended") else "🔴")
        ext_note   = " _(sprint overran)_" if bd.get("is_extended") else ""
        bd_line    = (
            f"\n*Burndown*  Day {bd['elapsed_days']}/{bd['total_days']}  ·  "
            f"{_format_decimal(float(bd['current_remaining']), 0)} scope remaining  ·  Ideal: {_format_decimal(float(bd['ideal_remaining']), 0)}  ·  "
            f"{track_icon} {'On track' if bd.get('on_track') else 'Behind'}{ext_note}  ·  "
            f"Velocity: {bd['velocity']}/day  ·  Projected: {bd['projected_end']}\n"
        )

    formula_line = (
        f"`{sigs['commitment']['score']}x{weights['commitment']:.2f}` + "
        f"`{sigs['carryover']['score']}x{weights['carryover']:.2f}` + "
        f"`{sigs['cycle_time']['score']}x{weights['cycle_time']:.2f}` + "
        f"`{sigs['bug_ratio']['score']}x{weights['bug_ratio']:.2f}`"
    )
    if r.get("bd_nudge"):
        formula_line += f" + burndown `{r['bd_nudge']:+d}`"
    formula_line += f"  =  *{fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}*  =  *{score}*"

    status_lines = "\n".join(
        f"  • {k}: {v}" for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1])
    ) or "  • No issues found"

    no_data_note   = "\n> ℹ️ _No issues yet — neutral score of 70 used._\n" if r["no_data_signals"] else ""
    state_banner   = ""
    if r["sprint_state"] == "extended":
        state_banner = "\n> ⚠️ _Sprint passed end date — not yet closed._\n"
    elif r["sprint_state"] == "closed":
        state_banner = "\n> 📋 _Showing last closed sprint._\n"

    date_range    = f"{r['sprint_start']} → {r['sprint_end']}" if r["sprint_start"] and r["sprint_end"] else "Dates not set"
    progress_note = f"   ·   Day {r.get('elapsed_days','?')}/{r.get('total_days','?')} ({r['sprint_progress_pct']}%)" if r.get("sprint_progress_pct") is not None else ""

    selected_activity_option = next(
        (option for option in (r.get("activity_date_options") or []) if option.get("is_default")),
        ((r.get("activity_date_options") or [{}])[0]),
    )
    selected_activity_key = selected_activity_option.get("key") or ""
    selected_activity_label = selected_activity_option.get("label") or "Today"
    dev_activity_for_slack = (r.get("dev_activity") or {}).get(selected_activity_key, [])
    qa_activity_for_slack = (r.get("qa_activity") or {}).get(selected_activity_key, [])

    # Dev activity for Slack
    dev_lines = ""
    if dev_activity_for_slack:
        dev_lines = f"\n*Developer Activity — {selected_activity_label}*\n"
        for dev in dev_activity_for_slack:
            stale_count = sum(1 for i in dev["issues"] if i["is_stale"])
            stale_note  = f" ⚠️ {stale_count} stale" if stale_count else ""
            dev_lines  += f"  👤 *{dev['name']}* — {len(dev['issues'])} issue(s){stale_note}\n"
            for iss in dev["issues"]:
                icon, _ = ALL_ISSUE_TYPES.get(iss["type"], DEFAULT_ISSUE_ICON)
                stale_tag  = " 🔴 _stale_" if iss["is_stale"] else ""
                active_tag = f" _(active {iss['active_days']}d)_" if iss["active_days"] > 1 else ""
                rft_tag    = f" _(🕐 {format_duration_hours(iss['time_in_rft'])} testing)_" if iss.get("time_in_rft", 0) > 0 else ""
                dev_lines += f"    {icon} {iss['key']} · {iss['status']}{active_tag}{rft_tag}{stale_tag}\n"

    # QA activity for Slack
    qa_lines = ""
    if qa_activity_for_slack:
        qa_lines = f"\n*QA Activity — {selected_activity_label}*\n"
        for item in qa_activity_for_slack:
            icon, _ = ALL_ISSUE_TYPES.get(item["type"], DEFAULT_ISSUE_ICON)
            rft_tag  = f" _(🕐 {format_duration_hours(item['time_in_rft'])})_" if item.get("time_in_rft", 0) > 0 else ""
            qa_lines += f"  {icon} *{item['key']}* {item['label']}{rft_tag} · {item['summary'][:50]}\n"

    return (
        f"📊  *Sprint Health Report*  —  Lumofy QA\n"
        f"*{r['sprint_name']}*   ·   {date_range}{progress_note}\n"
        f"{'—' * 44}\n\n"
        f"{health_dot}  *Health Score:  {score} / 100*\n"
        f"`{bar}`\n_{r['health_label'].title()}_\n"
        f"{state_banner}{no_data_note}\n"
        f"*Signals*\n{sig_rows}\n{bd_line}\n"
        f"*Formula*\n{formula_line}\n\n"
        f"{'—' * 44}\n"
        f"*Issue Status*\n{status_lines}\n"
        f"{dev_lines}{qa_lines}\n"
        f"🐛 Bugs: *{r['bugs']}*   |   📦 Scope: *{r['total']}*   |   🚧 Blockers: *{r['blocked_count']}*\n\n"
        f"_Generated {r['generated_at']}  ·  Lumofy QA Dashboard_"
    )

def format_slack_site_message(r: dict, site_url: str, pdf_url: str = "") -> str:
    score      = r["health_score"]
    health_dot = "🟢" if score >= 85 else "🟡" if score >= 70 else "🟠" if score >= 50 else "🔴"
    bugs_line  = f"New Bugs: {r['new_bugs']} | Carried: {r['carried_bugs']}"
    if r.get("bug_change_pct") is not None:
        p = abs(r["bug_change_pct"])
        bugs_line = f"New Bugs: {r['new_bugs']} ({r['bug_change_arrow']} {int(p) if float(p).is_integer() else p}%) | Carried: {r['carried_bugs']}"
    cycle_time = f"{r['current_avg_cycle_time']} days" if r.get("current_avg_cycle_time") is not None else "N/A"
    bd      = r.get("burndown", {})
    bd_note = f"\nBurndown: {_format_decimal(float(bd['current_remaining']), 0)} scope remaining · {'✅ On track' if bd.get('on_track') else '⚠️ Behind'}" if bd else ""
    return (
        f"🚀 Sprint Health Report Ready — Lumofy QA\n\nScore: {score}/100 {health_dot}\n"
        f"{bugs_line}\nCycle Time: {cycle_time}{bd_note}\n\n🔗 View Report:\n{site_url}"
    )

def _render_activity_date_select(date_options: list[dict], select_label: str) -> str:

    if not date_options: return ""
    initial = next((o for o in date_options if o.get("is_default")), date_options[0])
    opts = "".join(f"<button type='button' class='activity-date-option{' active' if o.get('is_default') else ''}' data-date-option='{escape(o['key'])}'>{escape(o['label'])}</button>" for o in date_options)
    return f"<div class='activity-date-filter' data-date-dropdown='true' aria-label='{escape(select_label)}'><div class='activity-date-label'>{escape(select_label)}</div><button type='button' class='activity-date-trigger' aria-haspopup='listbox' aria-expanded='false'><span class='activity-date-trigger-text' data-date-value>{escape(initial['label'])}</span></button><div class='activity-date-menu' role='listbox'>{opts}</div></div>"

def _build_dev_activity_html(dev_activity: dict[str, list], date_options: list[dict]) -> str:
    if not any(dev_activity.get(o["key"], []) for o in date_options):
        return "<div class='qa-dashboard-shell dev-dashboard-shell interactive-activity-shell empty'><div class='qa-dashboard-empty'>No developer activity recorded in the last 7 days.</div></div>"
    
    def _type_meta(t):
        n = (t or "").strip().lower()
        if n == "bug": return "BUG", "qa-type-bug", "qa-card-bug", "Bug"
        if n == "story": return "STORY", "qa-type-story", "qa-card-story", "Story"
        if n == "task": return "TASK", "qa-type-task", "qa-card-task", "Task"
        if n == "sub-task": return "SUB", "qa-type-sub", "qa-card-sub", "Sub-task"
        return "ENH", "qa-type-enh", "qa-card-enh", "Enhancement"

    def _outcome_meta(i):
        s, transitions = (i.get("status") or "").lower(), " | ".join(i.get("transitions_today") or []).lower()
        if i.get("is_stale"): return "Needs Attention", "qa-status-reopened"
        if i.get("is_done"): return "Done Today", "qa-status-done"
        if "code review" in s or "code review" in transitions: return "In Review", "qa-status-progress"
        if "ready for testing" in s: return "Ready For QA", "qa-status-passed"
        if any(x in s for x in ["pm review", "release"]): return "Ready", "qa-status-passed"
        return "Working", "qa-status-testing"

    html = f"<div class='qa-dashboard-shell dev-dashboard-shell interactive-activity-shell'><div class='qa-dashboard-head'><div><div class='qa-dashboard-title'>Developer Activity</div><div class='qa-dashboard-subtitle'>Status changes grouped by developer.</div></div><div class='activity-head-controls'><label class='qa-dashboard-search'><span class='qa-search-icon'>⌕</span><input type='search' class='qa-search-input' placeholder='Search...'><span class='qa-filter-icon'>⌯</span></label>{_render_activity_date_select(date_options, 'Date')}</div></div>"
    for option in date_options:
        day_items = dev_activity.get(option["key"], []) or []
        counts = {"all": 0, "bug": 0, "story": 0, "enh": 0, "task": 0, "sub": 0}
        for d in day_items:
            for iss in d.get("issues", []):
                counts["all"] += 1
                tp = (iss.get("type") or "").lower()
                if tp in counts: counts[tp] += 1
                elif tp == "sub-task": counts["sub"] += 1
                else: counts["enh"] += 1
        
        tabs = "".join(f"<button type='button' class='qa-tab{ ' active' if k=='all' else ''}' data-filter='{k}'>{k.title()} <strong>{v}</strong></button>" for k, v in counts.items() if v > 0 or k == 'all')
        html += f"<div class='activity-date-pane{ ' active' if option.get('is_default') else ''}' data-date='{escape(option['key'])}'><div class='qa-tabs' role='tablist'>{tabs}</div>"
        
        if not day_items: html += "<div class='qa-dashboard-empty'>No activity for this date.</div></div>"; continue
        
        html += "<div class='qa-tester-list'>"
        for dev in sorted(day_items, key=lambda x: -len(x.get("issues", []))):
            issues = dev.get("issues", [])
            html += f"<details class='qa-tester-section' open><summary class='qa-tester-summary'><div class='qa-tester-summary-left'>{_person_avatar_html(dev['name'], dev.get('avatar'), 'qa-tester-avatar')}<div class='qa-tester-name'>{escape(dev['name'])}</div><div class='qa-tester-count'>{len(issues)} issues</div></div><div class='qa-tester-chevron'></div></summary><div class='qa-tester-body'><div class='qa-issue-grid'>"
            for idx, iss in enumerate(issues):
                lbl, cls, c_cls, _ = _type_meta(iss["type"])
                outcome, outcome_cls = _outcome_meta(iss)
                trans_main = (iss.get("transitions_today") or [iss.get("status")])[-1]
                trans_sub = f"Active {iss.get('active_days')}d" if iss.get("active_days") else "Updated today"
                l_story = f"<div class='qa-linked-story'><span class='qa-linked-story-label'>Story</span><span class='qa-linked-story-value'>{escape(iss['linked_story'])}</span></div>" if iss.get("linked_story") else ""
                html += f'<article class="qa-issue-card {c_cls}{" hidden-by-limit" if idx >= 6 else ""}" data-activity-card="true" data-type="{lbl.lower()}" data-search="{escape(str(iss))}"><div class="qa-issue-top"><div class="qa-issue-type {cls}">{lbl}</div><a href="{iss["url"]}" class="qa-issue-key">{iss["key"]}</a></div><a href="{iss["url"]}" class="qa-issue-title">{escape(iss["summary"][:68])}</a>{l_story}<div class="qa-issue-transition"><div class="qa-issue-transition-main">{escape(trans_main)}</div><div class="qa-issue-transition-sub">{escape(trans_sub)}</div></div><div class="qa-issue-tags"><span class="qa-mini-pill {outcome_cls}">{outcome}</span></div></article>'
            html += f"</div>{f'<button type=\"button\" class=\"qa-show-more\" data-expand=\"6\">Show More <span>+{len(issues)-6}</span></button>' if len(issues)>6 else ''}</div></details>"
        html += "</div></div>"
    return html + "</div>"

def _build_qa_activity_html(qa_items: dict[str, list], date_options: list[dict]) -> str:
    # Logic similar to dev activity but for QA
    if not any(qa_items.get(o["key"], []) for o in date_options):
        return "<div class='qa-dashboard-shell interactive-activity-shell empty'><div class='qa-dashboard-empty'>No QA activity recorded.</div></div>"
    
    html = f"<div class='qa-dashboard-shell interactive-activity-shell'><div class='qa-dashboard-head'><div><div class='qa-dashboard-title'>QA Activity</div><div class='qa-dashboard-subtitle'>Transitions by QA team.</div></div><div class='activity-head-controls'><label class='qa-dashboard-search'><span class='qa-search-icon'>⌕</span><input type='search' class='qa-search-input' placeholder='Search...'><span class='qa-filter-icon'>⌯</span></label>{_render_activity_date_select(date_options, 'Date')}</div></div>"
    
    for option in date_options:
        day_items = qa_items.get(option["key"], []) or []
        # Group by actor
        by_actor = {}
        for item in day_items:
            actor = item.get("actor", "Unknown")
            if actor not in by_actor: by_actor[actor] = {"name": actor, "avatar": item.get("actor_avatar"), "issues": []}
            by_actor[actor]["issues"].append(item)
            
        html += f"<div class='activity-date-pane{ ' active' if option.get('is_default') else ''}' data-date='{escape(option['key'])}'>"
        if not by_actor: html += "<div class='qa-dashboard-empty'>No activity for this date.</div></div>"; continue
        
        html += "<div class='qa-tester-list'>"
        for actor in sorted(by_actor.values(), key=lambda x: -len(x["issues"])):
            issues = actor["issues"]
            html += f"<details class='qa-tester-section' open><summary class='qa-tester-summary'><div class='qa-tester-summary-left'>{_person_avatar_html(actor['name'], actor.get('avatar'), 'qa-tester-avatar')}<div class='qa-tester-name'>{escape(actor['name'])}</div><div class='qa-tester-count'>{len(issues)} issues</div></div><div class='qa-tester-chevron'></div></summary><div class='qa-tester-body'><div class='qa-issue-grid'>"
            for idx, iss in enumerate(issues):
                # Simplified QA card
                html += f'<article class="qa-issue-card qa-card-story{" hidden-by-limit" if idx >= 6 else ""}" data-activity-card="true" data-search="{escape(str(iss))}"><div class="qa-issue-top"><div class="qa-issue-type qa-type-story">{iss.get("type", "ITEM")}</div><a href="{iss.get("url")}" class="qa-issue-key">{iss.get("key")}</a></div><a href="{iss.get("url")}" class="qa-issue-title">{escape(iss.get("summary","")[:68])}</a><div class="qa-issue-transition"><div class="qa-issue-transition-main">{escape(iss.get("status",""))}</div><div class="qa-issue-transition-sub">Updated by QA</div></div></article>'
            html += "</div></div></details>"
        html += "</div></div>"
    return html + "</div>"

def _build_todays_bug_reports_html(bugs: dict[str, list], date_options: list[dict]) -> str:
    if not any(bugs.get(o["key"], []) for o in date_options):
        return "<div class='bug-report-shell empty'><div class='bug-report-empty'>No bugs created.</div></div>"
    
    html = f"<div class='bug-report-shell interactive-activity-shell'><div class='bug-report-head'><div><div class='bug-report-title'>New Bugs</div><div class='bug-report-subtitle'>Created items by reporter.</div></div>{_render_activity_date_select(date_options, 'Date')}</div>"
    for option in date_options:
        day_bugs = bugs.get(option["key"], []) or []
        html += f"<div class='activity-date-pane{ ' active' if option.get('is_default') else ''}' data-date='{escape(option['key'])}'>"
        if not day_bugs: html += "<div class='bug-report-empty'>None.</div></div>"; continue
        
        # simplified bug display
        html += "<div class='bug-person-grid'>"
        for bug in day_bugs:
            html += f'<article class="bug-ticket-card open"><div class="bug-ticket-top"><span class="bug-ticket-type bug-ticket-type-bug">BUG</span><a href="{bug.get("url")}" class="bug-ticket-key">{bug.get("key")}</a><span class="bug-ticket-status open">{escape(bug.get("status","Open"))}</span></div><a href="{bug.get("url")}" class="bug-ticket-summary">{escape(bug.get("summary","")[:110])}</a></article>'
        html += "</div></div>"
    return html + "</div>"

def _build_sprint_details_html(r: dict) -> str:
    """Combines all detailed metrics panels into one section."""
    medians_html = _build_cycle_time_medians_panel_html(r.get("cycle_time_medians", {}))
    bottlenecks_html = _build_blocked_time_ratio_panel_html(r.get("bottlenecks", {}))
    issue_types_html = _build_issue_type_breakdown_panel(r.get("issue_type_counts", {}), r.get("total", 0))
    assignee_html = _build_assignee_workload_panel(r.get("assignee_counts", {}), r.get("total", 0))
    
    return f"""
    <div class="section-title">Sprint Performance Details</div>
    <div class="card">
        <div class="details-grid">
            <div class="details-column">
                <div class="details-title">Cycle Time Medians</div>
                {medians_html}
            </div>
            <div class="details-column">
                <div class="details-title">Blocked Time & Bottlenecks</div>
                {bottlenecks_html}
            </div>
        </div>
    </div>
    
    <div class="section-title">Work Distribution</div>
    <div class="card">
        <div class="details-grid">
            <div class="details-column">
                <div class="details-title">Issue Type Breakdown</div>
                {issue_types_html}
            </div>
            <div class="details-column">
                <div class="details-title">Assignee Workload</div>
                {assignee_html}
            </div>
        </div>
    </div>
    """

def write_html_report(r: dict, output_path: str = "sprint_health_report.html") -> str:
    template_path = Path(__file__).parent / "dashboard_template.html"
    css_path = Path(__file__).parent / "dashboard_style.css"
    js_path = Path(__file__).parent / "dashboard_script.js"
    
    if not template_path.exists():
        return f"Error: Template not found at {template_path}"
    
    html = template_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    js = js_path.read_text(encoding="utf-8") if js_path.exists() else ""
    
    bd = r.get("burndown", {})
    weights = r.get("weights", {})
    fb = r.get("formula_breakdown", {})
    sigs = r.get("signals", {})
    
    replacements = {
        "{{DASHBOARD_CSS}}": css,
        "{{DASHBOARD_JS}}": js,
        "{{SPRINT_NAME}}": escape(r['sprint_name']),
        "{{DATE_RANGE}}": f"{r['sprint_start']} → {r['sprint_end']}",
        "{{ELAPSED_DAYS}}": str(r.get('elapsed_days', '?')),
        "{{TOTAL_DAYS}}": str(r.get('total_days', '?')),
        "{{PROGRESS_PCT}}": str(round(r.get('sprint_progress_pct', 0))),
        "{{SCORE}}": str(r['health_score']),
        "{{SCORE_CLASS}}": r['health_color'],
        "{{HEALTH_LABEL}}": escape(r['health_label'].title()),
        "{{GENERATED_AT}}": escape(r['generated_at']),
        "{{STATE_BANNER}}": f"<div class='state-banner {r['sprint_state']}'>{escape(r['sprint_status_note'])}</div>" if r.get('sprint_status_note') else "",
        "{{NO_DATA_BANNER}}": "<div class='no-data-banner'>Neutral scores used for signals with zero matching issues.</div>" if r.get('no_data_signals') else "",
        "{{HEALTH_SIGNALS_FORMULA_HTML}}": "<div class='signals-formula-note'>Score = (Commitment × 0.4) + (Carryover × 0.3) + (Cycle Time × 0.2) + (Bug Ratio × 0.1)</div>",
        "{{SIGNALS_HTML}}": "".join(f'<div class="signal-card"><div class="signal-label">{k.upper()}</div><div class="signal-score {v["color"]}">{v["score"]}<span class="signal-unit">pts</span></div><div class="signal-metric">{v["raw"]}</div></div>' for k, v in sigs.items()),
        "{{BUG_CARDS_HTML}}": f'<div class="bug-cards"><div class="bug-card new-bugs"><div class="bug-card-title">New Bugs</div><div class="bug-card-count">{r["new_bugs"]}</div></div><div class="bug-card carried-bugs"><div class="bug-card-title">Carried Bugs</div><div class="bug-card-count">{r["carried_bugs"]}</div></div></div>',
        "{{BURNDOWN_STATS}}": f'<div class="bd-stats"><div class="bd-stat"><div class="bd-stat-val">{_format_decimal(float(bd.get("current_remaining",0)),0)}</div><div class="bd-stat-lbl">Remaining</div></div><div class="bd-stat"><div class="bd-stat-val">{bd.get("velocity",0)}/d</div><div class="bd-stat-lbl">Velocity</div></div></div>' if bd else "",
        "{{BURNDOWN_SVG}}": _build_burndown_svg(bd),
        "{{BURNDOWN_BREAKDOWN_HTML}}": _build_remaining_scope_breakdown_html(bd),
        "{{BURNDOWN_EXPLAINER_HTML}}": _build_burndown_explainer_html(bd),
        "{{BURNDOWN_TAKEAWAYS_HTML}}": _build_burndown_takeaways_html(bd),
        "{{DEV_ACTIVITY_HTML}}": _build_dev_activity_html(r.get('dev_activity',{}), r.get('activity_date_options',[])),
        "{{QA_ACTIVITY_HTML}}": _build_qa_activity_html(r.get('qa_activity',{}), r.get('activity_date_options',[])),
        "{{TODAY_BUG_REPORTS_HTML}}": _build_todays_bug_reports_html(r.get('today_bug_reports',{}), r.get('activity_date_options',[])),
        "{{AI_HTML}}": r.get('ai_insights_html', ''),
        "{{FORMULA_BREAKDOWN_HTML}}": "".join(f'<div class="formula-row"><span>{k.title()}</span><strong>{fb.get(k,0)}</strong></div>' for k in ['commitment', 'carryover', 'cycle_time', 'bug_ratio']),
        "{{FORMULA_FINAL_TEXT}}": f"{fb.get('commitment',0)} + {fb.get('carryover',0)} + {fb.get('cycle_time',0)} + {fb.get('bug_ratio',0)}",
        "{{SPRINT_DETAILS_HTML}}": _build_sprint_details_html(r),
    }
    
    for k, v in replacements.items():
        html = html.replace(k, str(v))
        
    out = Path(output_path)
    out.write_text(html, encoding="utf-8")
    print(f"[ok] HTML report updated via template: {out.resolve()}")
    return str(out.resolve())
