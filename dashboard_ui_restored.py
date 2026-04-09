import os, json, datetime
from typing import List, Dict

# High-Fidelity Design Logic (Restored from Main)

def _person_avatar_html(name: str, avatar_url: str | None, class_name: str = "qa-tester-avatar") -> str:
    initials = escape(_person_initials(name))
    safe_name = escape(name or "Unknown")
    safe_url = escape(avatar_url or "")
    if safe_url:
        return (
            f"<div class='{class_name}'>"
            f"<img src='{safe_url}' alt='{safe_name}' loading='lazy' referrerpolicy='no-referrer' "
            f"onerror=\"this.style.display='none';this.nextElementSibling.style.display='flex';\">"
            f"<span class='{class_name}-fallback' style='display:none'>{initials}</span>"
            f"</div>"
        )
    return f"<div class='{class_name}'><span class='{class_name}-fallback'>{initials}</span></div>"

def write_html_report(r: dict, output_path: str = "sprint_health_report.html") -> str:
    score       = r["health_score"]
    score_class = "green" if score >= 85 else "yellow" if score >= 70 else "orange" if score >= 50 else "red"
    fb          = r["formula_breakdown"]
    sigs        = r["signals"]
    bd          = r.get("burndown", {})
    weights     = r["weights"]
    thresholds  = r["signal_thresholds"]
    benchmark_summaries = _signal_benchmark_summaries()
    ai_insights = r.get("ai_insights")

    def signal_color(s): return "green" if s >= 85 else "yellow" if s >= 70 else "orange" if s >= 50 else "red"
    def nd_badge(k):
        return '<span class="no-data-badge">no data ΓÇö neutral</span>' if r["signals"][k].get("no_data") else ""
    def bug_linkage_html(counts: dict) -> str:
        counts = counts or {}
        parts = [
            ("Story", counts.get("story", 0)),
            ("Enh/Task", counts.get("enhancement_task", 0)),
            ("No Link", counts.get("no_link", 0)),
        ]
        if counts.get("other", 0):
            parts.append(("Other", counts.get("other", 0)))
        return "".join(
            f"<span class='bug-link-pill'><strong>{value}</strong> {escape(label)}</span>"
            for label, value in parts
        )

    issue_type_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td><td>{round(v/r['total']*100,1) if r['total'] else 0}%</td></tr>"
        for k, v in r["issue_type_counts"].items()
    ) or "<tr><td colspan='3'>No data</td></tr>"

    assignee_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td>"
        f"<td><div class='bar'><span style='width:{round(v/r['total']*100,1) if r['total'] else 0}%'></span></div></td></tr>"
        for k, v in list(r["assignee_counts"].items())[:10]
    ) or "<tr><td colspan='3'>No data</td></tr>"

    carryover_rows = "\n".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(r["unfinished_status_counts"].items(), key=lambda x: -x[1])
    ) or "<tr><td colspan='2'>No unfinished work</td></tr>"

    _co = max(r["carried_over"], 1)
    age_rows = "\n".join(
        f"<tr><td>{k}</td><td>{v}</td><td><div class='bar'><span style='width:{round(v/_co*100,1)}%'></span></div></td></tr>"
        for k, v in r["age_buckets"].items()
    )

    signal_defs = [
        {"key": "commitment", "label": "Commitment Reliability", "score": sigs["commitment"]["score"],
         "metric": sigs["commitment"]["raw"], "pct": sigs["commitment"]["pct"],
         "formula": "Completed committed scope ├╖ Total committed sprint scope ├ù 100"},
        {"key": "carryover",  "label": "Carryover Rate", "score": sigs["carryover"]["score"],
         "metric": sigs["carryover"]["raw"], "pct": sigs["carryover"]["pct"],
         "formula": "Official rollover from previous sprint ├╖ Total scope in current sprint ├ù 100"},
        {"key": "cycle_time", "label": "Cycle Time Stability", "score": sigs["cycle_time"]["score"],
         "metric": sigs["cycle_time"]["raw"],
         "pct": f"{sigs['cycle_time']['pct']}% vs 3-sprint avg" if sigs["cycle_time"]["pct"] is not None else "No baseline",
         "formula": "Current avg cycle time for completed work vs previous 3-sprint avg"},
        {"key": "bug_ratio",  "label": "Bug Ratio (New Only)", "score": sigs["bug_ratio"]["score"],
         "metric": sigs["bug_ratio"]["raw"], "pct": sigs["bug_ratio"]["pct"],
         "formula": "New bugs created during this sprint ├╖ total story scope"},
    ]
    health_signals_formula_html = (
        "<div class='signals-formula-note'>"
        "Simple formula: we convert each signal to a score out of 100, then final health = "
        "Commitment 35% + Carryover 25% + Cycle Time 20% + Bug Ratio 20% + Burndown adjustment."
        "</div>"
    )
    signals_html = ""
    for sd in signal_defs:
        sc = signal_color(sd["score"])
        signals_html += f"""
        <div class="signal-card">
          <div class="signal-label">{sd['label']}</div>
          <div class="signal-score {sc}">{sd['score']}<span class="signal-unit">/100</span></div>
          <div class="signal-metric">
            <span class="signal-metric-main">{sd['metric']}</span>
            <span class="signal-metric-sep">ΓÇó</span>
            <span class="signal-metric-pct">{sd['pct']}%</span>
          </div>
          <div class="signal-benchmark">{escape(benchmark_summaries.get(sd['key'], ''))}</div>
          {nd_badge(sd['key'])}
        </div>"""

    bug_ratio_base_work = (r.get("bug_ratio_base") or {}).get("base_work", 0.0)
    new_story_linked_bugs = r.get("new_story_linked_bugs", 0)
    new_bug_pct      = round(new_story_linked_bugs / bug_ratio_base_work * 100, 1) if bug_ratio_base_work else 0
    new_bugs_res_pct = round(r["new_bugs_done"] / r["new_bugs"] * 100, 1) if r["new_bugs"] else 0
    new_bug_linkage_html = bug_linkage_html(r.get("new_bug_linkage", {}))
    carried_bug_linkage_html = bug_linkage_html(r.get("carried_bug_linkage", {}))
    bug_story_insights = r.get("bug_story_insights") or {}
    top_bug_engineer = bug_story_insights.get("top_engineer_name") or "N/A"
    top_bug_engineer_count = bug_story_insights.get("top_engineer_bug_count", 0)
    avg_bugs_per_story = bug_story_insights.get("avg_bugs_per_story", 0)
    affected_story_count = bug_story_insights.get("unique_story_count", 0)
    story_bug_count = bug_story_insights.get("story_bug_count", 0)
    bug_cards_html   = f"""
    <div class="bug-cards">
      <div class="bug-card new-bugs">
        <div class="bug-card-icon">NEW</div>
        <div class="bug-card-title">New Bugs</div>
        <div class="bug-card-count">{r['new_bugs']}</div>
        <div class="bug-card-sub">Created this sprint</div>
        <div class="bug-card-ratio">Bug Ratio: <strong>{new_bug_pct}%</strong> of story scope</div>
        <div class="bug-card-resolved">Resolved: <strong>{r['new_bugs_done']}</strong> ({new_bugs_res_pct}%)</div>
        <div class="bug-linkage-row">{new_bug_linkage_html}</div>
        <div class="bug-card-note">Counts toward Health Score</div>
      </div>
      <div class="bug-card carried-bugs">
        <div class="bug-card-icon">OLD</div>
        <div class="bug-card-title">Carried Bugs</div>
        <div class="bug-card-count">{r['carried_bugs']}</div>
        <div class="bug-card-sub">From previous sprints</div>
        <div class="bug-linkage-row">{carried_bug_linkage_html}</div>
        <div class="bug-card-note">Display only - not in Health Score</div>
      </div>
    </div>
    <div class="bug-insight-grid">
      <div class="bug-insight-card">
        <div class="bug-insight-label">Average Bugs per Story</div>
        <div class="bug-insight-value">{avg_bugs_per_story}</div>
        <div class="bug-insight-sub">{story_bug_count} bugs linked to {affected_story_count} stor{'y' if affected_story_count == 1 else 'ies'}</div>
      </div>
      <div class="bug-insight-card">
        <div class="bug-insight-label">Most Bugs on Stories</div>
        <div class="bug-insight-value">{escape(top_bug_engineer)}</div>
        <div class="bug-insight-sub">{top_bug_engineer_count} bug{'s' if top_bug_engineer_count != 1 else ''} linked to stories</div>
      </div>
    </div>"""

    burndown_svg  = _build_burndown_svg(bd)
    burndown_explainer_html = _build_burndown_explainer_html(bd)
    burndown_breakdown_html = _build_remaining_scope_breakdown_html(bd)
    burndown_takeaways_html = _build_burndown_takeaways_html(bd)
    bd_track_cls  = "green" if bd.get("on_track") else "red"
    bd_track_txt  = "On track" if bd.get("on_track") else "Behind ideal"
    if bd.get("is_extended"): bd_track_cls, bd_track_txt = "red", "Sprint overran"
    burndown_stats = ""
    if bd:
        burndown_stats = f"""
        <div class="bd-stats">
          <div class="bd-stat"><div class="bd-stat-val">{bd['elapsed_days']}/{bd['total_days']}</div><div class="bd-stat-lbl">Days Elapsed</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{_format_decimal(float(bd['current_remaining']), 0)}</div><div class="bd-stat-lbl">Scope Remaining</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{_format_decimal(float(bd['ideal_remaining']), 0)}</div><div class="bd-stat-lbl">Ideal Scope Remaining</div></div>
          <div class="bd-stat"><div class="bd-stat-val {bd_track_cls}">{bd_track_txt}</div><div class="bd-stat-lbl">Status</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{bd['velocity']}/day</div><div class="bd-stat-lbl">Velocity</div></div>
          <div class="bd-stat"><div class="bd-stat-val">{bd['projected_end']}</div><div class="bd-stat-lbl">Projected Finish</div></div>
        </div>"""

    no_data_banner = '<div class="no-data-banner">Some signals had no data yet. A neutral score of 70 was used.</div>' if r["no_data_signals"] else ""
    state_banner   = ""
    if r["sprint_state"] == "extended":
        state_banner = '<div class="state-banner extended">Sprint has passed its end date but has not been closed in Jira yet.</div>'
    elif r["sprint_state"] == "closed":
        state_banner = '<div class="state-banner closed">No active sprint - showing data from the most recently closed sprint.</div>'

    progress_pct = r.get("sprint_progress_pct") or 0
    date_range   = f"{escape(r['sprint_start'])} -> {escape(r['sprint_end'])}" if r["sprint_start"] and r["sprint_end"] else "Dates not set"
    bd_nudge_html = ""
    if r.get("bd_nudge"):
        bd_nudge_html = (
            f"<div class='formula-row'><div class='formula-component'><span>Burndown Nudge</span>"
            f"<span class='formula-code'>{r['bd_nudge']:+d} pts</span></div>"
            f"<strong>{'bonus' if r['bd_nudge'] > 0 else 'penalty'}</strong></div>"
        )

    dev_activity_html = _build_dev_activity_html(r.get("dev_activity", {}), r.get("activity_date_options", []))
    qa_activity_html  = _build_qa_activity_html(r.get("qa_activity", {}), r.get("activity_date_options", []))
    today_bug_reports_html = _build_todays_bug_reports_html(r.get("today_bug_reports", {}), r.get("activity_date_options", []))
    sprint_details_html = _build_sprint_details_html(r)

    ai_html = ""
    if ai_insights and _config_ai().get("include_in_html"):
        actions_html = "".join(f"<li>{escape(i)}</li>" for i in ai_insights.get("actions", []))
        ai_html = f"""
  <div class="section-title">AI Insight</div>
  <div class="card">
    <div class="ai-title">{escape(ai_insights.get('title','AI Insight'))}</div>
    <div class="ai-summary">{escape(ai_insights.get('summary',''))}</div>
    {'<ul class="ai-actions">' + actions_html + '</ul>' if actions_html else ''}
  </div>"""

    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sprint Health - {escape(r['sprint_name'])}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}