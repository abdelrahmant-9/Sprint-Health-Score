import os, json, datetime, socket, subprocess, sys, argparse, hashlib, time
import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from pathlib import Path

class SprintState:
    def __init__(self, sprint: dict | None):
        self.sprint    = sprint or {}
        self.state     = self._detect()
        self.name      = self.sprint.get('name', 'Unknown Sprint')
        self.start_str = _parse_sprint_date(self.sprint, 'startDate', 'start_date')
        self.end_str   = _parse_sprint_date(self.sprint, 'endDate', 'end_date', 'completeDate')

    def _detect(self) -> str:
        if not self.sprint: return 'empty'
        raw = (self.sprint.get('state') or '').lower()
        if raw == 'active':
            end_str = _parse_sprint_date(self.sprint, 'endDate', 'end_date')
            if end_str:
                end_dt = _parse_date_str(end_str)
                if end_dt and datetime.now(timezone.utc).date() > end_dt.date():
                    return 'extended'
            return 'active'
        if raw == 'closed': return 'closed'
        return 'active'

    @property
    def is_active(self): return self.state in ('active', 'extended')

    @property
    def elapsed_days(self) -> int | None:
        start = _parse_date_str(self.start_str)
        if not start: return None
        return max(0, (datetime.now(timezone.utc).date() - start.date()).days)

    @property
    def total_days(self) -> int | None:
        start = _parse_date_str(self.start_str)
        end   = _parse_date_str(self.end_str)
        if not start or not end: return None
        return max(1, (end.date() - start.date()).days)

    @property
    def sprint_progress_pct(self) -> float | None:
        el, to = self.elapsed_days, self.total_days
        if el is None or to is None: return None
        return round(min(100.0, el / to * 100), 1)


def _parse_date_str(value: str) -> datetime | None:
    if not value: return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_sprint_date(sprint_info: dict, *keys: str) -> str:
    for key in keys:
        val = sprint_info.get(key)
        if val: return str(val)[:10]
    return ''



# High-Fidelity UI Logic Restored from Main Branch

def _format_decimal(value: float, places: int = 2) -> str:
    text = f"{value:.{places}f}"

def format_duration_hours(hours_value: float | int | None) -> str:
    if hours_value is None:
        return "0 min"
    try:
        hours_float = float(hours_value)
    except (TypeError, ValueError):
        return "0 min"
    if hours_float <= 0:
        return "0 min"
    if hours_float < 1:
        minutes = max(1, round(hours_float * 60))
        return f"{minutes} min"
    whole = int(hours_float)
    if abs(hours_float - whole) < 1e-9:
        return f"{whole} hour" if whole == 1 else f"{whole} hours"
    return f"{hours_float:.1f} hours"


def format_slack_message(r: dict) -> str:
    score      = r["health_score"]
    health_dot = "≡ƒƒó" if score >= 85 else "≡ƒƒí" if score >= 70 else "≡ƒƒá" if score >= 50 else "≡ƒö┤"
    filled     = round(score / 10)
    bar        = "Γûê" * filled + "Γûæ" * (10 - filled)

    def sig_dot(s): return "≡ƒƒó" if s >= 85 else "≡ƒƒí" if s >= 70 else "≡ƒƒá" if s >= 50 else "≡ƒö┤"
    def nd(k): return " _ΓÇö no data yet_" if r["signals"][k].get("no_data") else ""

    sigs    = r["signals"]
    fb      = r["formula_breakdown"]
    weights = r["weights"]

    sig_rows = (
        f"{sig_dot(sigs['commitment']['score'])}  *Commitment*  {sigs['commitment']['raw']}  ΓåÆ  *{sigs['commitment']['score']} pts*{nd('commitment')}\n"
        f"{sig_dot(sigs['carryover']['score'])}  *Carryover*   {sigs['carryover']['raw']}  ΓåÆ  *{sigs['carryover']['score']} pts*{nd('carryover')}\n"
        f"{sig_dot(sigs['cycle_time']['score'])}  *Cycle Time*  {sigs['cycle_time']['raw']}  ΓåÆ  *{sigs['cycle_time']['score']} pts*{nd('cycle_time')}\n"
        f"{sig_dot(sigs['bug_ratio']['score'])}  *Bug Ratio*   {sigs['bug_ratio']['raw']}  ΓåÆ  *{sigs['bug_ratio']['score']} pts*{nd('bug_ratio')}\n"
        f"≡ƒÉ¢  *New Bugs*  {r['new_bugs']} created ({r['new_bugs_done']} resolved)   |   ≡ƒôª *Carried* {r['carried_bugs']}"
    )

    bd = r.get("burndown", {})
    bd_line = ""
    if bd:
        track_icon = "Γ£à" if bd.get("on_track") else ("ΓÜá∩╕Å" if not bd.get("is_extended") else "≡ƒö┤")
        ext_note   = " _(sprint overran)_" if bd.get("is_extended") else ""
        bd_line    = (
            f"\n*Burndown*  Day {bd['elapsed_days']}/{bd['total_days']}  ┬╖  "
            f"{_format_decimal(float(bd['current_remaining']), 0)} scope remaining  ┬╖  Ideal: {_format_decimal(float(bd['ideal_remaining']), 0)}  ┬╖  "
            f"{track_icon} {'On track' if bd.get('on_track') else 'Behind'}{ext_note}  ┬╖  "
            f"Velocity: {bd['velocity']}/day  ┬╖  Projected: {bd['projected_end']}\n"
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
        f"  ΓÇó {k}: {v}" for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1])
    ) or "  ΓÇó No issues found"

    no_data_note   = "\n> Γä╣∩╕Å _No issues yet ΓÇö neutral score of 70 used._\n" if r["no_data_signals"] else ""
    state_banner   = ""
    if r["sprint_state"] == "extended":
        state_banner = "\n> ΓÜá∩╕Å _Sprint passed end date ΓÇö not yet closed._\n"
    elif r["sprint_state"] == "closed":
        state_banner = "\n> ≡ƒôï _Showing last closed sprint._\n"

    date_range    = f"{r['sprint_start']} ΓåÆ {r['sprint_end']}" if r["sprint_start"] and r["sprint_end"] else "Dates not set"
    progress_note = f"   ┬╖   Day {r.get('elapsed_days','?')}/{r.get('total_days','?')} ({r['sprint_progress_pct']}%)" if r.get("sprint_progress_pct") is not None else ""

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
        dev_lines = f"\n*Developer Activity ΓÇö {selected_activity_label}*\n"
        for dev in dev_activity_for_slack:
            stale_count = sum(1 for i in dev["issues"] if i["is_stale"])
            stale_note  = f" ΓÜá∩╕Å {stale_count} stale" if stale_count else ""
            dev_lines  += f"  ≡ƒæñ *{dev['name']}* ΓÇö {len(dev['issues'])} issue(s){stale_note}\n"
            for iss in dev["issues"]:
                icon, _ = ALL_ISSUE_TYPES.get(iss["type"], DEFAULT_ISSUE_ICON)
                stale_tag  = " ≡ƒö┤ _stale_" if iss["is_stale"] else ""
                active_tag = f" _(active {iss['active_days']}d)_" if iss["active_days"] > 1 else ""
                rft_tag    = f" _(≡ƒòÉ {format_duration_hours(iss['time_in_rft'])} testing)_" if iss.get("time_in_rft", 0) > 0 else ""
                dev_lines += f"    {icon} {iss['key']} ┬╖ {iss['status']}{active_tag}{rft_tag}{stale_tag}\n"

    # QA activity for Slack
    qa_lines = ""
    if qa_activity_for_slack:
        qa_lines = f"\n*QA Activity ΓÇö {selected_activity_label}*\n"
        for item in qa_activity_for_slack:
            icon, _ = ALL_ISSUE_TYPES.get(item["type"], DEFAULT_ISSUE_ICON)
            rft_tag  = f" _(≡ƒòÉ {format_duration_hours(item['time_in_rft'])})_" if item.get("time_in_rft", 0) > 0 else ""
            qa_lines += f"  {icon} *{item['key']}* {item['label']}{rft_tag} ┬╖ {item['summary'][:50]}\n"

    return (
        f"≡ƒôè  *Sprint Health Report*  ΓÇö  Lumofy QA\n"
        f"*{r['sprint_name']}*   ┬╖   {date_range}{progress_note}\n"
        f"{'ΓÇö' * 44}\n\n"
        f"{health_dot}  *Health Score:  {score} / 100*\n"
        f"`{bar}`\n_{r['health_label'].title()}_\n"
        f"{state_banner}{no_data_note}\n"
        f"*Signals*\n{sig_rows}\n{bd_line}\n"
        f"*Formula*\n{formula_line}\n\n"
        f"{'ΓÇö' * 44}\n"
        f"*Issue Status*\n{status_lines}\n"
        f"{dev_lines}{qa_lines}\n"
        f"≡ƒÉ¢ Bugs: *{r['bugs']}*   |   ≡ƒôª Scope: *{r['total']}*   |   ≡ƒÜº Blockers: *{r['blocked_count']}*\n\n"
        f"_Generated {r['generated_at']}  ┬╖  Lumofy QA Dashboard_"
    )


def format_slack_site_message(r: dict, site_url: str, pdf_url: str = "") -> str:
    score      = r["health_score"]
    health_dot = "≡ƒƒó" if score >= 85 else "≡ƒƒí" if score >= 70 else "≡ƒƒá" if score >= 50 else "≡ƒö┤"
    bugs_line  = f"New Bugs: {r['new_bugs']} | Carried: {r['carried_bugs']}"
    if r.get("bug_change_pct") is not None:
        p = abs(r["bug_change_pct"])
        bugs_line = f"New Bugs: {r['new_bugs']} ({r['bug_change_arrow']} {int(p) if float(p).is_integer() else p}%) | Carried: {r['carried_bugs']}"
    cycle_time = f"{r['current_avg_cycle_time']} days" if r.get("current_avg_cycle_time") is not None else "N/A"
    bd      = r.get("burndown", {})
    bd_note = f"\nBurndown: {_format_decimal(float(bd['current_remaining']), 0)} scope remaining ┬╖ {'Γ£à On track' if bd.get('on_track') else 'ΓÜá∩╕Å Behind'}" if bd else ""
    return (
        f"≡ƒÜÇ Sprint Health Report Ready ΓÇö Lumofy QA\n\nScore: {score}/100 {health_dot}\n"
        f"{bugs_line}\nCycle Time: {cycle_time}{bd_note}\n\n≡ƒöù View Report:\n{site_url}"
    )


# ΓÇöΓÇöΓÇö HTML REPORT ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö


def _person_initials(name: str) -> str:
    parts = [part for part in (name or "Unknown").split() if part]
    if not parts:
        return "UN"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


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


def calc_cycle_time_median_per_type(issues: list) -> dict:
    import statistics
    type_cycle_times = {}
    for issue in issues:
        key = issue["key"]
        issue_type = issue["fields"]["issuetype"]["name"]
        hist = _ISSUE_HISTORY_CACHE.get(key, {})
        status_transitions = (hist.get("data") or {}).get("status") or []
        
        status_transitions = sorted(status_transitions, key=lambda x: str(x.get("datetime") or ""))
        
        first_in_progress = None
        first_done = None
        
        for tx in status_transitions:
            to_status = str(tx.get("to", "")).strip().upper()
            dt = tx.get("datetime")
            if not dt:
                continue
            if first_in_progress is None and "IN PROGRESS" in to_status:
                first_in_progress = dt
            if first_in_progress is not None and is_effectively_done_status(to_status, issue_type):
                if first_done is None:
                    first_done = dt
                    
        if first_in_progress and first_done:
            days = max(0.0, (first_done - first_in_progress).total_seconds() / 86400.0)
            if issue_type not in type_cycle_times:
                type_cycle_times[issue_type] = []
            type_cycle_times[issue_type].append(days)
            
    medians = {}
    for t, times in type_cycle_times.items():
        if times:
            medians[t] = statistics.median(times)
            
    return medians


def calc_status_bottlenecks(issues: list) -> dict:
    status_durations = {}
    total_blocked_seconds = 0
    total_active_seconds = 0
    status_hits = {}
    
    waiting_states = {
        "READY FOR TESTING", "PENDING FIXES", "READY FOR PM REVIEW", 
        "BLOCKED", "ON HOLD"
    }

    for issue in issues:
        key = issue["key"]
        issue_type = issue["fields"]["issuetype"]["name"]
        hist = _ISSUE_HISTORY_CACHE.get(key, {})
        status_transitions = (hist.get("data") or {}).get("status") or []
        created_str = issue["fields"].get("created")
        created_dt = _parse_date_str(created_str) if created_str else None
        
        hits_for_issue = set()
        
        if not status_transitions:
            s_name = issue["fields"]["status"]["name"]
            if not is_effectively_done_status(s_name, issue_type) and created_dt:
                dur = max(0.0, (datetime.now(timezone.utc) - created_dt).total_seconds())
                is_blocked = (s_name.upper() in waiting_states) or (s_name.upper() == "OPEN" and issue_type == BUG_TYPE)
                if is_blocked:
                    status_durations[s_name] = status_durations.get(s_name, 0) + dur
                    total_blocked_seconds += dur
                    hits_for_issue.add(s_name)
                elif s_name.upper() == "IN PROGRESS":
                    total_active_seconds += dur
            for s in hits_for_issue:
                status_hits[s] = status_hits.get(s, 0) + 1
            continue
            
        status_transitions = sorted(status_transitions, key=lambda x: str(x.get("datetime") or ""))
        
        last_time = created_dt
        last_status = status_transitions[0]["from"] if status_transitions[0].get("from") else "Open"
        current_time_dt = datetime.now(timezone.utc)
        
        for tx in status_transitions:
            to_status = tx["to"]
            dt = tx["datetime"]
            if not dt:
                continue
            
            if last_time and last_status:
                duration = max(0.0, (dt - last_time).total_seconds())
                is_blocked = (last_status.upper() in waiting_states) or (last_status.upper() == "OPEN" and issue_type == BUG_TYPE)
                if is_blocked:
                    status_durations[last_status] = status_durations.get(last_status, 0) + duration
                    total_blocked_seconds += duration
                    hits_for_issue.add(last_status)
                elif last_status.upper() == "IN PROGRESS":
                    total_active_seconds += duration
                    
            last_status = to_status
            last_time = dt
            
        if last_status and not is_effectively_done_status(last_status, issue_type) and last_time:
            duration = max(0.0, (current_time_dt - last_time).total_seconds())
            is_blocked = (last_status.upper() in waiting_states) or (last_status.upper() == "OPEN" and issue_type == BUG_TYPE)
            if is_blocked:
                status_durations[last_status] = status_durations.get(last_status, 0) + duration
                total_blocked_seconds += duration
                hits_for_issue.add(last_status)
            elif last_status.upper() == "IN PROGRESS":
                total_active_seconds += duration

        for s in hits_for_issue:
            status_hits[s] = status_hits.get(s, 0) + 1

    total_execution_seconds = total_active_seconds + total_blocked_seconds
    blocked_ratio_pct = (total_blocked_seconds / total_execution_seconds * 100.0) if total_execution_seconds > 0 else 0.0
    
    sorted_bottlenecks = sorted(status_durations.items(), key=lambda x: x[1], reverse=True)
    top_bottlenecks = []
    
    for st_name, seconds in sorted_bottlenecks[:3]:
        pct = (seconds / total_blocked_seconds * 100.0) if total_blocked_seconds > 0 else 0.0
        top_bottlenecks.append({
            "name": st_name,
            "pct": pct,
            "days": seconds / 86400.0
        })
        
    worst_bottleneck_name = None
    worst_bottleneck_days = 0.0
    if sorted_bottlenecks:
        worst_name = sorted_bottlenecks[0][0]
        worst_bottleneck_name = worst_name
        hits = status_hits.get(worst_name, 1)
        worst_bottleneck_days = (sorted_bottlenecks[0][1] / 86400.0) / hits if hits > 0 else 0.0
        
    return {
        "blocked_ratio_pct": blocked_ratio_pct,
        "top_bottlenecks": top_bottlenecks,
        "worst_bottleneck_name": worst_bottleneck_name,
        "worst_bottleneck_days": worst_bottleneck_days
    }


def calc_dev_progress_days(changelog: list[dict]) -> int:
    """
    Development duration:
    from first transition to In Progress
    until first transition to Ready for Testing.
    If not reached Ready for Testing yet, count until now.
    """
    in_progress_started_at = None
    qa_upper = {s.upper() for s in QA_STATUSES}

    for event in changelog:
        to_upper = (event.get("to") or "").strip().upper()
        if in_progress_started_at is None and to_upper == "IN PROGRESS":
            in_progress_started_at = event.get("datetime")
            continue
        if in_progress_started_at is not None and to_upper in qa_upper:
            ended_at = event.get("datetime")
            if ended_at:
                return max(0, int((ended_at - in_progress_started_at).total_seconds() // 86400))
            return 0

    if in_progress_started_at is not None:
        return max(0, int((datetime.now(timezone.utc) - in_progress_started_at).total_seconds() // 86400))
    return 0


def get_status_transitions_today(changelog: list[dict]) -> list[dict]:
    today_local = datetime.now(LOCAL_TZ).date()
    return [e for e in changelog if e["datetime"].astimezone(LOCAL_TZ).date() == today_local]


def get_status_transitions_on_date(changelog: list[dict], target_date) -> list[dict]:
    return [e for e in changelog if e["datetime"].astimezone(LOCAL_TZ).date() == target_date]


def updated_on_date(updated: str, target_date) -> bool:
    dt = parse_jira_datetime(updated)
    return bool(dt and dt.astimezone(LOCAL_TZ).date() == target_date)


def _activity_date_key(target_date) -> str:
    return target_date.isoformat()


def _activity_date_label(target_date) -> str:
    today_local = datetime.now(LOCAL_TZ).date()
    if target_date == today_local:
        return f"Today ┬╖ {target_date.strftime('%d %b')}"
    if target_date == today_local - timedelta(days=1):
        return f"Yesterday ┬╖ {target_date.strftime('%d %b')}"
    return target_date.strftime("%a ┬╖ %d %b")


def _recent_activity_dates(days: int = 7) -> list:
    today_local = datetime.now(LOCAL_TZ).date()
    return [today_local - timedelta(days=offset) for offset in range(max(1, days))]


def _sprint_activity_dates(sprint_start_str: str, fallback_days: int = 7) -> list:
    today_local = datetime.now(LOCAL_TZ).date()
    sprint_start_dt = _parse_date_str(sprint_start_str)
    if sprint_start_dt:
        sprint_start_local = sprint_start_dt.astimezone(LOCAL_TZ).date()
        if sprint_start_local > today_local:
            return [today_local]
    else:
        sprint_start_local = today_local

    # Work week is Sunday -> Thursday, excluding Friday and Saturday.
    days_since_sunday = (today_local.weekday() + 1) % 7
    week_start = today_local - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=4)

    # On Wednesday, include Thursday as the final workday option as requested.
    if today_local.weekday() == 2:
        visible_end = min(week_end, today_local + timedelta(days=1))
    elif today_local.weekday() in {4, 5}:  # Friday / Saturday
        visible_end = week_end
    else:
        visible_end = min(today_local, week_end)

    visible_start = max(week_start, sprint_start_local)
    if visible_start > visible_end:
        return [today_local]

    return [
        visible_start + timedelta(days=offset)
        for offset in range((visible_end - visible_start).days + 1)
        if (visible_start + timedelta(days=offset)).weekday() not in {4, 5}
    ]


# ΓÇöΓÇöΓÇö BURNDOWN ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def build_burndown(issues: list, ss: SprintState) -> dict:
    if not ss.start_str or not ss.end_str: return {}
    start_dt = _parse_date_str(ss.start_str)
    end_dt   = _parse_date_str(ss.end_str)
    now_dt   = datetime.now(timezone.utc)
    if not start_dt or not end_dt or end_dt <= start_dt: return {}

    story_issues = [issue for issue in issues if _is_story_issue(issue)]
    total_days    = max(1, (end_dt.date() - start_dt.date()).days)
    elapsed_days  = max(0, (now_dt.date() - start_dt.date()).days)
    effective_days = elapsed_days if ss.state == "extended" else min(elapsed_days, total_days)
    total_issues  = round(sum(get_issue_weight(issue) for issue in story_issues), 1)

    completions_by_day: dict[int, float] = {}
    for issue in story_issues:
        completion_dt = get_effective_completion_datetime(issue)
        if completion_dt and completion_dt >= start_dt:
            day_idx = (completion_dt.date() - start_dt.date()).days
            completions_by_day[day_idx] = round(
                completions_by_day.get(day_idx, 0.0) + get_issue_weight(issue), 1
            )

    actual_line: list[float] = []
    remaining = total_issues
    for d in range(effective_days + 1):
        remaining -= completions_by_day.get(d, 0)
        actual_line.append(round(max(0.0, remaining), 1))

    ideal_line = [round(total_issues * (1 - d / total_days), 1) for d in range(total_days + 1)]
    current_remaining = actual_line[-1] if actual_line else total_issues
    ideal_at_today    = ideal_line[min(effective_days, total_days)]
    done_count = round(total_issues - current_remaining, 1)
    velocity   = round(done_count / effective_days, 2) if effective_days > 0 else 0.0

    if velocity > 0 and current_remaining > 0:
        projected_end = (now_dt + timedelta(days=current_remaining / velocity)).strftime("%Y-%m-%d")
    elif current_remaining == 0:
        projected_end = "Done Γ£ô"
    else:
        projected_end = "N/A"

    day_labels   = [(start_dt + timedelta(days=d)).strftime("%m/%d") for d in range(effective_days + 1)]
    ideal_labels = [(start_dt + timedelta(days=d)).strftime("%m/%d") for d in range(total_days + 1)]
    behind_by    = round(current_remaining - ideal_at_today, 1)

    return {
        "total_issues": total_issues, "total_scope": total_issues, "total_days": total_days,
        "elapsed_days": effective_days, "actual_line": actual_line,
        "ideal_line": ideal_line, "day_labels": day_labels,
        "ideal_labels": ideal_labels, "current_remaining": current_remaining,
        "completed_scope": done_count,
        "ideal_remaining": ideal_at_today, "velocity": velocity,
        "projected_end": projected_end, "on_track": current_remaining <= ideal_at_today,
        "behind_by": behind_by, "is_extended": ss.state == "extended",
    }


# ΓÇöΓÇöΓÇö CALCULATIONS ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def calc_cycle_time_days(created: str, resolved: str) -> float | None:
    if not created or not resolved: return None
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
        r = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
        return max(0.0, (r - c).total_seconds() / 86400)
    except Exception:
        return None


def parse_jira_datetime(value: str) -> datetime | None:
    if not value: return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def get_effective_completion_datetime(issue: dict) -> datetime | None:
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
    current_status = ((fields.get("status") or {}).get("name") or "").strip()
    if not is_effectively_done_status(current_status, issue_type):
        return None

    resolution_dt = parse_jira_datetime(fields.get("resolutiondate"))
    if resolution_dt:
        return resolution_dt

    issue_key = issue.get("key", "")
    if issue_key:
        history = fetch_issue_history(issue_key, fields.get("updated", "") or "")
        for event in (history.get("status") or []):
            if is_effectively_done_status(event.get("to", ""), issue_type):
                return event.get("datetime")

    return parse_jira_datetime(fields.get("updated"))


def issue_age_days(created: str) -> float | None:
    dt = parse_jira_datetime(created)
    if not dt: return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def days_since_updated(updated: str) -> float | None:
    dt = parse_jira_datetime(updated)
    if not dt: return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def updated_today(updated: str) -> bool:
    dt = parse_jira_datetime(updated)
    if not dt: return False
    return dt.astimezone(LOCAL_TZ).date() == datetime.now(LOCAL_TZ).date()


def get_issue_weight(issue: dict) -> float:
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    story_points = fields.get("customfield_10016")
    if story_points is None:
        return 1.0
    try:
        points = float(story_points)
    except (TypeError, ValueError):
        return 1.0
    return points if points > 0 else 1.0


def get_work_weight(issue: dict, weighting: str = "hybrid_scope") -> float:
    mode = (weighting or "hybrid_scope").strip().lower()
    if mode == "item_count":
        return 1.0
    return get_issue_weight(issue)


def calculate_carryover_metrics(
    issues: list,
    sprint_start_dt: datetime | None,
    include_mid_sprint_added: bool = False,
    weighting: str = "hybrid_scope",
) -> dict:
    committed_work = 0.0
    completed_work = 0.0
    carried_over_work = 0.0
    committed_items = 0
    completed_items = 0
    carried_over_items = 0

    for issue in issues:
        fields = issue.get("fields", {})
        created_dt = parse_jira_datetime(fields.get("created"))
        if not include_mid_sprint_added and sprint_start_dt and created_dt and created_dt > sprint_start_dt:
            continue

        weight = get_work_weight(issue, weighting=weighting)
        committed_work += weight
        committed_items += 1

        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if is_effectively_done_status(status_name, issue_type):
            completed_work += weight
            completed_items += 1
        else:
            carried_over_work += weight
            carried_over_items += 1

    carryover_rate_pct = round((carried_over_work / committed_work * 100), 1) if committed_work > 0 else 0.0
    completion_rate_pct = round((completed_work / committed_work * 100), 1) if committed_work > 0 else 0.0

    return {
        "committed_work": round(committed_work, 1),
        "completed_work": round(completed_work, 1),
        "carried_over_work": round(carried_over_work, 1),
        "carryover_rate_pct": carryover_rate_pct,
        "completion_rate_pct": completion_rate_pct,
        "committed_items": committed_items,
        "completed_items": completed_items,
        "carried_over_items": carried_over_items,
        "include_mid_sprint_added": include_mid_sprint_added,
        "weighting": weighting,
    }


def _parse_sprint_field_date(value) -> datetime | None:
    if not value:
        return None
    return parse_jira_datetime(str(value))


def _get_previous_sprints_for_issue(issue: dict, current_sprint: dict | None, current_sprint_start_dt: datetime | None) -> list[dict]:
    current_sprint = current_sprint or {}
    current_sprint_id = current_sprint.get("id")
    issue_sprints = ((issue.get("fields") or {}).get("customfield_10020") or [])
    previous = []
    for sprint in issue_sprints:
        if not isinstance(sprint, dict):
            continue
        if current_sprint_id is not None and sprint.get("id") == current_sprint_id:
            continue
        sprint_end = _parse_sprint_field_date(sprint.get("completeDate") or sprint.get("endDate") or sprint.get("end_date"))
        sprint_start = _parse_sprint_field_date(sprint.get("startDate") or sprint.get("start_date"))
        if current_sprint_start_dt and sprint_end and sprint_end > current_sprint_start_dt:
            continue
        if current_sprint_start_dt and sprint_start and sprint_start >= current_sprint_start_dt:
            continue
        previous.append(sprint)
    return previous


def calculate_carried_in_work_metrics(
    issues: list,
    current_sprint: dict | None,
    current_sprint_start_dt: datetime | None,
    weighting: str = "hybrid_scope",
    include_item_list: bool = False,
) -> dict:
    total_work = 0.0
    carried_in_work = 0.0
    total_items = 0
    carried_in_items = 0
    carried_in_issue_keys: list[str] = []

    for issue in issues:
        fields = issue.get("fields", {})
        weight = get_work_weight(issue, weighting=weighting)
        total_work += weight
        total_items += 1

        previous_sprints = _get_previous_sprints_for_issue(issue, current_sprint, current_sprint_start_dt)
        if not previous_sprints:
            continue

        resolution_dt = parse_jira_datetime(fields.get("resolutiondate"))
        was_completed_before_current_sprint = bool(
            current_sprint_start_dt and resolution_dt and resolution_dt <= current_sprint_start_dt
        )
        if was_completed_before_current_sprint:
            continue

        carried_in_work += weight
        carried_in_items += 1
        if include_item_list:
            carried_in_issue_keys.append(issue.get("key", ""))

    carried_in_rate_pct = round((carried_in_work / total_work * 100), 1) if total_work > 0 else 0.0
    result = {
        "total_work": round(total_work, 1),
        "carried_in_work": round(carried_in_work, 1),
        "carried_in_rate_pct": carried_in_rate_pct,
        "total_items": total_items,
        "carried_in_items": carried_in_items,
        "weighting": weighting,
    }
    if include_item_list:
        result["carried_in_issue_keys"] = carried_in_issue_keys
    return result


def _status_at_datetime(current_status: str, status_events: list[dict], target_dt: datetime | None) -> str:
    if not target_dt:
        return (current_status or "").strip()
    if not status_events:
        return (current_status or "").strip()

    status_name = (status_events[0].get("from") or current_status or "").strip()
    for event in status_events:
        if event["datetime"] <= target_dt:
            status_name = (event.get("to") or status_name).strip()
        else:
            break
    return status_name


def _find_transition_into_current_sprint(sprint_events: list[dict], current_sprint_name: str) -> dict | None:
    current_name = (current_sprint_name or "").strip()
    if not current_name:
        return None
    matched = None
    for event in sprint_events:
        to_name = (event.get("to") or "").strip()
        if to_name == current_name:
            matched = event
    return matched


def calculate_sprint_carryover_metrics(
    issues: list,
    current_sprint: dict | None,
    current_sprint_start_dt: datetime | None,
    weighting: str = "hybrid_scope",
    include_item_list: bool = False,
) -> dict:
    current_sprint = current_sprint or {}
    current_sprint_name = (current_sprint.get("name") or "").strip()
    board_id = current_sprint.get("boardId") or get_board_id()
    sprint_catalog = fetch_board_sprint_catalog(int(board_id)) if board_id else {}

    total_work = 0.0
    total_items = 0

    historical_work = 0.0
    historical_items = 0
    historical_issue_keys: list[str] = []

    official_rollover_work = 0.0
    official_rollover_items = 0
    official_rollover_issue_keys: list[str] = []

    for issue in issues:
        fields = issue.get("fields", {})
        key = issue.get("key", "")
        weight = get_work_weight(issue, weighting=weighting)
        total_work += weight
        total_items += 1

        history = fetch_issue_history(key, fields.get("updated", "") or "")
        sprint_events = history.get("sprint", [])
        status_events = history.get("status", [])
        transition_into_current = _find_transition_into_current_sprint(sprint_events, current_sprint_name)
        if not transition_into_current:
            continue

        current_status = ((fields.get("status") or {}).get("name") or "").strip()
        status_at_current_start = _status_at_datetime(current_status, status_events, current_sprint_start_dt)
        if not is_effectively_done_status(status_at_current_start, (fields.get("issuetype") or {}).get("name", "")):
            historical_work += weight
            historical_items += 1
            if include_item_list:
                historical_issue_keys.append(key)

        previous_sprint_name = (transition_into_current.get("from") or "").strip()
        previous_sprint = sprint_catalog.get(previous_sprint_name) or {}
        previous_sprint_end_dt = _parse_sprint_field_date(
            previous_sprint.get("completeDate") or previous_sprint.get("endDate") or previous_sprint.get("end_date")
        )
        if previous_sprint_end_dt:
            status_at_previous_close = _status_at_datetime(current_status, status_events, previous_sprint_end_dt)
            if not is_effectively_done_status(status_at_previous_close, (fields.get("issuetype") or {}).get("name", "")):
                official_rollover_work += weight
                official_rollover_items += 1
                if include_item_list:
                    official_rollover_issue_keys.append(key)

    result = {
        "total_work": round(total_work, 1),
        "total_items": total_items,
        "historical_carried_in_work": round(historical_work, 1),
        "historical_carried_in_items": historical_items,
        "historical_carried_in_rate_pct": round((historical_work / total_work * 100), 1) if total_work > 0 else 0.0,
        "official_rollover_work": round(official_rollover_work, 1),
        "official_rollover_items": official_rollover_items,
        "official_rollover_rate_pct": round((official_rollover_work / total_work * 100), 1) if total_work > 0 else 0.0,
        "weighting": weighting,
    }
    if include_item_list:
        result["historical_carried_in_issue_keys"] = historical_issue_keys
        result["official_rollover_issue_keys"] = official_rollover_issue_keys
    return result


# ΓÇöΓÇöΓÇö SCORING ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def _progress_weight(sprint_pct: float | None) -> float:
    if sprint_pct is None: return 1.0
    if sprint_pct < 30: return sprint_pct / 30
    if sprint_pct < 60: return 0.5 + (sprint_pct - 30) / 60
    return 1.0


def _blend(real_score: int, sprint_pct: float | None, neutral: int = 70) -> int:
    w = _progress_weight(sprint_pct)
    return round(neutral + w * (real_score - neutral))


def score_commitment(completed, committed, sprint_pct=None, is_extended=False):
    points, cfg = _config_points(), METRICS_CONFIG["commitment"]
    if committed == 0: return points["neutral"], 0.0
    pct = completed / committed * 100
    if cfg["ideal_min_pct"] <= pct <= cfg["ideal_max_pct"]: raw = points["excellent"]
    elif pct >= cfg["good_min_pct"]: raw = points["good"]
    elif pct >= cfg["warning_min_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    if is_extended: score = min(score, int(cfg["extended_cap_score"]))
    return score, round(pct, 1)


def score_carryover(carried, total, sprint_pct=None, is_extended=False):
    points, cfg = _config_points(), METRICS_CONFIG["carryover"]
    if total == 0: return points["neutral"], 0.0
    pct = carried / total * 100
    if pct < cfg["excellent_lt_pct"]: raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]: raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    score = _blend(raw, sprint_pct, points["neutral"])
    return score, round(pct, 1)


def score_cycle_time(current_avg, prev_avg, sprint_pct=None):
    points, cfg = _config_points(), METRICS_CONFIG["cycle_time"]
    if current_avg is None or prev_avg is None or prev_avg == 0:
        return points["neutral"], None
    diff_pct = (current_avg - prev_avg) / prev_avg * 100
    if abs(diff_pct) <= cfg["stable_abs_pct"]: raw = points["excellent"]
    elif diff_pct <= cfg["good_increase_pct"]: raw = points["good"]
    elif diff_pct <= cfg["warning_increase_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(diff_pct, 1)


def score_bug_ratio(new_bugs, total, sprint_pct=None):
    points, cfg = _config_points(), METRICS_CONFIG["bug_ratio"]
    if total == 0 and new_bugs == 0: return points["neutral"], 0.0
    denom = total if total > 0 else 1
    pct   = new_bugs / denom * 100
    if pct < cfg["excellent_lt_pct"]: raw = points["excellent"]
    elif pct <= cfg["good_lte_pct"]: raw = points["good"]
    elif pct <= cfg["warning_lte_pct"]: raw = points["warning"]
    else: raw = points["poor"]
    return _blend(raw, sprint_pct, points["neutral"]), round(pct, 1)


def score_burndown(bd: dict, sprint_pct) -> int:
    cfg = METRICS_CONFIG["burndown"]
    if not bd: return 0
    if bd.get("current_remaining", 0) == 0: return int(cfg["done_bonus"])
    if bd.get("on_track"): return int(cfg["on_track_bonus"])
    behind = bd.get("behind_by", 0)
    if behind <= cfg["behind_small_max"]: return 0
    if behind <= cfg["behind_medium_max"]: return int(cfg["behind_medium_penalty"])
    return int(cfg["behind_large_penalty"])


def calc_health_score(c_score, co_score, cy_score, b_score, bd_nudge=0) -> dict:
    cfg     = _config_final_score()
    formula = (cfg.get("custom_formula") or "").strip() or DEFAULT_METRICS_CONFIG["final_score"]["custom_formula"]
    context = _build_formula_context(c_score, co_score, cy_score, b_score, bd_nudge)
    raw     = _safe_eval_formula(formula, context)
    bounded = max(float(cfg.get("min_score", 0)), min(float(cfg.get("max_score", 100)), raw))
    final   = round(bounded) if cfg.get("round_result", True) else bounded
    return {
        "score": int(round(final)), "raw_score": raw,
        "formula": formula, "context": context,
        "weighted_breakdown": {
            "commitment": round(context["weighted_commitment"], 1),
            "carryover":  round(context["weighted_carryover"],  1),
            "cycle_time": round(context["weighted_cycle_time"], 1),
            "bug_ratio":  round(context["weighted_bug_ratio"],  1),
        },
    }


def health_label(score: int) -> tuple[str, str]:
    labels = METRICS_CONFIG["labels"]
    if score >= labels["green_min_score"]:  return ":green_circle:",  "Predictable sprint"
    if score >= labels["yellow_min_score"]: return ":yellow_circle:", "Some instability"
    if score >= labels["orange_min_score"]: return ":orange_circle:", "Execution issues"
    return ":red_circle:", "Sprint breakdown"


def _extract_response_text(payload: dict) -> str:
    output_text = (payload.get("output_text") or "").strip()
    if output_text: return output_text
    for item in payload.get("output", []):
        if item.get("type") != "message": continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    return ""


def generate_ai_insights(report: dict) -> dict | None:
    cfg = _config_ai()
    if not cfg.get("enabled"): return None
    if not OPENAI_API_KEY:
        return {"status": "disabled", "title": "AI insights unavailable",
                "summary": "Set OPENAI_API_KEY in .env to enable AI recommendations.", "actions": []}
    payload = {
        "model": (cfg.get("model") or OPENAI_MODEL).strip() or OPENAI_MODEL,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": (
                "You analyze sprint health reports. Reply in JSON only with keys "
                "title, summary, actions. actions must be an array of up to 3 short strings."
            )}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(report, ensure_ascii=False)}]},
        ],
        "max_output_tokens": int(cfg.get("max_output_tokens", 350)),
    }
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=OPENAI_TIMEOUT,
        )
        resp.raise_for_status()
        parsed  = json.loads(_extract_response_text(resp.json()))
        actions = parsed.get("actions") if isinstance(parsed.get("actions"), list) else []
        return {
            "status": "ok",
            "title":   str(parsed.get("title") or "AI insight").strip(),
            "summary": str(parsed.get("summary") or "").strip(),
            "actions": [str(i).strip() for i in actions if str(i).strip()][:3],
        }
    except Exception as e:
        return {"status": "error", "title": "AI insight failed",
                "summary": f"AI request failed: {e}", "actions": []}


# ΓÇöΓÇöΓÇö DEVELOPER & QA ACTIVITY ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def build_developer_activity(
    issues: list,
    sprint_start_str: str,
    target_dates: list | None = None,
    allowed_qa_names: set[str] | None = None,
    allowed_dev_names: set[str] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """
    Returns:
      dev_activity ΓÇö developer-owned status transitions grouped by date then assignee
      qa_activity  ΓÇö QA status transitions grouped by date then actor
    """
    qa_filter = allowed_qa_names or set()
    dev_filter = allowed_dev_names or set()
    target_dates = target_dates or _sprint_activity_dates(sprint_start_str)
    date_keys = [_activity_date_key(target_date) for target_date in target_dates]
    dev_maps: dict[str, dict[str, dict]] = {date_key: {} for date_key in date_keys}
    qa_items_by_date: dict[str, list[dict]] = {date_key: [] for date_key in date_keys}
    qa_upper = {s.upper() for s in QA_STATUSES}
    pending_upper = {s.upper() for s in QA_PENDING_STATUSES}
    pm_review_upper = {s.upper() for s in QA_PM_REVIEW}

    for issue in issues:
        f            = issue["fields"]
        updated_raw  = f.get("updated")
        assignee     = f.get("assignee")
        issue_type   = (f.get("issuetype") or {}).get("name", "")
        status_name  = f["status"]["name"]
        story_points = f.get("customfield_10016")
        key          = issue.get("key", "")
        summary      = f.get("summary", "")
        url          = f"{JIRA_BASE_URL}/browse/{key}"
        linked_story, linked_story_summary = _extract_linked_story_details(f)

        # ΓÇöΓÇö Fetch changelog for every sprint issue ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö
        changelog = fetch_issue_changelog(key, updated_raw or "")

        # Time in "IN TESTING" from entry until it exits to the next QA outcome.
        time_in_rft = calc_time_in_status(changelog, "IN TESTING")

        # ΓÇöΓÇö Developer Activity ΓÇö developer-owned transitions today ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö
        dev_name   = (assignee or {}).get("displayName", "Unassigned")
        dev_avatar = (assignee or {}).get("avatarUrls", {}).get("48x48", "")
        dev_name_norm = _normalize_person_name(dev_name)
        if dev_filter and dev_name_norm not in dev_filter:
            continue

        active_days = calc_dev_progress_days(changelog)

        stale_threshold = get_stale_threshold(issue_type, story_points)
        is_stale        = active_days > stale_threshold and not is_effectively_done_status(status_name, issue_type)

        assignee_account_id = (assignee or {}).get("accountId", "")
        for target_date in target_dates:
            date_key = _activity_date_key(target_date)
            day_transitions = get_status_transitions_on_date(changelog, target_date)

            for t in day_transitions:
                actor_norm = _normalize_person_name(t.get("actor", ""))
                if qa_filter and actor_norm not in qa_filter:
                    continue

                to_upper = t["to"].strip().upper()
                from_upper = t["from"].strip().upper()

                if to_upper in qa_upper:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "started_testing",
                        "Γû╢ Started Testing", "#1a6bff", time_in_rft, url, story_points
                    ))
                elif from_upper in qa_upper and to_upper in pending_upper:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "pending_fixes",
                        "≡ƒöä Pending Fixes", "#fbbf24", time_in_rft, url, story_points
                    ))
                elif from_upper in qa_upper and to_upper in pm_review_upper:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "pm_review",
                        "Γ£à Ready for PM Review", "#00d4aa", time_in_rft, url, story_points
                    ))
                elif from_upper in qa_upper and is_effectively_done_status(t["to"], issue_type):
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "done",
                        "Γ£à Done", "#00d4aa", time_in_rft, url, story_points
                    ))
                else:
                    qa_items_by_date[date_key].append(_qa_event(
                        key, summary, issue_type, t, "status_changed",
                        f"Γåö {t['from']} ΓåÆ {t['to']}", "#4a90d9", time_in_rft, url, story_points
                    ))

            if not day_transitions:
                continue

            seen_transitions = set()
            transitions_for_day: list[str] = []
            for t in day_transitions:
                is_dev_action = False
                if assignee_account_id and t.get("actor_account_id"):
                    is_dev_action = t.get("actor_account_id") == assignee_account_id
                elif dev_name and dev_name != "Unassigned":
                    is_dev_action = (t.get("actor", "") or "").strip().lower() == dev_name.strip().lower()
                if not is_dev_action:
                    continue

                transition_label = f"{t['from']} ΓåÆ {t['to']}"
                if transition_label in seen_transitions:
                    continue
                seen_transitions.add(transition_label)
                transitions_for_day.append(transition_label)

            if not transitions_for_day:
                continue

            if dev_name not in dev_maps[date_key]:
                dev_maps[date_key][dev_name] = {"name": dev_name, "avatar": dev_avatar, "issues": []}

            dev_maps[date_key][dev_name]["issues"].append({
                "key": key, "summary": summary, "type": issue_type,
                "status": status_name, "story_points": story_points,
                "active_days": active_days, "is_stale": is_stale,
                "stale_threshold": stale_threshold, "is_done": is_effectively_done_status(status_name, issue_type),
                "time_in_rft": time_in_rft, "transitions_today": transitions_for_day,
                "url": url,
                "linked_story": linked_story,
                "linked_story_summary": linked_story_summary,
            })

    dev_history = {
        date_key: sorted(dev_maps[date_key].values(), key=lambda d: d["name"])
        for date_key in date_keys
    }
    qa_history = {
        date_key: qa_items_by_date[date_key]
        for date_key in date_keys
    }
    return dev_history, qa_history


def _qa_event(key, summary, issue_type, transition, event, label, color,
              time_in_rft, url, story_points) -> dict:
    return {
        "key": key, "summary": summary, "type": issue_type,
        "status": transition["to"], "from_status": transition["from"],
        "event": event, "label": label, "color": color,
        "actor": transition.get("actor", "Unknown"),
        "actor_avatar": transition.get("actor_avatar", ""),
        "time_in_rft": time_in_rft, "url": url, "story_points": story_points,
    }


# ΓÇöΓÇöΓÇö REPORT BUILDER ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def _extract_linked_story_key(fields: dict) -> str:
    parent = fields.get("parent") or {}
    parent_key = parent.get("key")
    parent_type = (((parent.get("fields") or {}).get("issuetype") or {}).get("name") or "").strip().lower()
    if parent_key and parent_type == "story":
        return parent_key

    for link in (fields.get("issuelinks") or []):
        for side in ("outwardIssue", "inwardIssue"):
            issue = link.get(side) or {}
            issue_key = issue.get("key")
            issue_type = (((issue.get("fields") or {}).get("issuetype") or {}).get("name") or "").strip().lower()
            if issue_key and issue_type == "story":
                return issue_key
    return ""


def _extract_linked_story_details(fields: dict) -> tuple[str, str]:
    parent = fields.get("parent") or {}
    parent_fields = parent.get("fields") or {}
    parent_key = parent.get("key") or ""
    parent_type = ((parent_fields.get("issuetype") or {}).get("name") or "").strip().lower()
    parent_summary = (parent_fields.get("summary") or "").strip()
    if parent_key and parent_type == "story":
        return parent_key, parent_summary

    for link in (fields.get("issuelinks") or []):
        for side in ("outwardIssue", "inwardIssue"):
            issue = link.get(side) or {}
            issue_fields = issue.get("fields") or {}
            issue_key = issue.get("key") or ""
            issue_type = ((issue_fields.get("issuetype") or {}).get("name") or "").strip().lower()
            issue_summary = (issue_fields.get("summary") or "").strip()
            if issue_key and issue_type == "story":
                return issue_key, issue_summary
    return "", ""


def _extract_linked_work_category(fields: dict) -> str:
    parent = fields.get("parent") or {}
    parent_fields = parent.get("fields") or {}
    parent_type = ((parent_fields.get("issuetype") or {}).get("name") or "").strip().lower()
    if parent_type == "story":
        return "story"
    if parent_type in {"enhancement", "improvement", "task"}:
        return "enhancement_task"
    if parent_type:
        return "other"

    for link in (fields.get("issuelinks") or []):
        for side in ("outwardIssue", "inwardIssue"):
            issue = link.get(side) or {}
            issue_fields = issue.get("fields") or {}
            issue_type = ((issue_fields.get("issuetype") or {}).get("name") or "").strip().lower()
            if issue_type == "story":
                return "story"
            if issue_type in {"enhancement", "improvement", "task"}:
                return "enhancement_task"
            if issue_type:
                return "other"
    return "no_link"


def _build_bug_linkage_breakdown(issues: list[dict]) -> dict[str, int]:
    counts = {"story": 0, "enhancement_task": 0, "no_link": 0, "other": 0}
    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if issue_type not in {BUG_TYPE, "Feature Bug"}:
            continue
        category = _extract_linked_work_category(fields)
        counts[category] = counts.get(category, 0) + 1
    return counts


def _build_bug_story_insights(issues: list[dict]) -> dict:
    story_bug_count = 0
    linked_story_keys: set[str] = set()
    engineer_counts: dict[str, int] = {}

    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if issue_type not in {BUG_TYPE, "Feature Bug"}:
            continue

        linked_story_key, _ = _extract_linked_story_details(fields)
        if not linked_story_key:
            continue

        story_bug_count += 1
        linked_story_keys.add(linked_story_key)

        assignee = fields.get("assignee") or {}
        assignee_name = (assignee.get("displayName") or "Unassigned").strip() or "Unassigned"
        engineer_counts[assignee_name] = engineer_counts.get(assignee_name, 0) + 1

    unique_story_count = len(linked_story_keys)
    avg_bugs_per_story = round(story_bug_count / unique_story_count, 1) if unique_story_count > 0 else 0.0
    top_engineer_name = ""
    top_engineer_bug_count = 0
    if engineer_counts:
        top_engineer_name, top_engineer_bug_count = sorted(
            engineer_counts.items(),
            key=lambda item: (-item[1], item[0].lower()),
        )[0]

    return {
        "story_bug_count": story_bug_count,
        "unique_story_count": unique_story_count,
        "avg_bugs_per_story": avg_bugs_per_story,
        "top_engineer_name": top_engineer_name,
        "top_engineer_bug_count": top_engineer_bug_count,
    }


def _count_story_linked_bugs(issues: list[dict]) -> int:
    count = 0
    for issue in issues:
        fields = issue.get("fields", {})
        issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
        if issue_type not in {BUG_TYPE, "Feature Bug"}:
            continue
        linked_story_key, _ = _extract_linked_story_details(fields)
        if linked_story_key:
            count += 1
    return count


def _signal_benchmark_summaries() -> dict:
    commitment = METRICS_CONFIG["commitment"]
    carryover = METRICS_CONFIG["carryover"]
    cycle_time = METRICS_CONFIG["cycle_time"]
    bug_ratio = METRICS_CONFIG["bug_ratio"]
    return {
        "commitment": (
            f"Target: {int(commitment['ideal_min_pct'])}-{int(commitment['ideal_max_pct'])}% "
            f"| Good: {int(commitment['good_min_pct'])}%+ | Risk: <{int(commitment['warning_min_pct'])}%"
        ),
        "carryover": (
            f"Best: <{int(carryover['excellent_lt_pct'])}% "
            f"| Good: up to {int(carryover['good_lte_pct'])}% "
            f"| Risk: >{int(carryover['warning_lte_pct'])}%"
        ),
        "cycle_time": (
            f"Best: within +/-{int(cycle_time['stable_abs_pct'])}% "
            f"| Good: up to +{int(cycle_time['good_increase_pct'])}% "
            f"| Risk: >+{int(cycle_time['warning_increase_pct'])}%"
        ),
        "bug_ratio": (
            f"Best: <{int(bug_ratio['excellent_lt_pct'])}% "
            f"| Good: up to {int(bug_ratio['good_lte_pct'])}% "
            f"| Risk: >{int(bug_ratio['warning_lte_pct'])}%"
        ),
    }


def _sprint_placement_label(fields: dict) -> str:
    sprints = fields.get("customfield_10020") or []
    if not sprints:
        return "Backlog"
    active = [
        s.get("name", "").strip()
        for s in sprints
        if (s.get("state", "") or "").lower() == "active" and s.get("name")
    ]
    if active:
        return active[0]
    named = [s.get("name", "").strip() for s in sprints if s.get("name")]
    return named[-1] if named else "Backlog"


def _build_remaining_scope_breakdown(issues: list) -> list[dict]:
    by_type: dict[str, dict] = {}
    for issue in issues:
        fields = issue.get("fields", {})
        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        issue_type = ((fields.get("issuetype") or {}).get("name") or "Unknown").strip() or "Unknown"
        if is_effectively_done_status(status_name, issue_type):
            continue
        row = by_type.setdefault(issue_type, {"type": issue_type, "count": 0, "scope": 0.0})
        row["count"] += 1
        row["scope"] = round(row["scope"] + get_issue_weight(issue), 1)
    return sorted(by_type.values(), key=lambda item: (-item["scope"], -item["count"], item["type"]))


def _build_scope_breakdown(issues: list, remaining_only: bool = False) -> list[dict]:
    by_type: dict[str, dict] = {}
    for issue in issues:
        fields = issue.get("fields", {})
        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        issue_type = ((fields.get("issuetype") or {}).get("name") or "Unknown").strip() or "Unknown"
        if remaining_only and is_effectively_done_status(status_name, issue_type):
            continue
        row = by_type.setdefault(issue_type, {"type": issue_type, "count": 0, "scope": 0.0})
        row["count"] += 1
        row["scope"] = round(row["scope"] + get_issue_weight(issue), 1)
    return sorted(by_type.values(), key=lambda item: (-item["scope"], -item["count"], item["type"]))


def calculate_bug_ratio_base_work(issues: list, weighting: str = "hybrid_scope") -> dict:
    included_types = {STORY_TYPE}
    base_work = 0.0
    base_items = 0
    by_type: dict[str, dict] = {}

    for issue in issues:
        issue_type = ((((issue.get("fields") or {}).get("issuetype")) or {}).get("name") or "").strip()
        if issue_type not in included_types:
            continue
        weight = get_work_weight(issue, weighting=weighting)
        base_work += weight
        base_items += 1
        row = by_type.setdefault(issue_type, {"type": issue_type, "count": 0, "scope": 0.0})
        row["count"] += 1
        row["scope"] = round(row["scope"] + weight, 1)

    return {
        "base_work": round(base_work, 1),
        "base_items": base_items,
        "included_types": sorted(included_types),
        "breakdown": sorted(by_type.values(), key=lambda item: (-item["scope"], -item["count"], item["type"])),
    }


def _is_new_sprint_bug(issue: dict, sprint_start_dt: datetime | None) -> bool:
    fields = issue.get("fields", {})
    issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
    if issue_type not in {BUG_TYPE, "Feature Bug"}:
        return False
    created_dt = parse_jira_datetime(fields.get("created"))
    if not sprint_start_dt or not created_dt:
        return False
    return created_dt.date() >= sprint_start_dt.date()


def _build_planned_scope_metrics(issues: list, sprint_start_dt: datetime | None) -> dict:
    planned_scope = 0.0
    completed_scope = 0.0
    remaining_scope = 0.0
    for issue in issues:
        if _is_new_sprint_bug(issue, sprint_start_dt):
            continue
        weight = get_issue_weight(issue)
        planned_scope += weight
        status_name = (((issue.get("fields") or {}).get("status") or {}).get("name") or "").strip()
        if is_effectively_done_status(status_name, ((issue.get("fields") or {}).get("issuetype") or {}).get("name", "")):
            completed_scope += weight
        else:
            remaining_scope += weight
    return {
        "planned_scope": round(planned_scope, 1),
        "completed_scope": round(completed_scope, 1),
        "remaining_scope": round(remaining_scope, 1),
    }


def build_today_bug_reports(target_dates: list | None = None) -> dict[str, list[dict]]:
    target_dates = target_dates or _recent_activity_dates(7)
    date_keys = [_activity_date_key(target_date) for target_date in target_dates]
    bug_issues = fetch_recent_created_bugs(days=len(target_dates))
    rows_by_date: dict[str, list[dict]] = {date_key: [] for date_key in date_keys}
    for issue in bug_issues:
        f = issue.get("fields", {})
        key = issue.get("key", "")
        creator_user = f.get("creator") or {}
        reporter_user = f.get("reporter") or {}
        creator = (
            creator_user.get("displayName")
            or reporter_user.get("displayName")
            or "Unknown"
        )
        creator_avatar = (
            (creator_user.get("avatarUrls") or {}).get("48x48")
            or (reporter_user.get("avatarUrls") or {}).get("48x48")
            or ""
        )
        linked_story = _extract_linked_story_key(f)
        created_dt = parse_jira_datetime(f.get("created"))
        if not created_dt:
            continue
        date_key = _activity_date_key(created_dt.astimezone(LOCAL_TZ).date())
        if date_key not in rows_by_date:
            continue
        rows_by_date[date_key].append({
            "key": key,
            "summary": f.get("summary", ""),
            "status": ((f.get("status") or {}).get("name") or ""),
            "type": ((f.get("issuetype") or {}).get("name") or ""),
            "created_by": creator,
            "created_by_avatar": creator_avatar,
            "linked_story": linked_story,
            "is_linked_to_story": bool(linked_story),
            "sprint_placement": _sprint_placement_label(f),
            "url": f"{JIRA_BASE_URL}/browse/{key}",
        })
    return rows_by_date


def build_report(issues: list, sprint_info: dict, prev_sprints: list) -> dict:
    ss    = SprintState(sprint_info)
    sp    = ss.sprint_progress_pct
    is_ex = ss.state == "extended"

    sprint_start_dt = _parse_date_str(ss.start_str)
    story_issues = [issue for issue in issues if _is_story_issue(issue)]
    total        = len(issues)
    done         = sum(1 for i in issues if is_effectively_done_status(i["fields"]["status"]["name"], i["fields"]["issuetype"]["name"]))
    carried_over = total - done

    # Bug separation
    new_bugs = carried_bugs = new_bugs_done = 0
    new_bug_items: list[dict] = []
    carried_bug_items: list[dict] = []
    for i in issues:
        f = i["fields"]
        if f["issuetype"]["name"] not in {BUG_TYPE, "Feature Bug"}:
            continue
        created_dt = parse_jira_datetime(f.get("created"))
        if sprint_start_dt and created_dt and created_dt.date() >= sprint_start_dt.date():
            new_bugs += 1
            new_bug_items.append(i)
            if is_effectively_done_status(f["status"]["name"], f["issuetype"]["name"]): new_bugs_done += 1
        else:
            carried_bugs += 1
            carried_bug_items.append(i)

    bugs         = new_bugs + carried_bugs
    new_story_linked_bugs = _count_story_linked_bugs(new_bug_items)
    stories_done = sum(1 for i in issues
                       if i["fields"]["issuetype"]["name"] == STORY_TYPE
                       and is_effectively_done_status(i["fields"]["status"]["name"], i["fields"]["issuetype"]["name"]))

    status_counts = {}; issue_type_counts = {}; assignee_counts = {}
    unfinished_status_counts = {}
    blockers = flagged = 0
    age_buckets = {"0-3d": 0, "4-7d": 0, "8-14d": 0, "15+d": 0}
    age_values  = []

    for i in issues:
        f = i["fields"]
        s = f["status"]["name"]
        t = f["issuetype"]["name"]
        status_counts[s] = status_counts.get(s, 0) + 1
        if not is_effectively_done_status(s, t):
            unfinished_status_counts[s] = unfinished_status_counts.get(s, 0) + 1
        issue_type_counts[t] = issue_type_counts.get(t, 0) + 1
        assignee      = f.get("assignee")
        assignee_name = assignee.get("displayName") if assignee else "Unassigned"
        assignee_counts[assignee_name] = assignee_counts.get(assignee_name, 0) + 1
        labels   = [l.lower() for l in (f.get("labels") or [])]
        if "blocked" in labels or "blocker" in labels or "block" in s.lower(): blockers += 1
        if "flagged" in labels or bool(f.get("customfield_10021")): flagged += 1
        if not is_effectively_done_status(s, t):
            age = issue_age_days(f.get("created"))
            if age is not None:
                age_values.append(age)
                if age <= 3: age_buckets["0-3d"] += 1
                elif age <= 7: age_buckets["4-7d"] += 1
                elif age <= 14: age_buckets["8-14d"] += 1
                else: age_buckets["15+d"] += 1

    cycle_times = [
        ct for i in story_issues
        if is_effectively_done_status(i["fields"]["status"]["name"], i["fields"]["issuetype"]["name"])
        for ct in [calc_cycle_time_days(i["fields"].get("created"), i["fields"].get("resolutiondate"))]
        if ct is not None
    ]
    current_avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else None
    prev_avg_ct    = None
    if prev_sprints:
        valid = [s["avg_cycle_time"] for s in prev_sprints if s["avg_cycle_time"] is not None]
        if valid: prev_avg_ct = sum(valid) / len(valid)
    prev_bugs = next((s["bugs"] for s in prev_sprints if s.get("bugs") is not None), None)

    bd          = build_burndown(issues, ss)
    total_scope = bd.get("total_scope", float(len(story_issues))) if bd else float(len(story_issues))
    scope_cfg = _config_scope_calculation()
    carryover_metrics = calculate_carryover_metrics(
        story_issues,
        sprint_start_dt=sprint_start_dt,
        include_mid_sprint_added=bool(scope_cfg.get("include_mid_sprint_added", False)),
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
    )
    carried_in_metrics = calculate_carried_in_work_metrics(
        issues,
        current_sprint=sprint_info,
        current_sprint_start_dt=sprint_start_dt,
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
        include_item_list=False,
    )
    sprint_carryover_metrics = calculate_sprint_carryover_metrics(
        story_issues,
        current_sprint=sprint_info,
        current_sprint_start_dt=sprint_start_dt,
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
        include_item_list=False,
    )
    committed_scope = total_scope
    completed_scope = sum(
        get_work_weight(issue, weighting=str(scope_cfg.get("weighting", "hybrid_scope")))
        for issue in story_issues
        if is_effectively_done_status(
            ((issue.get("fields", {}).get("status") or {}).get("name") or "").strip(),
            ((issue.get("fields", {}).get("issuetype") or {}).get("name") or "").strip(),
        )
    )
    official_rollover_scope = sprint_carryover_metrics["official_rollover_work"]
    bug_ratio_base = calculate_bug_ratio_base_work(
        story_issues,
        weighting=str(scope_cfg.get("weighting", "hybrid_scope")),
    )
    bug_ratio_base_work = bug_ratio_base["base_work"]

    c_score,  c_pct  = score_commitment(completed_scope, committed_scope, sp, is_ex)
    co_score, co_pct = score_carryover(official_rollover_scope, total_scope, sp, is_ex)
    cy_score, cy_pct = score_cycle_time(current_avg_ct, prev_avg_ct, sp)
    b_score,  b_pct  = score_bug_ratio(new_story_linked_bugs, bug_ratio_base_work, sp)

    if bd:
        bd["total_breakdown"] = _build_scope_breakdown(issues, remaining_only=False)
        bd["remaining_breakdown"] = _build_scope_breakdown(issues, remaining_only=True)
    bd_nudge    = score_burndown(bd, sp)
    health_calc = calc_health_score(c_score, co_score, cy_score, b_score, bd_nudge)
    health      = health_calc["score"]
    emoji, label = health_label(health)

    bug_change_pct, bug_change_arrow = None, "ΓåÆ"
    if prev_bugs is not None and prev_bugs > 0:
        bug_change_pct   = round((bugs - prev_bugs) / prev_bugs * 100, 1)
        bug_change_arrow = "Γåô" if bug_change_pct < 0 else ("Γåæ" if bug_change_pct > 0 else "ΓåÆ")

    no_data_signals = []
    if len(story_issues) == 0: no_data_signals.extend(["commitment", "carryover", "bug_ratio"])
    if current_avg_ct is None or prev_avg_ct is None: no_data_signals.append("cycle_time")

    weights = _config_weights()
    fb      = dict(health_calc["weighted_breakdown"])

    activity_cfg = _config_activity_people()
    qa_name_filter = {
        _normalize_person_name(name)
        for name in (activity_cfg.get("qa_names") or [])
        if _normalize_person_name(str(name))
    }
    dev_name_filter = {
        _normalize_person_name(name)
        for name in (activity_cfg.get("developer_names") or [])
        if _normalize_person_name(str(name))
    }

    activity_dates = _sprint_activity_dates(ss.start_str)
    today_activity_key = _activity_date_key(datetime.now(LOCAL_TZ).date())
    activity_date_options = [
        {
            "key": _activity_date_key(target_date),
            "label": _activity_date_label(target_date),
            "is_default": _activity_date_key(target_date) == today_activity_key,
        }
        for target_date in activity_dates
    ]
    activity_issues = fetch_recent_project_issues(days=len(activity_dates))
    dev_activity, qa_activity = build_developer_activity(
        activity_issues,
        ss.start_str,
        target_dates=activity_dates,
        allowed_qa_names=qa_name_filter,
        allowed_dev_names=dev_name_filter,
    )
    today_bug_reports = build_today_bug_reports(activity_dates)

    cycle_time_medians = calc_cycle_time_median_per_type(issues)
    bottlenecks = calc_status_bottlenecks(issues)

    ai_insights = generate_ai_insights({
        "sprint_name": ss.name, "health_score": health, "health_label": label,
        "signals": {
            "commitment": {"score": c_score, "pct": c_pct},
            "carryover":  {"score": co_score, "pct": co_pct},
            "cycle_time": {"score": cy_score, "pct": cy_pct},
            "bug_ratio":  {"score": b_score,  "pct": b_pct},
        },
        "burndown": bd, "blocked_count": blockers, "new_bugs": new_bugs,
    })

    return {
        "sprint_name": ss.name, "sprint_start": ss.start_str, "sprint_end": ss.end_str,
        "sprint_state": ss.state, "sprint_progress_pct": sp,
        "elapsed_days": ss.elapsed_days, "total_days": ss.total_days,
        "health_score": health, "health_emoji": emoji, "health_label": label,
        "bd_nudge": bd_nudge,
        "total": total, "done": done, "carried_over": carried_over,
        "bugs": bugs, "new_bugs": new_bugs, "new_bugs_done": new_bugs_done,
        "new_story_linked_bugs": new_story_linked_bugs,
        "carried_bugs": carried_bugs, "stories_done": stories_done,
        "new_bug_linkage": _build_bug_linkage_breakdown(new_bug_items),
        "carried_bug_linkage": _build_bug_linkage_breakdown(carried_bug_items),
        "bug_story_insights": _build_bug_story_insights(new_bug_items + carried_bug_items),
        "blocked_count": blockers, "flagged_count": flagged,
        "status_counts": status_counts,
        "unfinished_status_counts": unfinished_status_counts,
        "issue_type_counts": dict(sorted(issue_type_counts.items(), key=lambda x: -x[1])),
        "assignee_counts":   dict(sorted(assignee_counts.items(),   key=lambda x: -x[1])),
        "age_buckets": age_buckets,
        "avg_unfinished_age_days": round(sum(age_values)/len(age_values), 1) if age_values else None,
        "no_data_signals": no_data_signals,
        "signals": {
            "commitment": {
                "score": c_score, "pct": c_pct,
                "raw": f"{_format_decimal(completed_scope)}/{_format_decimal(committed_scope)} story scope done",
                "no_data": committed_scope == 0,
            },
            "carryover":  {
                "score": co_score, "pct": co_pct,
                "raw": f"{_format_decimal(official_rollover_scope)}/{_format_decimal(total_scope)} story scope rolled from previous sprint",
                "no_data": total_scope == 0,
            },
            "cycle_time": {
                "score": cy_score, "pct": cy_pct,
                "raw": f"avg {round(current_avg_ct,1) if current_avg_ct else 'N/A'} days" +
                       (f" (prev 3 sprints: {round(prev_avg_ct,1)})" if prev_avg_ct else ""),
                "no_data": current_avg_ct is None or prev_avg_ct is None,
            },
            "bug_ratio": {
                "score": b_score, "pct": b_pct,
                "raw": f"{new_story_linked_bugs} story-linked new bugs / {_format_decimal(bug_ratio_base_work)} story scope",
                "no_data": bug_ratio_base_work == 0 and new_story_linked_bugs == 0,
            },
        },
        "formula_breakdown": fb, "weights": dict(weights),
        "formula_expression": health_calc["formula"],
        "formula_context": health_calc["context"],
        "signal_thresholds": _signal_threshold_texts(),
        "ai_insights": ai_insights,
        "burndown": bd,
        "carryover_metrics": carryover_metrics,
        "carried_in_metrics": carried_in_metrics,
        "sprint_carryover_metrics": sprint_carryover_metrics,
        "bug_ratio_base": bug_ratio_base,
        "dev_activity": dev_activity,
        "qa_activity":  qa_activity,
        "today_bug_reports": today_bug_reports,
        "cycle_time_medians": cycle_time_medians,
        "bottlenecks": bottlenecks,
        "activity_date_options": activity_date_options,
        "bug_change_pct": bug_change_pct, "bug_change_arrow": bug_change_arrow,
        "current_avg_cycle_time": round(current_avg_ct, 1) if current_avg_ct is not None else None,
        "execution": {"completed": done, "unfinished": carried_over,
                      "completion_pct": c_pct, "carryover_pct": co_pct},
        "formula": (
            f"({c_score}├ù0.35) + ({co_score}├ù0.25) + ({cy_score}├ù0.20) + ({b_score}├ù0.20)"
            + (f" + burndown nudge ({bd_nudge:+d})" if bd_nudge else "")
            + f" = *{health}*"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ΓÇöΓÇöΓÇö HTML HELPERS ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def _build_burndown_svg(bd: dict) -> str:
    if not bd or not bd.get("actual_line"):
        return "<p style='color:#4a5568;font-style:italic'>No burndown data available.</p>"
    W, H   = 820, 320
    PAD_L, PAD_R, PAD_T, PAD_B = 54, 28, 20, 48
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    actual, ideal = bd["actual_line"], bd["ideal_line"]
    max_y  = max(bd["total_issues"], 1)
    def cx(day, total): return round(PAD_L + day / total * plot_w, 2)
    def cy(val):        return round(PAD_T + (1 - val / max_y) * plot_h, 2)
    ideal_pts  = " ".join(f"{cx(d, bd['total_days'])},{cy(v)}" for d, v in enumerate(ideal))
    actual_pts = " ".join(f"{cx(d, bd['total_days'])},{cy(v)}" for d, v in enumerate(actual))
    grid_lines = ""
    for pct in [0, 20, 40, 60, 80, 100]:
        val = max_y * pct / 100; y = cy(val)
        grid_lines += (
            f'<line x1="{PAD_L}" y1="{y}" x2="{W-PAD_R}" y2="{y}" stroke="#1e3a5f" stroke-width="1"/>'
            f'<text x="{PAD_L-6}" y="{y+4}" text-anchor="end" font-size="10" fill="#4a90d9">{round(max_y*pct/100)}</text>'
        )
    x_labels   = ""
    label_list = bd.get("ideal_labels", [])
    step       = max(1, len(label_list) // 6)
    for idx in range(0, len(label_list), step):
        x = cx(idx, bd["total_days"])
        x_labels += f'<text x="{x}" y="{H-PAD_B+16}" text-anchor="middle" font-size="10" fill="#4a90d9">{label_list[idx]}</text>'
    today_x    = cx(min(bd["elapsed_days"], bd["total_days"]), bd["total_days"])
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
    if not bd:
        return ""
    return f"""
    <div class="burndown-explainer">
      <div class="burndown-explainer-title">What This Workload Burndown Shows</div>
      <p class="burndown-explainer-copy">
        The burndown compares how much scope should be left each day versus how much scope is actually still open.
      </p>
      <div class="burndown-scope-note">
        Burndown scope here tracks Stories only. The Remaining Scope Breakdown below still shows all remaining work types.
      </div>
      <div class="burndown-legend">
        <span><i class="ideal"></i> Ideal line: the expected pace to finish on time</span>
        <span><i class="actual"></i> Actual line: the real remaining scope day by day</span>
        <span><i class="today"></i> Today marker: where the sprint stands right now</span>
      </div>
      <p class="burndown-explainer-copy">
        If the actual line stays above the ideal line, the sprint is burning slower than planned. If it reaches zero by the end date, the sprint scope is fully completed.
      </p>
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
    if not bd:
        return ""
    remaining_breakdown = bd.get("remaining_breakdown") or []
    story_item = next(
        (item for item in remaining_breakdown if str(item.get("type", "")).strip().lower() == "story"),
        None,
    )
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
        <div class="burndown-takeaway">
          <strong>{escape(top_type)}</strong>
          <span>Largest remaining work type</span>
        </div>
        <div class="burndown-takeaway">
          <strong>{top_share}%</strong>
          <span>Of remaining scope comes from {escape(top_type)}</span>
        </div>
        <div class="burndown-takeaway">
          <strong>{_format_decimal(behind_by, 0)} scope</strong>
          <span>Extra scope above ideal pace</span>
        </div>
        <div class="burndown-takeaway">
          <strong class="{risk_class}">{escape(risk_label)}</strong>
          <span>Burndown risk signal right now</span>
        </div>
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
      <circle cx="60" cy="60" r="{r}" fill="none" stroke="#00d4aa" stroke-width="14"
        stroke-linecap="round" stroke-dasharray="{completed_len} {c}" transform="rotate(-90 60 60)"/>
      <circle cx="60" cy="60" r="{r}" fill="none" stroke="#1a6bff" stroke-width="14"
        stroke-linecap="round" stroke-dasharray="{remaining_len} {c}" stroke-dashoffset="{-completed_len}"
        transform="rotate(-90 60 60)"/>
      <circle cx="60" cy="60" r="28" fill="#101d34"/>
      <text x="60" y="55" text-anchor="middle" class="details-donut-value">{pct}%</text>
      <text x="60" y="71" text-anchor="middle" class="details-donut-label">Complete</text>
    </svg>"""


def _build_age_distribution_chart_html(age_buckets: dict) -> str:
    if not age_buckets:
        return "<div class='details-empty'>No unfinished issues yet.</div>"
    max_val = max(age_buckets.values()) if age_buckets else 0
    max_val = max(max_val, 1)
    bars = []
    colors = ["#00d4aa", "#1a6bff", "#fbbf24", "#4a90d9"]
    for idx, (label, value) in enumerate(age_buckets.items()):
        height = max(10, round((value / max_val) * 140)) if value else 6
        bars.append(
            f"<div class='age-bar-col'>"
            f"<div class='age-bar-value'>{value}</div>"
            f"<div class='age-bar' style='height:{height}px;background:{colors[idx % len(colors)]}'></div>"
            f"<div class='age-bar-label'>{escape(label)}</div>"
            f"</div>"
        )
    return f"<div class='age-chart'>{''.join(bars)}</div>"


def _build_issue_type_breakdown_panel(issue_type_counts: dict, total: int) -> str:
    if not issue_type_counts:
        return "<div class='details-empty'>No issue type data.</div>"
    colors = {
        "Sub-task": "#00d4aa",
        "Bug": "#ff4757",
        "Story": "#1a6bff",
        "Enhancement": "#4a90d9",
        "Feature-Bug": "#a78bfa",
        "Task": "#fbbf24",
    }
    total = max(int(total or 0), 1)
    rows = []
    donut_segments = []
    offset = 0.0
    circumference = 2 * 3.14159 * 42
    sorted_items = list(issue_type_counts.items())[:6]
    for issue_type, count in sorted_items:
        pct = round((count / total) * 100, 1)
        color = colors.get(issue_type, "#4a90d9")
        seg = round((count / total) * circumference, 2)
        donut_segments.append(
            f"<circle cx='60' cy='60' r='42' fill='none' stroke='{color}' stroke-width='14' "
            f"stroke-dasharray='{seg} {circumference}' stroke-dashoffset='{-offset}' transform='rotate(-90 60 60)'/>"
        )
        offset += seg
        rows.append(
            f"<div class='issue-type-row'>"
            f"<div class='issue-type-name'><i style='background:{color}'></i>{escape(issue_type)}</div>"
            f"<div class='issue-type-count'>{count}</div>"
            f"<div class='issue-type-pct'>{pct}%</div>"
            f"<div class='issue-type-track'><span style='width:{pct}%;background:{color}'></span></div>"
            f"</div>"
        )
    donut = (
        "<svg viewBox='0 0 120 120' class='details-donut' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='60' cy='60' r='42' fill='none' stroke='rgba(255,255,255,.07)' stroke-width='14'/>"
        + "".join(donut_segments) +
        f"<circle cx='60' cy='60' r='28' fill='#101d34'/><text x='60' y='64' text-anchor='middle' class='details-donut-value'>{total}</text></svg>"
    )
    return (
        "<div class='issue-type-layout'>"
        f"<div class='issue-type-donut-wrap'>{donut}</div>"
        f"<div class='issue-type-list'>{''.join(rows)}</div>"
        "</div>"
    )


def _build_assignee_workload_panel(assignee_counts: dict, total: int) -> str:
    if not assignee_counts:
        return "<div class='details-empty'>No assignee workload data.</div>"
    total = max(int(total or 0), 1)
    rows = []
    for assignee, count in list(assignee_counts.items())[:8]:
        share = round((count / total) * 100, 1)
        primary = min(100, round(share * 0.55, 1))
        secondary = min(100 - primary, round(share * 0.30, 1))
        tertiary = min(100 - primary - secondary, round(share * 0.15, 1))
        rows.append(
            f"<div class='assignee-row'>"
            f"<div class='assignee-name'>{escape(assignee)}</div>"
            f"<div class='assignee-load'>"
            f"<span class='seg seg-a' style='width:{primary}%'></span>"
            f"<span class='seg seg-b' style='width:{secondary}%'></span>"
            f"<span class='seg seg-c' style='width:{tertiary}%'></span>"
            f"</div>"
            f"<div class='assignee-count'>{count}</div>"
            f"</div>"
        )
    return f"<div class='assignee-list'>{''.join(rows)}</div>"


def _build_sprint_details_html(r: dict) -> str:
    completion_pct = round((r.get("done", 0) / max(r.get("total", 0), 1)) * 100)
    sprint_state = (r.get("sprint_state") or "").strip()
    state_label = "Active" if sprint_state == "active" else ("Extended" if sprint_state == "extended" else sprint_state.title())
    summary = (
        f"<div class='details-summary-bar'>"
        f"<div class='details-summary-main'>{escape(r.get('sprint_name', 'Sprint'))} "
        f"<span class='details-state'>({escape(state_label)})</span></div>"
        f"<div class='details-summary-meta'>Dates: {escape(r.get('sprint_start') or 'N/A')} - {escape(r.get('sprint_end') or 'N/A')}</div>"
        f"<div class='details-summary-meta'>Total Issues: {r.get('total', 0)} ({completion_pct}% complete)</div>"
        f"</div>"
    )
    avg_age = r.get("avg_unfinished_age_days")
    avg_age_label = f"{avg_age} Days" if avg_age is not None else "N/A"
    return f"""
    <div class="section-title">Sprint Details</div>
    <div class="sprint-details-shell">
      {summary}
      <div class="sprint-details-grid">
        <div class="details-panel">
          <div class="details-panel-head">
            <h3>Carryover Breakdown &amp; Status Summary</h3>
            <span>Status view</span>
          </div>
          <div class="details-subpanel">
            <div class="details-subtitle">Progress Overview</div>
            <div class="details-progress-layout">
              {_build_progress_donut_svg(r.get('done', 0), r.get('total', 0))}
              <div class="details-legend">
                <div><i class="done"></i>Completed</div>
                <div><i class="progress"></i>Carryover</div>
                <div><i class="blocked"></i>Blocked / On Hold</div>
              </div>
            </div>
          </div>
          <div class="details-subpanel">
            <div class="details-subpanel-top">
              <div class="details-subtitle">Critical Status List</div>
              <div class="details-subnote">Average Time in Status</div>
            </div>
            <div class="status-chip-grid">
              {''.join(
                  f"<div class='status-chip'><span>{escape(status)}</span><strong>{count}</strong></div>"
                  for status, count in list(sorted(r.get('unfinished_status_counts', {}).items(), key=lambda x: -x[1]))[:5]
              ) or "<div class='details-empty'>No critical statuses.</div>"}
            </div>
          </div>
        </div>
        <div class="details-panel">
          <div class="details-panel-head">
            <h3>Age of Unfinished Issues</h3>
            <span>Avg. {avg_age_label}</span>
          </div>
          <div class="details-big-metric">Average Age: {avg_age_label}</div>
          {_build_age_distribution_chart_html(r.get('age_buckets', {}))}
        </div>
        <div class="details-panel">
          <div class="details-panel-head">
            <h3>Issue Type Breakdown</h3>
            <span>Types</span>
          </div>
          {_build_issue_type_breakdown_panel(r.get('issue_type_counts', {}), r.get('total', 0))}
        </div>
        <div class="details-panel">
          <div class="details-panel-head">
            <h3>Workload by Assignee</h3>
            <span>Current Load</span>
          </div>
          {_build_assignee_workload_panel(r.get('assignee_counts', {}), r.get('total', 0))}
        </div>
        <div class="details-panel">
          <div class="details-panel-head">
            <h3>Cycle Time (Median)</h3>
            <span>"In Progress" &rarr; "Done"</span>
          </div>
          {_build_cycle_time_medians_panel_html(r.get('cycle_time_medians', {}))}
        </div>
        <div class="details-panel">
          <div class="details-panel-head">
            <h3>Blocked Time Ratio</h3>
            <span>Where execution is getting stuck</span>
          </div>
          {_build_blocked_time_ratio_panel_html(r.get('bottlenecks', {}))}
        </div>
      </div>
    </div>"""


def _build_cycle_time_medians_panel_html(medians: dict) -> str:
    if not medians:
        return "<div style='color:#8ab4d9;font-size:13px;padding:20px'>No cycle times yet.</div>"
    html = ""
    max_median = max(medians.values()) if medians else 0.0
    overall = round(sum(medians.values()) / len(medians), 1) if medians else 0.0
    
    html += f'<div class="details-big-metric" style="margin-bottom: 20px;">Overall Median: {overall} Days</div>'
    html += '<div class="issue-type-list">'
    
    for t_name, days in sorted(medians.items(), key=lambda x: -x[1]):
        icon, color = ALL_ISSUE_TYPES.get(t_name, DEFAULT_ISSUE_ICON)
        pct = round((days / max_median) * 100) if max_median > 0 else 0
        html += f"""
        <div class="issue-type-row" style="grid-template-columns: minmax(0, 1.4fr) 42px 1fr;">
          <div class="issue-type-name"><i style="background:{color}"></i>{escape(t_name)}</div>
          <div class="issue-type-count">{round(days, 1)}d</div>
          <div class="issue-type-track"><span style="width:{pct}%;background:{color}"></span></div>
        </div>"""
    html += '</div>'
    return html


def _build_blocked_time_ratio_panel_html(bottlenecks: dict) -> str:
    if not bottlenecks:
        return "<div style='color:#8ab4d9;font-size:13px;padding:20px'>No bottleneck data.</div>"
    ratio_pct = bottlenecks.get("blocked_ratio_pct", 0.0)
    top = bottlenecks.get("top_bottlenecks", [])
    worst_name = bottlenecks.get("worst_bottleneck_name")
    worst_days = bottlenecks.get("worst_bottleneck_days", 0.0)
    
    circumference = 263.89
    dash = (ratio_pct / 100.0) * circumference
    dash_array = f"{dash} {circumference}"
    
    legend_html = ""
    colors = ["#fbbf24", "#ff4757", "#a78bfa"]
    for i, t in enumerate(top):
        c = colors[i % len(colors)]
        legend_name = f'{t["name"]} (Bugs only)' if str(t.get("name", "")).strip().lower() == "open" else t["name"]
        legend_html += f'<div><i style="background:{c}"></i>{escape(legend_name)} <span style="color:#8ab4d9; font-size: 11px;">({round(t["pct"])}%)</span></div>'
        
    bottleneck_html = ""
    if worst_name:
        worst_label = f'{worst_name} (for Bugs only)' if str(worst_name).strip().lower() == "open" else worst_name
        bottleneck_html = f"""
        <div style="margin-top: 18px; font-size: 12px; line-height: 1.5; color: #8ab4d9; background: rgba(255,255,255,.03); padding: 12px 14px; border-radius: 10px; border: 1px solid rgba(26,107,255,.12);">
          <strong style="color:#e0eaff;">Top Bottleneck:</strong> Tickets sit longest in <em style="color:#fbbf24;">"{escape(worst_label)}"</em> status, costing an average of {round(worst_days, 1)} days per blocked issue.
        </div>"""
        
    return f"""
    <div class="details-progress-layout" style="margin-top: 14px;">
      <svg viewBox="0 0 120 120" class="details-donut" xmlns="http://www.w3.org/2000/svg">
        <circle cx="60" cy="60" r="42" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="14" />
        <circle cx="60" cy="60" r="42" fill="none" stroke="#fbbf24" stroke-width="14" stroke-linecap="round" stroke-dasharray="{dash_array}" transform="rotate(-90 60 60)" />
        <circle cx="60" cy="60" r="30" fill="#101d34" />
        <text x="60" y="56" text-anchor="middle" class="details-donut-value" style="font-size:15px;">{round(ratio_pct, 1)}%</text>
        <text x="60" y="69" text-anchor="middle" class="details-donut-label" style="font-size:7.5px;">Blocked</text>
      </svg>
      <div class="details-legend">
        {legend_html}
      </div>
    </div>
    {bottleneck_html}"""


def _issue_row_html(iss: dict, show_rft: bool = True) -> str:
    icon, color = ALL_ISSUE_TYPES.get(iss["type"], DEFAULT_ISSUE_ICON)
    done_style  = "opacity:0.6;text-decoration:line-through;" if iss.get("is_done") else ""
    stale_tag   = (
        f'<span class="issue-stale-tag">≡ƒö┤ Stale ({iss["active_days"]}d / {iss["stale_threshold"]}d)</span>'
        if iss.get("is_stale") else ""
    )
    active_tag = (
        f'<span class="issue-active-tag">Active {iss["active_days"]}d</span>'
        if iss.get("active_days", 0) > 1 and not iss.get("is_stale") else ""
    )
    pts_tag  = f'<span class="issue-pts-tag">{iss["story_points"]} pts</span>' if iss.get("story_points") else ""
    done_tag = '<span class="issue-done-tag">Γ£ô Done</span>' if iss.get("is_done") else ""
    rft_tag  = (
        f'<span class="issue-active-tag">≡ƒòÉ {format_duration_hours(iss["time_in_rft"])} in testing</span>'
        if show_rft and iss.get("time_in_rft", 0) > 0 else ""
    )
    transitions_html = "".join(
        f'<span class="issue-status-tag">{escape(tr)}</span>'
        for tr in (iss.get("transitions_today") or [])
    )
    return f"""
    <div class="dev-issue {'stale' if iss.get('is_stale') else ''}">
      <span class="issue-icon" style="color:{color}">{icon}</span>
      <div class="issue-body">
        <a href="{iss['url']}" target="_blank" class="issue-key">{iss['key']}</a>
        <span class="issue-summary" style="{done_style}">{escape(iss['summary'][:70])}{'ΓÇª' if len(iss['summary'])>70 else ''}</span>
        <div class="issue-tags">
          <span class="issue-status-tag">{escape(iss['status'])}</span>
          {pts_tag}{active_tag}{rft_tag}{stale_tag}{done_tag}{transitions_html}
        </div>
      </div>
    </div>"""


def _render_activity_date_select(date_options: list[dict], select_label: str) -> str:
    if not date_options:
        return ""
    initial = next((option for option in date_options if option.get("is_default")), date_options[0])
    options_html = "".join(
        f"<button type='button' class='activity-date-option{' active' if option.get('is_default') else ''}' "
        f"data-date-option='{escape(option['key'])}'>{escape(option['label'])}</button>"
        for option in date_options
    )
    return (
        f"<div class='activity-date-filter' data-date-dropdown='true' aria-label='{escape(select_label)}'>"
        f"<div class='activity-date-label'>{escape(select_label)}</div>"
        f"<button type='button' class='activity-date-trigger' aria-haspopup='listbox' aria-expanded='false'>"
        f"<span class='activity-date-trigger-text' data-date-value>{escape(initial['label'])}</span>"
        f"</button>"
        f"<div class='activity-date-menu' role='listbox'>{options_html}</div>"
        "</div>"
    )


def _build_dev_activity_html(dev_activity: dict[str, list], date_options: list[dict]) -> str:
    if not any(dev_activity.get(option["key"], []) for option in date_options):
        return (
            "<div class='qa-dashboard-shell dev-dashboard-shell interactive-activity-shell empty'>"
            "<div class='qa-dashboard-empty'>No developer activity recorded in the last 7 days.</div>"
            "</div>"
        )

    def _type_meta(issue_type: str) -> tuple[str, str, str, str]:
        normalized = (issue_type or "").strip().lower()
        if normalized == "bug":
            return "BUG", "qa-type-bug", "qa-card-bug", "Bug"
        if normalized == "story":
            return "STORY", "qa-type-story", "qa-card-story", "Story"
        if normalized == "task":
            return "TASK", "qa-type-task", "qa-card-task", "Task"
        if normalized == "sub-task":
            return "SUB", "qa-type-sub", "qa-card-sub", "Sub-task"
        return "ENH", "qa-type-enh", "qa-card-enh", "Enhancement"

    def _dev_transition_copy(issue: dict) -> tuple[str, str]:
        transitions = issue.get("transitions_today") or []
        if transitions:
            main = transitions[-1]
        else:
            main = (issue.get("status") or "Updated today").strip()
        if issue.get("is_stale"):
            sub = f"Active {issue.get('active_days', 0)} day(s) - stale after {issue.get('stale_threshold', 0)}"
        elif issue.get("time_in_rft", 0) > 0:
            sub = f"In testing for {format_duration_hours(issue['time_in_rft'])}"
        elif issue.get("active_days", 0) > 0:
            sub = f"Active for {issue['active_days']} day(s)"
        else:
            sub = "Updated today by developer"
        return main, sub

    def _dev_outcome_meta(issue: dict) -> tuple[str, str]:
        status = ((issue.get("status") or "").strip()).lower()
        transitions = " | ".join(issue.get("transitions_today") or []).lower()
        if issue.get("is_stale"):
            return "Needs Attention", "qa-status-reopened"
        if issue.get("is_done"):
            return "Done Today", "qa-status-done"
        if "code review" in status or "code review" in transitions:
            return "In Review", "qa-status-progress"
        if "ready for testing" in status:
            return "Ready For QA", "qa-status-passed"
        if "ready for pm review" in status:
            return "Ready For PM", "qa-status-passed"
        if "ready to release" in status:
            return "Ready To Release", "qa-status-passed"
        if "open" in status or "progress" in status or "pending" in status:
            return "Working", "qa-status-testing"
        return "Updated Today", "qa-status-progress"

    def _activity_tabs_html(issue_type_counts: dict, aria_label: str) -> str:
        tabs = [
            f"<button type='button' class='qa-tab active' data-filter='all'>All <strong>{issue_type_counts['all']}</strong></button>",
            f"<button type='button' class='qa-tab' data-filter='bug'>Bugs <strong>{issue_type_counts['bug']}</strong></button>",
            f"<button type='button' class='qa-tab' data-filter='story'>Stories <strong>{issue_type_counts['story']}</strong></button>",
        ]
        if issue_type_counts["task"] > 0:
            tabs.append(f"<button type='button' class='qa-tab' data-filter='task'>Tasks <strong>{issue_type_counts['task']}</strong></button>")
        if issue_type_counts["sub"] > 0:
            tabs.append(f"<button type='button' class='qa-tab' data-filter='sub'>Sub-tasks <strong>{issue_type_counts['sub']}</strong></button>")
        tabs.append(f"<button type='button' class='qa-tab' data-filter='enh'>Enhancements <strong>{issue_type_counts['enh']}</strong></button>")
        return f"<div class='qa-tabs' role='tablist' aria-label='{escape(aria_label)}'>{''.join(tabs)}</div>"

    html = (
        "<div class='qa-dashboard-shell dev-dashboard-shell interactive-activity-shell'>"
        "<div class='qa-dashboard-head'>"
        "<div>"
        "<div class='qa-dashboard-title'>Developer Activity</div>"
        "<div class='qa-dashboard-subtitle'>Developer-owned status changes grouped by developer for the selected day.</div>"
        "</div>"
        "<div class='activity-head-controls'>"
        "<label class='qa-dashboard-search'>"
        "<span class='qa-search-icon'>Γîò</span>"
        "<input type='search' class='qa-search-input' placeholder='Search issues, PM-XXXX...' aria-label='Search developer issues'>"
        "<span class='qa-filter-icon'>Γî»</span>"
        "</label>"
        f"{_render_activity_date_select(date_options, 'Developer Activity Date')}"
        "</div>"
        "</div>"
    )

    for option_index, option in enumerate(date_options):
        day_items = dev_activity.get(option["key"], []) or []
        issue_type_counts = {"all": 0, "bug": 0, "story": 0, "enh": 0, "task": 0, "sub": 0}
        sorted_devs = sorted(day_items, key=lambda d: (-len(d.get("issues", [])), d.get("name", "")))
        for dev in sorted_devs:
            for iss in dev.get("issues", []):
                issue_type_counts["all"] += 1
                normalized = (iss.get("type") or "").strip().lower()
                if normalized == "bug":
                    issue_type_counts["bug"] += 1
                elif normalized == "story":
                    issue_type_counts["story"] += 1
                elif normalized == "task":
                    issue_type_counts["task"] += 1
                elif normalized == "sub-task":
                    issue_type_counts["sub"] += 1
                else:
                    issue_type_counts["enh"] += 1

        html += (
            f"<div class='activity-date-pane{' active' if option.get('is_default') else ''}' data-date='{escape(option['key'])}'>"
            f"{_activity_tabs_html(issue_type_counts, 'Developer issue type filter')}"
        )

        if not sorted_devs:
            html += "<div class='qa-dashboard-empty'>No developer activity recorded for this date.</div></div>"
            continue

        html += "<div class='qa-tester-list'>"
        for index, dev in enumerate(sorted_devs):
            dev_name = dev.get("name", "Unknown")
            issues = sorted(dev.get("issues", []), key=lambda x: x.get("key", ""))
            if not issues:
                continue
            html += (
                f"<details class='qa-tester-section' {'open' if index == 0 else ''}>"
                f"<summary class='qa-tester-summary'>"
                f"<div class='qa-tester-summary-left'>"
                f"{_person_avatar_html(dev_name, dev.get('avatar', ''), 'qa-tester-avatar')}"
                f"<div class='qa-tester-name'>{escape(dev_name)}</div>"
                f"<div class='qa-tester-count'>{len(issues)} issue{'s' if len(issues) != 1 else ''}</div>"
                f"</div>"
                f"<div class='qa-tester-chevron' aria-hidden='true'></div>"
                f"</summary>"
                f"<div class='qa-tester-body'>"
                f"<div class='qa-issue-grid'>"
            )
            hidden_count = max(0, len(issues) - 6)
            for issue_index, iss in enumerate(issues):
                type_label, type_class, card_class, type_full = _type_meta(iss["type"])
                type_filter = (
                    "bug" if type_label == "BUG"
                    else "story" if type_label == "STORY"
                    else "task" if type_label == "TASK"
                    else "sub" if type_label == "SUB"
                    else "enh"
                )
                transition_main, transition_sub = _dev_transition_copy(iss)
                compact_status_label, compact_status_class = _dev_outcome_meta(iss)
                search_blob = " ".join([
                    iss.get("key", ""),
                    iss.get("summary", ""),
                    iss.get("type", ""),
                    dev_name,
                    iss.get("linked_story", ""),
                    iss.get("linked_story_summary", ""),
                    transition_main,
                    transition_sub,
                    compact_status_label,
                    iss.get("status", ""),
                ]).lower()
                linked_story_html = ""
                if (iss.get("type") or "").strip().lower() == "sub-task" and iss.get("linked_story"):
                    story_summary = (iss.get("linked_story_summary") or "").strip()
                    linked_story_label = escape(iss["linked_story"])
                    linked_story_text = (
                        f"{linked_story_label} - {escape(story_summary[:68])}{'...' if len(story_summary) > 68 else ''}"
                        if story_summary else linked_story_label
                    )
                    linked_story_html = (
                        f"<div class='qa-linked-story'>"
                        f"<span class='qa-linked-story-label'>Story</span>"
                        f"<span class='qa-linked-story-value'>{linked_story_text}</span>"
                        f"</div>"
                    )
                html += f"""
                <article class="qa-issue-card {card_class}{' hidden-by-limit' if issue_index >= 6 else ''}" data-activity-card="true" data-type="{type_filter}" data-search="{escape(search_blob)}">
                  <div class="qa-issue-top">
                    <div class="qa-issue-type {type_class}" title="{escape(type_full)}" aria-label="{escape(type_full)}">{type_label}</div>
                    <a href="{iss['url']}" target="_blank" class="qa-issue-key">{iss['key']}</a>
                  </div>
                  <a href="{iss['url']}" target="_blank" class="qa-issue-title">{escape(iss['summary'][:68])}{'...' if len(iss['summary']) > 68 else ''}</a>
                  {linked_story_html}
                  <div class="qa-issue-transition">
                    <div class="qa-issue-transition-main">{escape(transition_main)}</div>
                    <div class="qa-issue-transition-sub">{escape(transition_sub)}</div>
                  </div>
                  <div class="qa-issue-tags">
                    <span class="qa-mini-pill {compact_status_class}">{escape(compact_status_label)}</span>
                  </div>
                </article>"""
            html += "</div>"
            if hidden_count > 0:
                html += f"<button type='button' class='qa-show-more' data-expand='6'>Show More <span>+{hidden_count}</span></button>"
            html += "</div></details>"
        html += "</div></div>"
    html += "</div>"
    return html


def _build_qa_activity_html(qa_items: dict[str, list], date_options: list[dict]) -> str:
    if not any(qa_items.get(option["key"], []) for option in date_options):
        return (
            "<div class='qa-dashboard-shell interactive-activity-shell empty'>"
            "<div class='qa-dashboard-empty'>No QA activity recorded in the last 7 days.</div>"
            "</div>"
        )

    def _type_meta(issue_type: str) -> tuple[str, str, str, str]:
        normalized = (issue_type or "").strip().lower()
        if normalized == "bug":
            return "BUG", "qa-type-bug", "qa-card-bug", "Bug"
        if normalized == "story":
            return "STORY", "qa-type-story", "qa-card-story", "Story"
        if normalized == "task":
            return "TASK", "qa-type-task", "qa-card-task", "Task"
        if normalized == "sub-task":
            return "SUB", "qa-type-sub", "qa-card-sub", "Sub-task"
        return "ENH", "qa-type-enh", "qa-card-enh", "Enhancement"

    def _status_meta(status: str) -> tuple[str, str]:
        lowered = (status or "").strip().lower()
        if "done" in lowered or "review" in lowered or "release" in lowered:
            return "Done", "qa-status-done"
        if "testing" in lowered:
            return "Testing", "qa-status-testing"
        if "progress" in lowered or "pending" in lowered or "open" in lowered:
            return "In Progress", "qa-status-progress"
        return ((status or "Ready").strip().title(), "qa-status-progress")

    def _transition_tag(issue: dict) -> str:
        if issue.get("time_in_rft", 0) > 0:
            return f"{format_duration_hours(issue['time_in_rft'])} in testing"
        transitions = issue.get("transitions") or []
        if transitions:
            last = transitions[-1]
            to_status = (last.get("to") or "").strip()
            if to_status:
                return to_status
        return "Activity today"

    def _transition_copy(issue: dict) -> tuple[str, str]:
        transitions = issue.get("transitions") or []
        if transitions:
            last = transitions[-1]
            from_status = (last.get("from") or "").strip() or "Unknown"
            to_status = (last.get("to") or "").strip() or "Updated"
            if from_status.lower() == to_status.lower():
                main = f"Status touched: {to_status}"
            else:
                main = f"{from_status} -> {to_status}"
        else:
            main = (issue.get("status") or "Activity today").strip()
        if issue.get("time_in_rft", 0) > 0:
            sub = f"In testing for {format_duration_hours(issue['time_in_rft'])}"
        else:
            sub = "Updated today by QA"
        return main, sub

    def _outcome_meta(issue: dict) -> tuple[str, str]:
        transitions = issue.get("transitions") or []
        last_to = ((transitions[-1].get("to") if transitions else issue.get("status")) or "").strip().lower()
        if "reopen" in last_to:
            return "Reopened", "qa-status-reopened"
        if "pending" in last_to or "progress" in last_to or "open" in last_to:
            return "Sent Back To Dev", "qa-status-sentback"
        if "done" in last_to:
            return "Passed QA", "qa-status-done"
        if "review" in last_to or "release" in last_to:
            return "Ready For PM", "qa-status-passed"
        if "testing" in last_to:
            return "Still Testing", "qa-status-testing"
        if issue.get("time_in_rft", 0) > 0:
            return "Still Testing", "qa-status-testing"
        return "Updated In QA", "qa-status-progress"

    def _activity_tabs_html(issue_type_counts: dict, aria_label: str) -> str:
        tabs = [
            f"<button type='button' class='qa-tab active' data-filter='all'>All <strong>{issue_type_counts['all']}</strong></button>",
            f"<button type='button' class='qa-tab' data-filter='bug'>Bugs <strong>{issue_type_counts['bug']}</strong></button>",
            f"<button type='button' class='qa-tab' data-filter='story'>Stories <strong>{issue_type_counts['story']}</strong></button>",
        ]
        if issue_type_counts["task"] > 0:
            tabs.append(f"<button type='button' class='qa-tab' data-filter='task'>Tasks <strong>{issue_type_counts['task']}</strong></button>")
        if issue_type_counts["sub"] > 0:
            tabs.append(f"<button type='button' class='qa-tab' data-filter='sub'>Sub-tasks <strong>{issue_type_counts['sub']}</strong></button>")
        tabs.append(f"<button type='button' class='qa-tab' data-filter='enh'>Enhancements <strong>{issue_type_counts['enh']}</strong></button>")
        return f"<div class='qa-tabs' role='tablist' aria-label='{escape(aria_label)}'>{''.join(tabs)}</div>"

    html = (
        "<div class='qa-dashboard-shell interactive-activity-shell'>"
        "<div class='qa-dashboard-head'>"
        "<div>"
        "<div class='qa-dashboard-title'>QA Activity</div>"
        "<div class='qa-dashboard-subtitle'>Transitions and testing activity grouped by QA owner for the selected day.</div>"
        "</div>"
        "<div class='activity-head-controls'>"
        "<label class='qa-dashboard-search'>"
        "<span class='qa-search-icon'>Γîò</span>"
        "<input type='search' class='qa-search-input' placeholder='Search issues, PM-XXXX...' aria-label='Search QA issues'>"
        "<span class='qa-filter-icon'>Γî»</span>"
        "</label>"
        f"{_render_activity_date_select(date_options, 'QA Activity Date')}"
        "</div>"
        "</div>"
    )

    for option_index, option in enumerate(date_options):
        day_items = qa_items.get(option["key"], []) or []
        by_tester: dict[str, dict] = {}
        issue_type_counts = {"all": 0, "bug": 0, "story": 0, "enh": 0, "task": 0, "sub": 0}
        for item in day_items:
            tester = item.get("actor", "Unknown")
            if tester not in by_tester:
                by_tester[tester] = {"name": tester, "avatar": item.get("actor_avatar", ""), "issues": {}}
            elif item.get("actor_avatar") and not by_tester[tester].get("avatar"):
                by_tester[tester]["avatar"] = item.get("actor_avatar", "")
            key = item.get("key", "")
            tester_issues = by_tester[tester]["issues"]
            if key not in tester_issues:
                tester_issues[key] = {
                    "key": key,
                    "summary": item.get("summary", ""),
                    "type": item.get("type", ""),
                    "url": item.get("url", ""),
                    "story_points": item.get("story_points"),
                    "time_in_rft": item.get("time_in_rft", 0),
                    "transitions": [],
                    "seen": set(),
                }
            transition = (item.get("from_status", ""), item.get("status", ""), item.get("actor", "Unknown"))
            if transition not in tester_issues[key]["seen"]:
                tester_issues[key]["seen"].add(transition)
                tester_issues[key]["transitions"].append({
                    "from": transition[0],
                    "to": transition[1],
                    "actor": transition[2],
                })
        for tester in by_tester.values():
            for issue in tester["issues"].values():
                issue_type_counts["all"] += 1
                normalized = (issue.get("type") or "").strip().lower()
                if normalized == "bug":
                    issue_type_counts["bug"] += 1
                elif normalized == "story":
                    issue_type_counts["story"] += 1
                elif normalized == "task":
                    issue_type_counts["task"] += 1
                elif normalized == "sub-task":
                    issue_type_counts["sub"] += 1
                else:
                    issue_type_counts["enh"] += 1
        sorted_testers = sorted(by_tester.values(), key=lambda t: (-len(t["issues"]), t["name"].lower()))

        html += (
            f"<div class='activity-date-pane{' active' if option.get('is_default') else ''}' data-date='{escape(option['key'])}'>"
            f"{_activity_tabs_html(issue_type_counts, 'QA issue type filter')}"
        )

        if not sorted_testers:
            html += "<div class='qa-dashboard-empty'>No QA activity recorded for this date.</div></div>"
            continue

        html += "<div class='qa-tester-list'>"
        for index, tester in enumerate(sorted_testers):
            issues = sorted(tester["issues"].values(), key=lambda x: x["key"])
            html += (
                f"<details class='qa-tester-section' {'open' if index == 0 else ''}>"
                f"<summary class='qa-tester-summary'>"
                f"<div class='qa-tester-summary-left'>"
                f"{_person_avatar_html(tester['name'], tester.get('avatar', ''), 'qa-tester-avatar')}"
                f"<div class='qa-tester-name'>{escape(tester['name'])}</div>"
                f"<div class='qa-tester-count'>{len(issues)} issue{'s' if len(issues) != 1 else ''}</div>"
                f"</div>"
                f"<div class='qa-tester-chevron' aria-hidden='true'></div>"
                f"</summary>"
                f"<div class='qa-tester-body'>"
                f"<div class='qa-issue-grid'>"
            )
            hidden_count = max(0, len(issues) - 6)
            for issue_index, issue in enumerate(issues):
                type_label, type_class, card_class, type_full = _type_meta(issue["type"])
                type_filter = (
                    "bug" if type_label == "BUG"
                    else "story" if type_label == "STORY"
                    else "task" if type_label == "TASK"
                    else "sub" if type_label == "SUB"
                    else "enh"
                )
                compact_status_label, compact_status_class = _outcome_meta(issue)
                transition_main, transition_sub = _transition_copy(issue)
                search_blob = " ".join([
                    issue.get("key", ""),
                    issue.get("summary", ""),
                    issue.get("type", ""),
                    tester["name"],
                    transition_main,
                    transition_sub,
                    compact_status_label,
                ]).lower()
                html += f"""
                <article class="qa-issue-card {card_class}{' hidden-by-limit' if issue_index >= 6 else ''}" data-activity-card="true" data-type="{type_filter}" data-search="{escape(search_blob)}">
                  <div class="qa-issue-top">
                    <div class="qa-issue-type {type_class}" title="{escape(type_full)}" aria-label="{escape(type_full)}">{type_label}</div>
                    <a href="{issue['url']}" target="_blank" class="qa-issue-key">{issue['key']}</a>
                  </div>
                  <a href="{issue['url']}" target="_blank" class="qa-issue-title">{escape(issue['summary'][:68])}{'...' if len(issue['summary']) > 68 else ''}</a>
                  <div class="qa-issue-transition">
                    <div class="qa-issue-transition-main">{escape(transition_main)}</div>
                    <div class="qa-issue-transition-sub">{escape(transition_sub)}</div>
                  </div>
                  <div class="qa-issue-tags">
                    <span class="qa-mini-pill {compact_status_class}">{escape(compact_status_label)}</span>
                  </div>
                </article>"""
            html += "</div>"
            if hidden_count > 0:
                html += f"<button type='button' class='qa-show-more' data-expand='6'>Show More <span>+{hidden_count}</span></button>"
            html += "</div></details>"
        html += "</div></div>"
    html += "</div>"
    return html

# ΓÇöΓÇöΓÇö SLACK ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def _build_todays_bug_reports_html(bugs: dict[str, list], date_options: list[dict]) -> str:
    if not any(bugs.get(option["key"], []) for option in date_options):
        return (
            "<div class='bug-report-shell empty'>"
            "<div class='bug-report-empty'>No bugs or enhancements were created in the last 7 days.</div>"
            "</div>"
        )

    def _normalize_bug_status(status: str) -> tuple[str, str]:
        raw = (status or "").strip()
        lowered = raw.lower()
        if "reopen" in lowered:
            return "reopened", "Reopened"
        if "progress" in lowered:
            return "in-progress", "In Progress"
        if lowered in {"open", "to do", "todo", "selected for development", "pending fixes"}:
            return "open", "Open"
        return "other", raw.title() if raw else "Unknown"

    def _created_item_type_meta(issue_type: str) -> tuple[str, str]:
        normalized = (issue_type or "").strip().lower()
        if normalized in {"bug", "feature bug"}:
            return "BUG", "bug-ticket-type-bug"
        return "ENH", "bug-ticket-type-enh"

    html = (
        "<div class='bug-report-shell interactive-activity-shell'>"
        "<div class='bug-report-head'>"
        "<div>"
        "<div class='bug-report-title'>Bug & Enhancement Reports</div>"
        "<div class='bug-report-subtitle'>Created bugs and enhancements grouped by reporter for the selected day.</div>"
        "</div>"
        f"{_render_activity_date_select(date_options, 'Created Items Date')}"
        "</div>"
    )

    for option_index, option in enumerate(date_options):
        day_bugs = bugs.get(option["key"], []) or []
        by_creator: dict[str, dict] = {}
        status_counts = {"open": 0, "in-progress": 0, "reopened": 0}
        type_counts = {"bug": 0, "enh": 0}
        for bug in day_bugs:
            creator = bug.get("created_by", "Unknown")
            if creator not in by_creator:
                by_creator[creator] = {
                    "name": creator,
                    "avatar": bug.get("created_by_avatar", ""),
                    "bugs": [],
                }
            elif bug.get("created_by_avatar") and not by_creator[creator].get("avatar"):
                by_creator[creator]["avatar"] = bug.get("created_by_avatar", "")
            status_key, _ = _normalize_bug_status(bug.get("status", ""))
            if status_key in status_counts:
                status_counts[status_key] += 1
            issue_type_normalized = (bug.get("type") or "").strip().lower()
            if issue_type_normalized in {"bug", "feature bug"}:
                type_counts["bug"] += 1
            else:
                type_counts["enh"] += 1
            by_creator[creator]["bugs"].append(bug)

        metric_specs = [
            ("Total Items", len(day_bugs), "metric-total"),
            ("Bugs", type_counts["bug"], "metric-open"),
            ("Enhancements", type_counts["enh"], "metric-enh"),
            ("Open", status_counts["open"], "metric-open"),
            ("In Progress", status_counts["in-progress"], "metric-progress"),
            ("Reopened", status_counts["reopened"], "metric-reopened"),
        ]

        html += f"<div class='activity-date-pane{' active' if option.get('is_default') else ''}' data-date='{escape(option['key'])}'>"
        html += "<div class='bug-report-metrics'>"
        for label, value, css_class in metric_specs:
            html += (
                f"<div class='bug-report-metric {css_class}'>"
                f"<span class='bug-report-metric-label'>{escape(label)}</span>"
                f"<strong class='bug-report-metric-value'>{value}</strong>"
                "</div>"
            )
        html += "</div>"

        if not by_creator:
            html += "<div class='bug-report-empty'>No bugs or enhancements were created for this date.</div></div>"
            continue

        html += "<div class='bug-report-groups'>"
        for creator_info in sorted(by_creator.values(), key=lambda x: (-len(x["bugs"]), x["name"].lower())):
            creator = creator_info["name"]
            creator_bugs = creator_info["bugs"]
            html += (
                f"<section class='bug-person-card'>"
                f"<div class='bug-person-header'>"
                f"{_person_avatar_html(creator, creator_info.get('avatar', ''), 'bug-person-avatar')}"
                f"<div class='bug-person-meta'>"
                f"<div class='bug-person-name'>{escape(creator)}</div>"
                f"<div class='bug-person-count'>{len(creator_bugs)} item{'s' if len(creator_bugs) != 1 else ''}</div>"
                f"</div>"
                f"</div>"
                f"<div class='bug-person-grid'>"
            )
            for bug in sorted(creator_bugs, key=lambda b: b.get("key", "")):
                status_class, status_label = _normalize_bug_status(bug.get("status", ""))
                type_label, type_class = _created_item_type_meta(bug.get("type", ""))
                story_tag = (
                    f"<span class='bug-tag bug-tag-link'>Story: {escape(bug['linked_story'])}</span>"
                    if bug.get("is_linked_to_story")
                    else "<span class='bug-tag bug-tag-storyless'>No Story</span>"
                )
                sprint_label = escape(bug.get("sprint_placement", "Backlog"))
                html += f"""
                <article class="bug-ticket-card {status_class}" data-activity-card="true" data-type="{'bug' if type_label == 'BUG' else 'enh'}" data-search="{escape(' '.join([bug.get('key', ''), bug.get('summary', ''), bug.get('type', ''), creator, bug.get('status', ''), bug.get('linked_story', ''), sprint_label]).lower())}">
                  <div class="bug-ticket-top">
                    <div class="bug-ticket-top-left">
                      <span class="bug-ticket-type {type_class}">{type_label}</span>
                      <a href="{bug['url']}" target="_blank" class="bug-ticket-key">{bug['key']}</a>
                    </div>
                    <span class="bug-ticket-status {status_class}">{escape(status_label)}</span>
                  </div>
                  <a href="{bug['url']}" target="_blank" class="bug-ticket-summary">{escape(bug['summary'][:110])}{'...' if len(bug['summary']) > 110 else ''}</a>
                  <div class="bug-ticket-tags">
                    {story_tag}
                    <span class="bug-tag bug-tag-sprint">{sprint_label}</span>
                  </div>
                </article>"""
            html += "</div></section>"
        html += "</div></div>"
    html += "</div>"
    return html

def format_slack_message(r: dict) -> str:
    score      = r["health_score"]
    health_dot = "≡ƒƒó" if score >= 85 else "≡ƒƒí" if score >= 70 else "≡ƒƒá" if score >= 50 else "≡ƒö┤"
    filled     = round(score / 10)
    bar        = "Γûê" * filled + "Γûæ" * (10 - filled)

    def sig_dot(s): return "≡ƒƒó" if s >= 85 else "≡ƒƒí" if s >= 70 else "≡ƒƒá" if s >= 50 else "≡ƒö┤"
    def nd(k): return " _ΓÇö no data yet_" if r["signals"][k].get("no_data") else ""

    sigs    = r["signals"]
    fb      = r["formula_breakdown"]
    weights = r["weights"]

    sig_rows = (
        f"{sig_dot(sigs['commitment']['score'])}  *Commitment*  {sigs['commitment']['raw']}  ΓåÆ  *{sigs['commitment']['score']} pts*{nd('commitment')}\n"
        f"{sig_dot(sigs['carryover']['score'])}  *Carryover*   {sigs['carryover']['raw']}  ΓåÆ  *{sigs['carryover']['score']} pts*{nd('carryover')}\n"
        f"{sig_dot(sigs['cycle_time']['score'])}  *Cycle Time*  {sigs['cycle_time']['raw']}  ΓåÆ  *{sigs['cycle_time']['score']} pts*{nd('cycle_time')}\n"
        f"{sig_dot(sigs['bug_ratio']['score'])}  *Bug Ratio*   {sigs['bug_ratio']['raw']}  ΓåÆ  *{sigs['bug_ratio']['score']} pts*{nd('bug_ratio')}\n"
        f"≡ƒÉ¢  *New Bugs*  {r['new_bugs']} created ({r['new_bugs_done']} resolved)   |   ≡ƒôª *Carried* {r['carried_bugs']}"
    )

    bd = r.get("burndown", {})
    bd_line = ""
    if bd:
        track_icon = "Γ£à" if bd.get("on_track") else ("ΓÜá∩╕Å" if not bd.get("is_extended") else "≡ƒö┤")
        ext_note   = " _(sprint overran)_" if bd.get("is_extended") else ""
        bd_line    = (
            f"\n*Burndown*  Day {bd['elapsed_days']}/{bd['total_days']}  ┬╖  "
            f"{_format_decimal(float(bd['current_remaining']), 0)} scope remaining  ┬╖  Ideal: {_format_decimal(float(bd['ideal_remaining']), 0)}  ┬╖  "
            f"{track_icon} {'On track' if bd.get('on_track') else 'Behind'}{ext_note}  ┬╖  "
            f"Velocity: {bd['velocity']}/day  ┬╖  Projected: {bd['projected_end']}\n"
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
        f"  ΓÇó {k}: {v}" for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1])
    ) or "  ΓÇó No issues found"

    no_data_note   = "\n> Γä╣∩╕Å _No issues yet ΓÇö neutral score of 70 used._\n" if r["no_data_signals"] else ""
    state_banner   = ""
    if r["sprint_state"] == "extended":
        state_banner = "\n> ΓÜá∩╕Å _Sprint passed end date ΓÇö not yet closed._\n"
    elif r["sprint_state"] == "closed":
        state_banner = "\n> ≡ƒôï _Showing last closed sprint._\n"

    date_range    = f"{r['sprint_start']} ΓåÆ {r['sprint_end']}" if r["sprint_start"] and r["sprint_end"] else "Dates not set"
    progress_note = f"   ┬╖   Day {r.get('elapsed_days','?')}/{r.get('total_days','?')} ({r['sprint_progress_pct']}%)" if r.get("sprint_progress_pct") is not None else ""

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
        dev_lines = f"\n*Developer Activity ΓÇö {selected_activity_label}*\n"
        for dev in dev_activity_for_slack:
            stale_count = sum(1 for i in dev["issues"] if i["is_stale"])
            stale_note  = f" ΓÜá∩╕Å {stale_count} stale" if stale_count else ""
            dev_lines  += f"  ≡ƒæñ *{dev['name']}* ΓÇö {len(dev['issues'])} issue(s){stale_note}\n"
            for iss in dev["issues"]:
                icon, _ = ALL_ISSUE_TYPES.get(iss["type"], DEFAULT_ISSUE_ICON)
                stale_tag  = " ≡ƒö┤ _stale_" if iss["is_stale"] else ""
                active_tag = f" _(active {iss['active_days']}d)_" if iss["active_days"] > 1 else ""
                rft_tag    = f" _(≡ƒòÉ {format_duration_hours(iss['time_in_rft'])} testing)_" if iss.get("time_in_rft", 0) > 0 else ""
                dev_lines += f"    {icon} {iss['key']} ┬╖ {iss['status']}{active_tag}{rft_tag}{stale_tag}\n"

    # QA activity for Slack
    qa_lines = ""
    if qa_activity_for_slack:
        qa_lines = f"\n*QA Activity ΓÇö {selected_activity_label}*\n"
        for item in qa_activity_for_slack:
            icon, _ = ALL_ISSUE_TYPES.get(item["type"], DEFAULT_ISSUE_ICON)
            rft_tag  = f" _(≡ƒòÉ {format_duration_hours(item['time_in_rft'])})_" if item.get("time_in_rft", 0) > 0 else ""
            qa_lines += f"  {icon} *{item['key']}* {item['label']}{rft_tag} ┬╖ {item['summary'][:50]}\n"

    return (
        f"≡ƒôè  *Sprint Health Report*  ΓÇö  Lumofy QA\n"
        f"*{r['sprint_name']}*   ┬╖   {date_range}{progress_note}\n"
        f"{'ΓÇö' * 44}\n\n"
        f"{health_dot}  *Health Score:  {score} / 100*\n"
        f"`{bar}`\n_{r['health_label'].title()}_\n"
        f"{state_banner}{no_data_note}\n"
        f"*Signals*\n{sig_rows}\n{bd_line}\n"
        f"*Formula*\n{formula_line}\n\n"
        f"{'ΓÇö' * 44}\n"
        f"*Issue Status*\n{status_lines}\n"
        f"{dev_lines}{qa_lines}\n"
        f"≡ƒÉ¢ Bugs: *{r['bugs']}*   |   ≡ƒôª Scope: *{r['total']}*   |   ≡ƒÜº Blockers: *{r['blocked_count']}*\n\n"
        f"_Generated {r['generated_at']}  ┬╖  Lumofy QA Dashboard_"
    )


def format_slack_site_message(r: dict, site_url: str, pdf_url: str = "") -> str:
    score      = r["health_score"]
    health_dot = "≡ƒƒó" if score >= 85 else "≡ƒƒí" if score >= 70 else "≡ƒƒá" if score >= 50 else "≡ƒö┤"
    bugs_line  = f"New Bugs: {r['new_bugs']} | Carried: {r['carried_bugs']}"
    if r.get("bug_change_pct") is not None:
        p = abs(r["bug_change_pct"])
        bugs_line = f"New Bugs: {r['new_bugs']} ({r['bug_change_arrow']} {int(p) if float(p).is_integer() else p}%) | Carried: {r['carried_bugs']}"
    cycle_time = f"{r['current_avg_cycle_time']} days" if r.get("current_avg_cycle_time") is not None else "N/A"
    bd      = r.get("burndown", {})
    bd_note = f"\nBurndown: {_format_decimal(float(bd['current_remaining']), 0)} scope remaining ┬╖ {'Γ£à On track' if bd.get('on_track') else 'ΓÜá∩╕Å Behind'}" if bd else ""
    return (
        f"≡ƒÜÇ Sprint Health Report Ready ΓÇö Lumofy QA\n\nScore: {score}/100 {health_dot}\n"
        f"{bugs_line}\nCycle Time: {cycle_time}{bd_note}\n\n≡ƒöù View Report:\n{site_url}"
    )


# ΓÇöΓÇöΓÇö HTML REPORT ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

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
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
  background:
    radial-gradient(circle at 12% 10%, var(--bg-orb-a) 0%, transparent 24%),
    radial-gradient(circle at 88% 14%, var(--bg-orb-b) 0%, transparent 26%),
    radial-gradient(circle at 50% 0%, var(--bg-orb-c) 0%, transparent 34%),
    linear-gradient(180deg,var(--page-bg-alt) 0%, var(--page-bg) 58%, var(--page-bg-deep) 100%);
  color:var(--text-main);padding:32px 16px;min-height:100vh;position:relative;overflow-x:hidden}}
.container{{max-width:1060px;margin:0 auto;position:relative;z-index:1}}
.header{{text-align:center;margin-bottom:36px;padding:28px 24px;border-radius:28px;
  background:var(--glass-hero-bg);border:1px solid var(--glass-border);backdrop-filter:blur(22px) saturate(140%);
  box-shadow:var(--glass-shadow);position:relative;overflow:hidden}}
.header::before{{content:'';position:absolute;inset:0 0 auto 0;height:1px;background:var(--glass-highlight);opacity:.95}}
.lumofy-logo{{display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:18px}}
.logo-mark{{width:32px;height:32px;background:linear-gradient(135deg,#1a6bff,#00d4aa);
  clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);}}
.logo-text{{font-size:22px;font-weight:800;color:#fff;letter-spacing:-0.5px}}
.logo-text span{{color:#1a6bff}}
.header h1{{font-size:30px;font-weight:700;color:#fff;margin-bottom:6px}}
.header p{{font-size:13px;color:#4a90d9}}
.header-actions{{display:flex;justify-content:center;align-items:center;gap:12px;flex-wrap:wrap;margin-top:18px}}
.admin-cta{{display:inline-flex;align-items:center;gap:10px;padding:12px 18px;border-radius:999px;
  background:linear-gradient(135deg,#1a6bff,#00d4aa);color:#fff;text-decoration:none;font-size:13px;
  font-weight:700;letter-spacing:.2px;border:1px solid rgba(255,255,255,.16);
  box-shadow:0 10px 28px rgba(26,107,255,.24),0 4px 12px rgba(0,0,0,.18);
  transition:transform .2s ease,box-shadow .2s ease,filter .2s ease}}
.admin-cta:hover{{transform:translateY(-2px);filter:brightness(1.05);
  box-shadow:0 16px 34px rgba(26,107,255,.34),0 6px 16px rgba(0,0,0,.28)}}
.admin-cta svg{{width:18px;height:18px;fill:#fff;flex-shrink:0}}
body[data-theme="light"] .admin-cta{{box-shadow:0 12px 24px rgba(34,94,168,.12),0 6px 14px rgba(20,40,80,.08)}}
body[data-theme="light"] .logo-text,
body[data-theme="light"] .header h1,
body[data-theme="light"] .score-label{{color:var(--text-main)}}
body[data-theme="light"] .header p{{color:var(--text-soft)}}
body[data-theme="light"] .progress-bar-wrap{{background:rgba(34,94,168,.1);border-color:rgba(34,94,168,.16)}}
.progress-bar-wrap{{background:rgba(26,107,255,.15);border-radius:999px;height:4px;width:260px;margin:12px auto 0;border:1px solid rgba(26,107,255,.2)}}
.progress-bar-fill{{height:4px;border-radius:999px;background:linear-gradient(90deg,#1a6bff,#00d4aa)}}
.card{{background:var(--glass-panel-bg);backdrop-filter:blur(22px) saturate(140%);border-radius:24px;
  padding:32px 28px;margin-bottom:24px;border:1px solid var(--glass-border);box-shadow:var(--glass-shadow);
  position:relative;z-index:1}}
.card::before{{content:'';position:absolute;left:0;right:0;top:0;height:1px;background:var(--glass-highlight);opacity:.9}}
.card.dropdown-open{{z-index:30}}
.score-wrap{{text-align:center}}
.score-circle{{width:150px;height:150px;border-radius:50%;margin:0 auto 20px;
  display:flex;align-items:center;justify-content:center;flex-direction:column;font-weight:700;border:2px solid rgba(26,107,255,.3)}}
.score-circle.green{{background:linear-gradient(135deg,#00d4aa22,#00d4aa44);border-color:#00d4aa}}
.score-circle.yellow{{background:linear-gradient(135deg,#fbbf2422,#fbbf2444);border-color:#fbbf24}}
.score-circle.orange{{background:linear-gradient(135deg,#fb923c22,#fb923c44);border-color:#fb923c}}
.score-circle.red{{background:linear-gradient(135deg,#ff475722,#ff475744);border-color:#ff4757}}
.score-number{{font-size:52px;color:#fff;line-height:1}}
.score-number.green{{color:#00d4aa}}.score-number.yellow{{color:#fbbf24}}
.score-number.orange{{color:#fb923c}}.score-number.red{{color:#ff4757}}
.score-label{{font-size:12px;color:#4a90d9;margin-top:2px}}
.health-status{{font-size:17px;font-weight:600;margin-top:14px;color:#e0eaff}}
.health-sub{{font-size:12px;color:#4a90d9;margin-top:6px}}
.no-data-banner,.state-banner{{border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px}}
.no-data-banner{{background:rgba(251,191,36,.08);border-left:3px solid #fbbf24;color:#fbbf24}}
.state-banner.extended{{background:rgba(255,71,87,.08);border-left:3px solid #ff4757;color:#ff4757}}
.state-banner.closed{{background:rgba(26,107,255,.08);border-left:3px solid #1a6bff;color:#4a90d9}}
.section-title{{font-size:16px;font-weight:700;color:#4a90d9;margin:32px 0 14px;
  text-transform:uppercase;letter-spacing:.8px;display:flex;align-items:center;gap:8px}}
.section-title::after{{content:'';flex:1;height:1px;background:rgba(26,107,255,.2)}}
.signals-formula-note{{margin:-4px 0 16px;padding:10px 14px;border-radius:12px;font-size:12px;line-height:1.55;
  color:#9dc4f0;background:rgba(26,107,255,.06);border:1px solid rgba(26,107,255,.14)}}
.signals-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin-bottom:24px}}
.signal-card{{background:linear-gradient(180deg,rgba(12,25,49,.96),rgba(9,19,39,.92));border-radius:18px;padding:22px 18px 18px;
  border:1px solid rgba(26,107,255,.22);text-align:center;transition:transform .25s,border-color .25s,box-shadow .25s;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.02)}}
.signal-card:hover{{transform:translateY(-4px);border-color:#1a6bff;box-shadow:0 16px 34px rgba(0,0,0,.22)}}
.signal-label{{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:#56a0ff;margin-bottom:16px;min-height:28px}}
.signal-score{{font-size:58px;font-weight:900;margin-bottom:12px;display:flex;align-items:baseline;justify-content:center;gap:6px;line-height:1}}
.signal-score.red{{color:#ff4757}}.signal-score.orange{{color:#fb923c}}
.signal-score.yellow{{color:#fbbf24}}.signal-score.green{{color:#00d4aa}}
.signal-unit{{font-size:16px;color:#3b70b4;font-weight:800}}
.signal-metric{{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:8px;font-size:12px;color:#b4d4ff;
  font-weight:600;margin:0 0 12px;padding:10px 12px;border-radius:12px;background:rgba(26,107,255,.08);border:1px solid rgba(26,107,255,.12);
  min-height:48px}}
.signal-metric-main{{color:#b4d4ff}}
.signal-metric-sep{{color:#3b70b4;font-weight:900}}
.signal-metric-pct{{color:#ffffff;font-weight:800}}
.signal-benchmark{{font-size:11px;line-height:1.5;color:#8ab4d9;min-height:34px;margin-bottom:8px}}
.no-data-badge{{display:inline-block;background:rgba(251,191,36,.1);color:#fbbf24;
  font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;margin-bottom:8px;
  text-transform:uppercase;letter-spacing:.4px;border:1px solid rgba(251,191,36,.3)}}
.bug-cards{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:8px}}
@media(max-width:560px){{.bug-cards{{grid-template-columns:1fr}}}}
.bug-insight-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}
@media(max-width:560px){{.bug-insight-grid{{grid-template-columns:1fr}}}}
.bug-insight-card{{border-radius:14px;padding:16px 18px;background:rgba(255,255,255,.03);border:1px solid rgba(74,144,217,.16)}}
.bug-insight-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#4a90d9;margin-bottom:8px}}
.bug-insight-value{{font-size:24px;font-weight:900;color:#eef5ff;line-height:1.2;margin-bottom:6px}}
.bug-insight-sub{{font-size:12px;line-height:1.5;color:#8ab4d9}}
.bug-card{{border-radius:14px;padding:24px 20px;border:1px solid;text-align:center}}
.bug-card.new-bugs{{background:rgba(251,191,36,.06);border-color:rgba(251,191,36,.3)}}
.bug-card.carried-bugs{{background:rgba(255,71,87,.06);border-color:rgba(255,71,87,.3)}}
.bug-card-icon{{font-size:28px;margin-bottom:8px}}
.bug-card-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#4a90d9;margin-bottom:8px}}
.bug-card-count{{font-size:48px;font-weight:900;color:#fff;line-height:1;margin-bottom:4px}}
.new-bugs .bug-card-count{{color:#fbbf24}}.carried-bugs .bug-card-count{{color:#ff4757}}
.bug-card-sub{{font-size:11px;color:#4a90d9;margin-bottom:10px}}
.bug-card-ratio{{font-size:12px;color:#8ab4d9;margin-bottom:4px}}
.bug-card-resolved{{font-size:12px;color:#00d4aa;margin-bottom:8px}}
.bug-linkage-row{{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:10px 0 12px}}
.bug-link-pill{{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;font-size:11px;
  color:#dce9ff;background:rgba(255,255,255,.05);border:1px solid rgba(74,144,217,.18)}}
.bug-link-pill strong{{color:#fff}}
.bug-card-note{{font-size:10px;padding:4px 10px;border-radius:999px;display:inline-block}}
.new-bugs .bug-card-note{{background:rgba(251,191,36,.1);color:#fbbf24;border:1px solid rgba(251,191,36,.2)}}
.carried-bugs .bug-card-note{{background:rgba(74,144,217,.1);color:#4a90d9;border:1px solid rgba(74,144,217,.2)}}
.bd-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px;margin-bottom:20px}}
.bd-stat{{text-align:center;padding:14px 10px;background:rgba(26,107,255,.06);border-radius:10px;border:1px solid rgba(26,107,255,.15)}}
.bd-stat-val{{font-size:16px;font-weight:700;color:#e0eaff;margin-bottom:4px}}
.bd-stat-val.green{{color:#00d4aa}}.bd-stat-val.red{{color:#ff4757}}
.bd-stat-lbl{{font-size:10px;color:#4a90d9;text-transform:uppercase;letter-spacing:.5px}}
.burndown-full-width{{margin-bottom:22px}}
.burndown-bottom-grid{{display:grid;grid-template-columns:1.6fr 1fr;gap:22px;align-items:stretch}}
.scope-breakdown{{display:flex;flex-direction:column;gap:12px;border-radius:14px}}
.scope-breakdown-title{{font-size:13px;font-weight:900;color:var(--text-main);letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;display:flex;align-items:center;gap:10px}}
.scope-breakdown-title::after{{content:'';height:1px;flex:1;background:var(--glass-border);opacity:0.5}}
.scope-breakdown-row{{display:flex;flex-direction:column;gap:6px;padding:14px 16px;border-radius:16px;background:var(--card-bg);border:1px solid var(--info-border);transition:all .3s ease;position:relative;overflow:hidden}}
.scope-breakdown-row:hover{{transform:translateX(4px);border-color:var(--ant-primary-500);background:rgba(255,255,255,0.02)}}
.scope-breakdown-row-top{{display:flex;justify-content:space-between;align-items:center;z-index:1}}
.scope-breakdown-row span:first-child{{color:var(--text-main);font-weight:700;font-size:14px}}
.scope-breakdown-row strong{{font-size:13px;color:var(--success-main);font-weight:800;padding:4px 10px;background:rgba(0,212,170,0.1);border-radius:8px}}
.scope-breakdown-bar-bg{{height:4px;background:rgba(255,255,255,0.05);border-radius:99px;width:100%;margin-top:4px;overflow:hidden}}
.scope-breakdown-bar-fill{{height:100%;border-radius:99px;background:var(--ant-primary-500);transition:width 1s cubic-bezier(0.16, 1, 0.3, 1)}}
.scope-breakdown-row.story .scope-breakdown-bar-fill{{background:linear-gradient(90deg,var(--ant-primary-400),var(--ant-primary-500))}}
.scope-breakdown-row.bug .scope-breakdown-bar-fill{{background:linear-gradient(90deg,var(--ant-error),#f43f5e)}}
.scope-breakdown-row.task .scope-breakdown-bar-fill{{background:linear-gradient(90deg,#fbbf24,#f59e0b)}}
.scope-breakdown-row.sub-task .scope-breakdown-bar-fill{{background:linear-gradient(90deg,#22d3ee,#06b6d4)}}
.scope-breakdown-row.enhancement .scope-breakdown-bar-fill{{background:linear-gradient(90deg,#8a7dff,#7c3aed)}}
.burndown-breakdown-under{{padding:24px 20px;background:var(--info-bg);border:1px solid var(--info-border);
  border-radius:14px;display:flex;flex-direction:column;align-self:stretch;height:100%}}
.burndown-explainer{{background:var(--info-bg);border:1px solid var(--info-border);border-radius:14px;
  padding:24px 20px;display:flex;flex-direction:column;gap:18px;align-self:stretch;height:100%}}
.burndown-explainer-title{{font-size:14px;font-weight:800;color:#e0eaff;letter-spacing:.2px}}
.burndown-explainer-copy{{font-size:12px;line-height:1.7;color:#8ab4d9}}
.formula-breakdown{{margin-bottom:16px;padding:16px;background:rgba(26,107,255,.06);border-radius:10px;border-left:3px solid #1a6bff}}
.formula-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;font-size:13px;color:#8ab4d9}}
.formula-row:last-child{{margin-bottom:0}}
.formula-component{{display:flex;align-items:center;gap:8px}}
.formula-code{{font-family:'Monaco','Courier New',monospace;background:rgba(26,107,255,.15);
  padding:2px 6px;border-radius:4px;font-size:11px;color:#4a90d9;font-weight:600;border:1px solid rgba(26,107,255,.2)}}
.formula-final{{background:linear-gradient(135deg,rgba(26,107,255,.2),rgba(0,212,170,.1));
  border:1px solid rgba(26,107,255,.3);color:#e0eaff;padding:20px;border-radius:10px;
  text-align:center;font-size:14px;font-weight:700;margin-top:16px;font-family:'Monaco','Courier New',monospace}}
.formula-final .value{{font-size:28px;margin-top:8px;color:#1a6bff}}
.sprint-details-shell{{display:flex;flex-direction:column;gap:16px}}
.details-summary-bar{{display:flex;flex-wrap:wrap;gap:18px;align-items:center;padding:14px 18px;border-radius:14px;
  background:rgba(26,107,255,.08);border:1px solid rgba(26,107,255,.18)}}
.details-summary-main{{font-size:16px;font-weight:700;color:#e0eaff}}
.details-state{{color:#00d4aa}}
.details-summary-meta{{font-size:13px;color:#8ab4d9}}
.sprint-details-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.details-panel{{background:linear-gradient(180deg,rgba(15,30,54,.96),rgba(10,20,40,.92));border:1px solid rgba(26,107,255,.18);
  border-radius:16px;padding:16px;box-shadow:0 8px 24px rgba(0,0,0,.18)}}
.details-panel-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}}
.details-panel-head h3{{font-size:14px;color:#e0eaff}}
.details-panel-head span{{font-size:12px;color:#4a90d9}}
.details-subpanel{{padding:14px;border-radius:14px;background:rgba(255,255,255,.03);border:1px solid rgba(26,107,255,.12);margin-bottom:12px}}
.details-subpanel:last-child{{margin-bottom:0}}
.details-subtitle{{font-size:13px;font-weight:700;color:#e0eaff;margin-bottom:12px}}
.details-subpanel-top{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}}
.details-subnote{{font-size:12px;color:#8ab4d9}}
.details-progress-layout{{display:grid;grid-template-columns:130px 1fr;gap:18px;align-items:center}}
.details-donut{{width:120px;height:120px}}
.details-donut-value{{font-size:20px;font-weight:800;fill:#e0eaff}}
.details-donut-label{{font-size:10px;fill:#8ab4d9}}
.details-legend{{display:grid;gap:10px}}
.details-legend div{{display:flex;align-items:center;gap:10px;font-size:13px;color:#c6daf7}}
.details-legend i{{width:10px;height:10px;border-radius:999px;display:inline-block}}
.details-legend .done{{background:#00d4aa}}
.details-legend .progress{{background:#1a6bff}}
.details-legend .blocked{{background:#fbbf24}}
.status-chip-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}}
.status-chip{{padding:10px 8px;border-radius:12px;background:rgba(255,255,255,.03);border:1px solid rgba(26,107,255,.12);text-align:center}}
.status-chip span{{display:block;font-size:11px;color:#8ab4d9;margin-bottom:6px}}
.status-chip strong{{font-size:26px;color:#e0eaff}}
.details-big-metric{{font-size:18px;font-weight:800;color:#e0eaff;margin:4px 0 14px}}
.age-chart{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;align-items:end;height:190px;padding:8px 4px 0}}
.age-bar-col{{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:8px;height:100%}}
.age-bar-value{{font-size:12px;color:#e0eaff}}
.age-bar{{width:56px;max-width:100%;border-radius:12px 12px 4px 4px;box-shadow:inset 0 -10px 30px rgba(255,255,255,.08)}}
.age-bar-label{{font-size:12px;color:#8ab4d9}}
.issue-type-layout{{display:grid;grid-template-columns:180px 1fr;gap:18px;align-items:center}}
.issue-type-donut-wrap{{display:flex;justify-content:center}}
.issue-type-list{{display:grid;gap:10px}}
.issue-type-row{{display:grid;grid-template-columns:minmax(0,1.4fr) 42px 44px 1fr;gap:10px;align-items:center}}
.issue-type-name{{display:flex;align-items:center;gap:8px;font-size:13px;color:#e0eaff}}
.issue-type-name i{{width:9px;height:9px;border-radius:999px;display:inline-block}}
.issue-type-count,.issue-type-pct{{font-size:12px;color:#8ab4d9}}
.issue-type-track{{height:8px;background:rgba(255,255,255,.06);border-radius:999px;overflow:hidden}}
.issue-type-track span{{display:block;height:100%;border-radius:999px}}
.assignee-list{{display:grid;gap:10px}}
.assignee-row{{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(120px,1fr) 36px;gap:12px;align-items:center}}
.assignee-name{{font-size:13px;color:#e0eaff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.assignee-load{{display:flex;height:10px;background:rgba(255,255,255,.06);border-radius:999px;overflow:hidden}}
.assignee-load .seg{{display:block;height:100%}}
.assignee-load .seg-a{{background:#00d4aa}}
.assignee-load .seg-b{{background:#1a6bff}}
.assignee-load .seg-c{{background:#fbbf24}}
.assignee-count{{font-size:13px;color:#e0eaff;text-align:right}}
.details-empty{{font-size:13px;color:#8ab4d9;padding:12px 0}}
.tables-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
@media(max-width:680px){{.tables-grid{{grid-template-columns:1fr}}}}
@media(max-width:900px){{.burndown-layout{{grid-template-columns:1fr}}.burndown-explainer{{min-height:auto}}}}
@media(max-width:900px){{.sprint-details-grid{{grid-template-columns:1fr}}.status-chip-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}.issue-type-layout{{grid-template-columns:1fr}}.details-progress-layout{{grid-template-columns:1fr;justify-items:center}}}}
@media(max-width:640px){{.status-chip-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.issue-type-row{{grid-template-columns:minmax(0,1fr) 40px 40px 1fr}}.assignee-row{{grid-template-columns:1fr}}.assignee-count{{text-align:left}}}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{border-bottom:1px solid rgba(26,107,255,.1);padding:8px 10px;text-align:left;color:#8ab4d9}}
th{{background:rgba(26,107,255,.08);color:#4a90d9;font-size:10px;text-transform:uppercase;letter-spacing:.5px}}
td:first-child{{color:#e0eaff}}
.bar{{background:rgba(26,107,255,.1);border-radius:999px;height:6px;overflow:hidden;min-width:60px}}
.bar>span{{display:block;height:6px;background:linear-gradient(90deg,#1a6bff,#00d4aa)}}
.dev-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}}
.dev-card{{background:rgba(10,20,40,.9);border-radius:14px;border:1px solid rgba(26,107,255,.2);overflow:hidden}}
.dev-header{{display:flex;align-items:center;gap:12px;padding:16px 18px;
  background:rgba(26,107,255,.06);border-bottom:1px solid rgba(26,107,255,.15)}}
.dev-avatar{{width:40px;height:40px;border-radius:50%;border:2px solid rgba(26,107,255,.3)}}
.dev-avatar-placeholder{{width:40px;height:40px;border-radius:50%;
  background:linear-gradient(135deg,#1a6bff,#00d4aa);
  display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700;color:#fff}}
.dev-name{{font-size:14px;font-weight:700;color:#e0eaff}}
.dev-meta{{font-size:11px;color:#4a90d9;margin-top:2px}}
.dev-stale-badge{{display:inline-block;background:rgba(255,71,87,.15);color:#ff4757;
  font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;margin-left:6px;border:1px solid rgba(255,71,87,.3)}}
.dev-issues{{padding:12px 18px;display:flex;flex-direction:column;gap:10px}}
.dev-issue{{display:flex;align-items:flex-start;gap:10px;padding:10px;
  border-radius:8px;background:rgba(26,107,255,.04);border:1px solid rgba(26,107,255,.1);transition:border-color .2s}}
.dev-issue:hover{{border-color:rgba(26,107,255,.3)}}
.dev-issue.stale{{background:rgba(255,71,87,.04);border-color:rgba(255,71,87,.2)}}
.issue-icon{{font-size:18px;margin-top:2px;flex-shrink:0}}
.issue-body{{flex:1;min-width:0}}
.issue-key{{font-size:11px;font-weight:700;color:#1a6bff;text-decoration:none;font-family:'Monaco','Courier New',monospace}}
.issue-key:hover{{color:#4a90d9}}
.issue-summary{{display:block;font-size:12px;color:#8ab4d9;margin:2px 0 6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.issue-tags{{display:flex;flex-wrap:wrap;gap:4px}}
.issue-status-tag{{font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;
  background:rgba(26,107,255,.1);color:#4a90d9;border:1px solid rgba(26,107,255,.2);
  text-transform:uppercase;letter-spacing:.3px}}
.issue-stale-tag{{font-size:9px;font-weight:700;padding:2px 7px;border-radius:999px;
  background:rgba(255,71,87,.1);color:#ff4757;border:1px solid rgba(255,71,87,.3)}}
.issue-active-tag{{font-size:9px;font-weight:600;padding:2px 7px;border-radius:999px;
  background:rgba(0,212,170,.08);color:#00d4aa;border:1px solid rgba(0,212,170,.2)}}
.issue-pts-tag{{font-size:9px;font-weight:600;padding:2px 7px;border-radius:999px;
  background:rgba(167,139,250,.08);color:#a78bfa;border:1px solid rgba(167,139,250,.2)}}
.issue-done-tag{{font-size:9px;font-weight:600;padding:2px 7px;border-radius:999px;
  background:rgba(0,212,170,.08);color:#00d4aa;border:1px solid rgba(0,212,170,.2)}}
/* QA Activity */
.qa-group{{margin-bottom:20px}}
.qa-group:last-child{{margin-bottom:0}}
.qa-group-label{{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;
  margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid rgba(26,107,255,.15)}}
.qa-dashboard-shell{{display:flex;flex-direction:column;gap:18px;position:relative;z-index:1}}
.qa-dashboard-shell.empty{{min-height:140px;justify-content:center}}
.qa-dashboard-empty{{text-align:center;padding:28px 20px;border-radius:18px;color:#8ab4d9;
  border:1px dashed rgba(74,144,217,.28);background:rgba(12,25,49,.55);font-size:13px}}
.qa-dashboard-head{{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap}}
.activity-head-controls{{display:grid;grid-template-columns:280px 220px;align-items:stretch;gap:12px;
  width:auto;max-width:100%;margin-left:auto;position:relative;z-index:8}}
.qa-dashboard-title{{font-size:22px;font-weight:800;letter-spacing:.3px;color:#f5f8ff;text-transform:uppercase}}
.qa-dashboard-subtitle{{font-size:12px;color:#8ab4d9;margin-top:6px}}
.qa-dashboard-search{{min-width:0;width:100%;display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:11px 14px;border-radius:16px;background:rgba(10,20,40,.82);border:1px solid rgba(74,144,217,.24);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);height:48px}}
.activity-date-filter{{position:relative;display:flex;flex-direction:column;justify-content:center;gap:4px;padding:7px 14px;
  border-radius:16px;background:linear-gradient(180deg,rgba(12,25,49,.92),rgba(9,19,38,.96));
  border:1px solid rgba(74,144,217,.24);box-shadow:inset 0 1px 0 rgba(255,255,255,.03),0 10px 22px rgba(0,0,0,.12);
  min-height:48px;cursor:pointer;z-index:6}}
.activity-date-label{{font-size:10px;font-weight:700;color:#78aee8;white-space:nowrap;text-transform:uppercase;letter-spacing:.65px;line-height:1}}
.activity-date-trigger{{width:100%;display:flex;align-items:center;justify-content:space-between;gap:10px;background:transparent;border:none;
  color:#eef5ff;font-size:13px;font-weight:700;padding:0;cursor:pointer;text-align:left}}
.activity-date-trigger::after{{content:'';width:8px;height:8px;border-right:2px solid #8fc0ff;border-bottom:2px solid #8fc0ff;
  transform:rotate(45deg) translateY(-1px);transform-origin:center;transition:transform .18s ease, border-color .18s ease;flex:0 0 8px}}
.activity-date-filter.open .activity-date-trigger::after{{transform:rotate(225deg) translateY(-1px)}}
.activity-date-trigger-text{{display:block;min-width:0}}
.activity-date-menu{{position:absolute;left:0;right:0;top:calc(100% + 10px);bottom:auto;display:none;flex-direction:column;gap:6px;padding:10px;
  border-radius:16px;border:1px solid rgba(74,144,217,.24);background:linear-gradient(180deg,rgba(12,25,49,.98),rgba(8,16,31,.98));
  box-shadow:0 22px 40px rgba(0,0,0,.34), inset 0 1px 0 rgba(255,255,255,.03);z-index:40;
  opacity:0;transform:translateY(-8px) scale(.98);transform-origin:top center;
  transition:opacity .18s ease, transform .18s ease}}
.activity-date-filter.open .activity-date-menu{{display:flex;opacity:1;transform:translateY(0) scale(1)}}
.activity-date-option{{width:100%;display:flex;align-items:center;padding:10px 12px;border-radius:12px;border:1px solid transparent;
  background:rgba(255,255,255,.02);color:#dce9ff;font-size:13px;font-weight:700;cursor:pointer;text-align:left}}
.activity-date-option:hover{{background:rgba(26,107,255,.12);border-color:rgba(74,144,217,.2)}}
.activity-date-option.active{{background:linear-gradient(180deg,rgba(38,72,128,.45),rgba(22,46,86,.75));color:#eef5ff;
  border-color:rgba(74,144,217,.34);box-shadow:inset 0 -2px 0 #2a82ff}}
.activity-date-filter:hover{{border-color:rgba(74,144,217,.34);box-shadow:inset 0 1px 0 rgba(255,255,255,.03),0 12px 24px rgba(0,0,0,.16)}}
.activity-date-filter:focus-within{{border-color:rgba(88,166,255,.42);box-shadow:0 0 0 3px rgba(26,107,255,.14),inset 0 1px 0 rgba(255,255,255,.03)}}
.activity-date-filter.open{{z-index:50}}
.activity-date-pane{{display:none}}
.activity-date-pane.active{{display:flex;flex-direction:column;gap:20px;position:relative;z-index:1}}
.qa-search-icon,.qa-filter-icon{{font-size:16px;color:#8fc0ff;opacity:.9}}
.qa-search-input{{flex:1;background:transparent;border:none;outline:none;color:#dce9ff;font-size:12px}}
.qa-search-input::placeholder{{color:#7fa6d8;opacity:1}}
.qa-tabs{{display:flex;flex-wrap:wrap;gap:10px;padding-bottom:10px;border-bottom:1px solid rgba(74,144,217,.16)}}
.qa-tab{{display:inline-flex;align-items:center;gap:8px;padding:10px 14px;border-radius:14px;font-size:12px;font-weight:700;
  color:#90a8cb;background:rgba(255,255,255,.02);border:1px solid rgba(74,144,217,.12);cursor:pointer}}
.qa-tab strong{{color:#d8e6ff;font-size:11px}}
.qa-tab.active{{color:#eaf3ff;background:linear-gradient(180deg,rgba(38,72,128,.45),rgba(22,46,86,.75));
  border-color:rgba(74,144,217,.34);box-shadow:0 8px 22px rgba(26,107,255,.18), inset 0 -2px 0 #2a82ff}}
.qa-tester-list{{display:flex;flex-direction:column;gap:16px}}
.qa-tester-section{{border-radius:22px;border:1px solid rgba(74,144,217,.2);
  background:linear-gradient(180deg,rgba(12,25,49,.96),rgba(9,18,36,.94));box-shadow:0 14px 34px rgba(0,0,0,.18);overflow:hidden}}
.qa-tester-summary{{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:18px 20px;cursor:pointer}}
.qa-tester-summary::-webkit-details-marker{{display:none}}
.qa-tester-summary-left{{display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
.qa-tester-avatar{{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,rgba(74,144,217,.9),rgba(26,107,255,.55));color:#eaf3ff;font-weight:800;font-size:16px;
  border:1px solid rgba(159,200,255,.18);overflow:hidden;position:relative;flex:0 0 42px}}
.qa-tester-avatar img{{width:100%;height:100%;object-fit:cover;display:block}}
.qa-tester-avatar-fallback{{width:100%;height:100%;display:flex;align-items:center;justify-content:center}}
.qa-tester-name{{font-size:16px;font-weight:800;color:#f5f8ff}}
.qa-tester-count{{font-size:12px;color:#8fc0ff}}
.qa-tester-chevron{{width:10px;height:10px;position:relative;flex:0 0 10px;transition:transform .2s ease}}
.qa-tester-chevron::before{{content:'';position:absolute;inset:0;border-right:2px solid #bfd5f6;border-bottom:2px solid #bfd5f6;
  transform:rotate(45deg);transform-origin:center}}
.qa-tester-section[open] .qa-tester-chevron{{transform:rotate(180deg)}}
.qa-tester-body{{padding:0 20px 18px;border-top:1px solid rgba(74,144,217,.14)}}
.qa-issue-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;padding-top:18px}}
.qa-issue-card{{position:relative;padding:16px;border-radius:18px;background:rgba(13,26,49,.96);
  border:1px solid rgba(74,144,217,.18);box-shadow:0 12px 24px rgba(0,0,0,.14), inset 0 1px 0 rgba(255,255,255,.02);
  transition:transform .2s ease,border-color .2s ease,box-shadow .2s ease}}
.qa-issue-card:hover{{transform:translateY(-2px);border-color:rgba(74,144,217,.34);box-shadow:0 16px 30px rgba(0,0,0,.18),0 0 18px rgba(42,130,255,.08)}}
.qa-card-bug{{box-shadow:inset 3px 0 0 #ff6b7c,0 12px 24px rgba(0,0,0,.14)}}
.qa-card-story{{box-shadow:inset 3px 0 0 #58a6ff,0 12px 24px rgba(0,0,0,.14)}}
.qa-card-task{{box-shadow:inset 3px 0 0 #fbbf24,0 12px 24px rgba(0,0,0,.14)}}
.qa-card-sub{{box-shadow:inset 3px 0 0 #22d3ee,0 12px 24px rgba(0,0,0,.14)}}
.qa-card-enh{{box-shadow:inset 3px 0 0 #8a7dff,0 12px 24px rgba(0,0,0,.14)}}
.qa-issue-top{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}}
.qa-issue-type{{padding:6px 10px;border-radius:10px;font-size:10px;font-weight:800;letter-spacing:.7px;border:1px solid}}
.qa-type-bug{{color:#ff7b89;background:rgba(255,107,124,.12);border-color:rgba(255,107,124,.3)}}
.qa-type-story{{color:#67b3ff;background:rgba(88,166,255,.12);border-color:rgba(88,166,255,.28)}}
.qa-type-task{{color:#ffd66b;background:rgba(251,191,36,.12);border-color:rgba(251,191,36,.28)}}
.qa-type-sub{{color:#79d2ff;background:rgba(34,211,238,.12);border-color:rgba(34,211,238,.28)}}
.qa-type-enh{{color:#a99cff;background:rgba(138,125,255,.12);border-color:rgba(138,125,255,.28)}}
.qa-issue-key{{font-size:11px;font-weight:700;color:#8fc0ff;text-decoration:none}}
.qa-issue-badge{{margin-left:auto;padding:6px 11px;border-radius:999px;font-size:11px;font-weight:700;border:1px solid;white-space:nowrap}}
.qa-status-testing{{background:rgba(251,191,36,.14);border-color:rgba(251,191,36,.24);color:#f7cb6b}}
.qa-status-done{{background:rgba(0,212,170,.12);border-color:rgba(0,212,170,.22);color:#76e4ca}}
.qa-status-progress{{background:rgba(88,166,255,.12);border-color:rgba(88,166,255,.22);color:#80b8ff}}
.qa-status-passed{{background:rgba(72,211,190,.12);border-color:rgba(72,211,190,.22);color:#7cf0d8}}
.qa-status-sentback{{background:rgba(88,166,255,.12);border-color:rgba(88,166,255,.22);color:#80b8ff}}
.qa-status-reopened{{background:rgba(255,107,124,.14);border-color:rgba(255,107,124,.24);color:#ff94a0}}
.qa-issue-title{{display:block;font-size:14px;font-weight:700;line-height:1.45;color:#eef5ff;text-decoration:none;min-height:40px;margin-bottom:12px}}
.qa-issue-title:hover{{color:#dbeaff}}
.qa-linked-story{{display:flex;flex-direction:column;gap:4px;margin:-2px 0 12px;padding:10px 12px;border-radius:12px;
  background:rgba(88,166,255,.08);border:1px solid rgba(88,166,255,.22)}}
.qa-linked-story-label{{font-size:10px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:#7fb2ff}}
.qa-linked-story-value{{font-size:11px;line-height:1.45;color:#dce9ff;word-break:break-word}}
.qa-issue-transition{{margin-bottom:12px;padding:10px 12px;border-radius:12px;background:rgba(255,255,255,.03);border:1px solid rgba(74,144,217,.12)}}
.qa-issue-transition-main{{font-size:11px;font-weight:700;color:#dce9ff;margin-bottom:4px}}
.qa-issue-transition-sub{{font-size:10px;color:#8ab4d9;line-height:1.45}}
.qa-issue-tags{{display:flex;flex-wrap:wrap;gap:8px}}
.qa-mini-pill{{display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;font-size:11px;font-weight:700;border:1px solid}}
.qa-mini-key{{background:rgba(31,67,124,.5);border-color:rgba(74,144,217,.22);color:#8fc0ff}}
.qa-show-more{{width:max-content;min-width:180px;margin:16px auto 0;padding:11px 18px;border-radius:14px;text-align:center;
  font-size:12px;font-weight:700;color:#d9e6ff;border:1px solid rgba(74,144,217,.24);background:rgba(255,255,255,.02);cursor:pointer}}
.qa-show-more span{{color:#8fc0ff;margin-left:6px}}
.qa-show-more.hidden{{display:none}}
.qa-issue-card.hidden-by-limit,.qa-issue-card.hidden-by-filter{{display:none}}
.qa-tester-section.hidden-by-filter{{display:none}}
.qa-empty-state{{padding:14px 12px;text-align:center;font-size:12px;color:#8ab4d9}}
@media(max-width:980px){{
  .qa-issue-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}
}}
@media(max-width:720px){{
  .qa-dashboard-title{{font-size:19px}}
  .qa-tester-name{{font-size:15px}}
  .qa-issue-grid{{grid-template-columns:1fr}}
  .qa-dashboard-search{{min-width:100%}}
  .activity-head-controls{{width:100%;grid-template-columns:1fr}}
  .activity-date-filter{{width:100%}}
  .activity-date-select{{min-width:0;flex:1}}
}}
.bug-report-shell{{display:flex;flex-direction:column;gap:20px;position:relative;z-index:1}}
.bug-report-shell.empty{{min-height:140px;justify-content:center}}
.bug-report-empty{{text-align:center;padding:28px 20px;border-radius:18px;color:#8ab4d9;
  border:1px dashed rgba(74,144,217,.28);background:rgba(12,25,49,.55);font-size:13px}}
.bug-report-head{{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:2px}}
.bug-report-head .activity-date-filter{{width:220px;flex:0 0 220px}}
.bug-report-title{{font-size:22px;font-weight:800;letter-spacing:.3px;color:#f5f8ff;text-transform:uppercase}}
.bug-report-subtitle{{font-size:12px;color:#8ab4d9;margin-top:6px}}
.bug-report-meta{{padding:10px 16px;border-radius:14px;background:rgba(26,107,255,.08);
  border:1px solid rgba(74,144,217,.22);font-size:11px;font-weight:700;color:#cfe2ff;text-transform:uppercase;letter-spacing:.6px}}
.bug-report-metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px}}
.bug-report-metric{{padding:16px 18px;border-radius:16px;border:1px solid rgba(74,144,217,.18);
  background:linear-gradient(180deg,rgba(16,31,58,.96),rgba(10,20,40,.92));box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}}
.bug-report-metric-label{{display:block;font-size:11px;font-weight:700;color:#8ab4d9;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px}}
.bug-report-metric-value{{font-size:28px;line-height:1;color:#f5f8ff}}
.bug-report-metric.metric-total{{border-color:rgba(74,144,217,.24)}}
.bug-report-metric.metric-total .bug-report-metric-value{{color:#9fc8ff}}
.bug-report-metric.metric-open{{border-color:rgba(0,212,170,.28)}}
.bug-report-metric.metric-open .bug-report-metric-value{{color:#1ce6b3}}
.bug-report-metric.metric-enh{{border-color:rgba(138,125,255,.28)}}
.bug-report-metric.metric-enh .bug-report-metric-value{{color:#a99cff}}
.bug-report-metric.metric-progress{{border-color:rgba(251,191,36,.28)}}
.bug-report-metric.metric-progress .bug-report-metric-value{{color:#fbbf24}}
.bug-report-metric.metric-reopened{{border-color:rgba(255,71,87,.28)}}
.bug-report-metric.metric-reopened .bug-report-metric-value{{color:#ff6b7c}}
.bug-report-metric.metric-storyless{{border-color:rgba(167,139,250,.28)}}
.bug-report-metric.metric-storyless .bug-report-metric-value{{color:#a78bfa}}
.bug-report-groups{{display:grid;grid-template-columns:1fr;gap:18px}}
.bug-person-card{{padding:22px;border-radius:22px;border:1px solid rgba(74,144,217,.2);
  background:linear-gradient(180deg,rgba(12,25,49,.96),rgba(9,18,36,.94));box-shadow:0 16px 36px rgba(0,0,0,.2)}}
.bug-person-header{{display:flex;align-items:center;gap:14px;margin-bottom:18px}}
.bug-person-avatar{{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,rgba(74,144,217,.9),rgba(26,107,255,.55));color:#eaf3ff;font-weight:800;font-size:16px;
  border:1px solid rgba(159,200,255,.18);flex-shrink:0;overflow:hidden;position:relative}}
.bug-person-avatar img{{width:100%;height:100%;object-fit:cover;display:block}}
.bug-person-avatar-fallback{{width:100%;height:100%;display:flex;align-items:center;justify-content:center}}
.bug-person-meta{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.bug-person-name{{font-size:16px;font-weight:800;color:#f5f8ff}}
.bug-person-count{{padding:8px 12px;border-radius:999px;background:rgba(26,107,255,.14);border:1px solid rgba(74,144,217,.24);
  font-size:11px;font-weight:700;color:#8fc0ff}}
.bug-person-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}
.bug-ticket-card{{position:relative;padding:18px 18px 16px;border-radius:18px;background:rgba(13,26,49,.96);
  border:1px solid rgba(74,144,217,.18);box-shadow:inset 3px 0 0 #4a90d9,0 12px 24px rgba(0,0,0,.14),inset 0 1px 0 rgba(255,255,255,.02)}}
.bug-ticket-card.open{{box-shadow:inset 3px 0 0 #00d4aa,0 12px 24px rgba(0,0,0,.14),inset 0 1px 0 rgba(255,255,255,.02)}}
.bug-ticket-card.in-progress{{box-shadow:inset 3px 0 0 #f59e0b,0 12px 24px rgba(0,0,0,.14),inset 0 1px 0 rgba(255,255,255,.02)}}
.bug-ticket-card.reopened{{box-shadow:inset 3px 0 0 #ff4757,0 12px 24px rgba(0,0,0,.14),inset 0 1px 0 rgba(255,255,255,.02)}}
.bug-ticket-top{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}}
.bug-ticket-top-left{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
.bug-ticket-type{{display:inline-flex;align-items:center;padding:6px 10px;border-radius:10px;font-size:10px;font-weight:800;
  letter-spacing:.7px;border:1px solid;text-transform:uppercase}}
.bug-ticket-type-bug{{color:#ff7b89;background:rgba(255,107,124,.12);border-color:rgba(255,107,124,.3)}}
.bug-ticket-type-enh{{color:#a99cff;background:rgba(138,125,255,.12);border-color:rgba(138,125,255,.28)}}
.bug-ticket-key{{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:999px;text-decoration:none;
  background:rgba(26,107,255,.16);border:1px solid rgba(74,144,217,.2);color:#8fc0ff;font-size:11px;font-weight:800}}
.bug-ticket-summary{{display:block;color:#f5f8ff;text-decoration:none;font-size:14px;font-weight:700;line-height:1.45;margin-bottom:14px}}
.bug-ticket-summary:hover{{color:#cfe2ff}}
.bug-ticket-status{{padding:6px 11px;border-radius:999px;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.4px;
  border:1px solid rgba(74,144,217,.2);background:rgba(26,107,255,.12);color:#8fc0ff;white-space:nowrap}}
.bug-ticket-status.open{{background:rgba(0,212,170,.12);border-color:rgba(0,212,170,.25);color:#1ce6b3}}
.bug-ticket-status.in-progress{{background:rgba(251,191,36,.14);border-color:rgba(251,191,36,.25);color:#fbbf24}}
.bug-ticket-status.reopened{{background:rgba(255,71,87,.14);border-color:rgba(255,71,87,.25);color:#ff6b7c}}
.bug-ticket-tags{{display:flex;flex-wrap:wrap;gap:10px}}
.bug-tag{{display:inline-flex;align-items:center;padding:7px 11px;border-radius:999px;font-size:11px;font-weight:700;
  border:1px solid rgba(74,144,217,.2)}}
.bug-tag-link{{background:rgba(26,107,255,.12);color:#8fc0ff}}
.bug-tag-storyless{{background:rgba(167,139,250,.12);color:#c1a6ff;border-color:rgba(167,139,250,.24)}}
.bug-tag-sprint{{background:rgba(0,212,170,.12);color:#1ce6b3;border-color:rgba(0,212,170,.2)}}
@media(max-width:720px){{
  .bug-report-title{{font-size:19px}}
  .bug-person-name{{font-size:15px}}
  .bug-ticket-summary{{font-size:13px}}
}}
.interp-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
.interp-item{{padding:16px;border-radius:10px;border:1px solid;text-align:center}}
.interp-item.green{{border-color:rgba(0,212,170,.3);background:rgba(0,212,170,.05)}}
.interp-item.yellow{{border-color:rgba(251,191,36,.3);background:rgba(251,191,36,.05)}}
.interp-item.orange{{border-color:rgba(251,146,60,.3);background:rgba(251,146,60,.05)}}
.interp-item.red{{border-color:rgba(255,71,87,.3);background:rgba(255,71,87,.05)}}
.interp-range{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.interp-range.green{{color:#00d4aa}}.interp-range.yellow{{color:#fbbf24}}
.interp-range.orange{{color:#fb923c}}.interp-range.red{{color:#ff4757}}
.interp-status{{font-size:13px;font-weight:700;margin-bottom:4px;color:#e0eaff}}
.interp-desc{{font-size:11px;color:#4a90d9;line-height:1.4}}
.ai-title{{font-size:18px;font-weight:700;color:#e0eaff;margin-bottom:10px}}
.ai-summary{{font-size:13px;color:#8ab4d9;line-height:1.7}}
.ai-actions{{margin:14px 0 0 18px;color:#e0eaff}}
.ai-actions li{{margin-bottom:8px}}
:root{{
  --page-bg:#06111f;
  --page-bg-alt:#0b1730;
  --page-bg-deep:#050d1a;
  --bg-orb-a:rgba(52,118,255,.18);
  --bg-orb-b:rgba(0,212,170,.11);
  --bg-orb-c:rgba(255,120,80,.08);
  --text-main:#e0eaff;
  --text-soft:#8ab4d9;
  --text-accent:#4a90d9;
  --glass-panel-bg:linear-gradient(180deg,rgba(13,24,45,.72),rgba(9,18,35,.64));
  --glass-hero-bg:linear-gradient(180deg,rgba(14,26,48,.76),rgba(10,20,39,.64));
  --glass-border:rgba(137,179,255,.16);
  --glass-highlight:linear-gradient(90deg,rgba(255,255,255,.24),rgba(255,255,255,.05),rgba(255,255,255,0));
  --glass-shadow:0 20px 50px rgba(0,0,0,.22);
  --card-bg:linear-gradient(180deg,rgba(12,25,49,.96),rgba(9,19,39,.92));
  --card-border:rgba(26,107,255,.22);
  --panel-bg:linear-gradient(180deg,rgba(15,30,54,.96),rgba(10,20,40,.92));
  --panel-border:rgba(26,107,255,.18);
  --chip-bg:rgba(26,107,255,.08);
  --chip-border:rgba(26,107,255,.12);
  --theme-toggle-bg:rgba(255,255,255,.04);
  --theme-toggle-border:rgba(74,144,217,.2);
  --theme-toggle-text:#dce9ff;
}}
body[data-theme="light"]{{
  --page-bg:#eef4fb;
  --page-bg-alt:#f8fbff;
  --page-bg-deep:#e6eef8;
  --bg-orb-a:rgba(68,124,214,.13);
  --bg-orb-b:rgba(73,199,184,.11);
  --bg-orb-c:rgba(255,179,120,.10);
  --text-main:#19314f;
  --text-soft:#5f7694;
  --text-accent:#2f6fbc;
  --glass-panel-bg:linear-gradient(180deg,rgba(255,255,255,.72),rgba(248,251,255,.62));
  --glass-hero-bg:linear-gradient(180deg,rgba(255,255,255,.78),rgba(247,250,255,.66));
  --glass-border:rgba(123,153,193,.24);
  --glass-highlight:linear-gradient(90deg,rgba(255,255,255,.92),rgba(255,255,255,.38),rgba(255,255,255,0));
  --glass-shadow:0 18px 40px rgba(100,130,170,.12);
  --card-bg:linear-gradient(180deg,rgba(255,255,255,.72),rgba(248,251,255,.62));
  --card-border:rgba(123,153,193,.24);
  --panel-bg:linear-gradient(180deg,rgba(255,255,255,.78),rgba(246,250,255,.68));
  --panel-border:rgba(123,153,193,.22);
  --chip-bg:rgba(66,104,156,.06);
  --chip-border:rgba(93,128,176,.12);
  --theme-toggle-bg:rgba(255,255,255,.5);
  --theme-toggle-border:rgba(123,153,193,.24);
  --theme-toggle-text:#19314f;
}}
.footer{{text-align:center;margin-top:40px;padding:20px;color:#2d5a8e;font-size:11px}}
.report-particles{{position:fixed;inset:0;width:100%;height:100%;display:block;pointer-events:none;z-index:0;opacity:.96}}
.theme-toggle{{display:inline-flex;align-items:center;gap:10px;padding:10px 14px;border-radius:999px;
  background:var(--theme-toggle-bg);border:1px solid var(--theme-toggle-border);color:var(--theme-toggle-text);
  text-decoration:none;font-size:12px;font-weight:800;letter-spacing:.04em;cursor:pointer;backdrop-filter:blur(10px);
  transition:transform .2s ease,border-color .2s ease,background .2s ease}}
.theme-toggle:hover{{transform:translateY(-1px);border-color:#1a6bff}}
.theme-toggle-icon{{font-size:14px;line-height:1}}
body[data-theme="light"]{{color:var(--text-main)}}
body[data-theme="light"] .container{{color:var(--text-main)}}
body[data-theme="light"] .card,
body[data-theme="light"] .signal-card,
body[data-theme="light"] .details-panel,
body[data-theme="light"] .details-subpanel,
body[data-theme="light"] .qa-dashboard-shell,
body[data-theme="light"] .bug-report-shell,
body[data-theme="light"] .qa-tester-section,
body[data-theme="light"] .qa-issue-card,
body[data-theme="light"] .bug-person-card,
body[data-theme="light"] .bug-ticket-card,
body[data-theme="light"] .burndown-explainer,
body[data-theme="light"] .burndown-takeaways,
body[data-theme="light"] .scope-breakdown,
body[data-theme="light"] .bug-insight-card,
body[data-theme="light"] .activity-date-filter,
body[data-theme="light"] .activity-date-menu{{background:var(--card-bg)!important;border-color:var(--card-border)!important;box-shadow:0 12px 28px rgba(90,121,163,.10)}}
body[data-theme="light"] .score-wrap,
body[data-theme="light"] .formula-breakdown,
body[data-theme="light"] .formula-final{{background:var(--card-bg)!important;border-color:var(--card-border)!important}}
body[data-theme="light"] .section-title,
body[data-theme="light"] .details-panel-head h3,
body[data-theme="light"] .qa-dashboard-title,
body[data-theme="light"] .bug-report-title,
body[data-theme="light"] .health-status,
body[data-theme="light"] .score-number,
body[data-theme="light"] .signal-score,
body[data-theme="light"] .bd-stat-val,
body[data-theme="light"] .burndown-explainer-title,
body[data-theme="light"] .burndown-takeaways-title,
body[data-theme="light"] .scope-breakdown-title,
body[data-theme="light"] .qa-tester-name,
body[data-theme="light"] .qa-issue-title,
body[data-theme="light"] .bug-insight-value{{color:var(--text-main)!important}}
body[data-theme="light"] .health-sub,
body[data-theme="light"] .details-panel-head span,
body[data-theme="light"] .qa-dashboard-subtitle,
body[data-theme="light"] .signal-benchmark,
body[data-theme="light"] .signal-metric-main,
body[data-theme="light"] .bug-insight-sub,
body[data-theme="light"] .burndown-explainer-copy,
body[data-theme="light"] .burndown-scope-note,
body[data-theme="light"] .qa-tester-count,
body[data-theme="light"] .activity-date-label,
body[data-theme="light"] .activity-date-trigger-text,
body[data-theme="light"] .qa-search-input,
body[data-theme="light"] .qa-search-input::placeholder{{color:var(--text-soft)!important}}
body[data-theme="light"] .signal-metric,
body[data-theme="light"] .qa-linked-story,
body[data-theme="light"] .qa-issue-transition,
body[data-theme="light"] .bug-link-pill,
body[data-theme="light"] .signals-formula-note{{background:var(--chip-bg)!important;border-color:var(--chip-border)!important}}
body[data-theme="light"] .qa-tab{{background:rgba(16,35,63,.03);border-color:rgba(34,94,168,.12);color:var(--text-soft)}}
body[data-theme="light"] .qa-tab strong{{color:var(--text-main)}}
body[data-theme="light"] .qa-tab.active{{background:linear-gradient(180deg,rgba(34,94,168,.14),rgba(34,94,168,.08));color:var(--text-main);border-color:rgba(34,94,168,.22)}}
body[data-theme="light"] .activity-date-trigger::after{{border-right-color:var(--text-accent);border-bottom-color:var(--text-accent)}}
body[data-theme="light"] .qa-mini-pill,
body[data-theme="light"] .bug-card-note{{box-shadow:none}}
body[data-theme="light"] .signal-unit,
body[data-theme="light"] .signal-metric-sep,
body[data-theme="light"] .bug-card-sub,
body[data-theme="light"] .bd-stat-lbl,
body[data-theme="light"] .details-summary-meta,
body[data-theme="light"] .bug-report-subtitle,
body[data-theme="light"] .footer{{color:var(--text-soft)!important}}
body[data-theme="light"] .signal-metric-pct,
body[data-theme="light"] .bug-link-pill strong,
body[data-theme="light"] td:first-child,
body[data-theme="light"] .details-subtitle,
body[data-theme="light"] .details-summary-main,
body[data-theme="light"] .issue-type-name,
body[data-theme="light"] .assignee-name,
body[data-theme="light"] .assignee-count,
body[data-theme="light"] .qa-issue-transition-main,
body[data-theme="light"] .qa-linked-story-value,
body[data-theme="light"] .bug-person-name,
body[data-theme="light"] .bug-ticket-summary,
body[data-theme="light"] .interp-status,
body[data-theme="light"] .ai-title{color:var(--text-main)!important}
body[data-theme="light"] th{background:rgba(66,104,156,.06);color:var(--text-accent)}
body[data-theme="light"] td,
body[data-theme="light"] .formula-row,
body[data-theme="light"] .ai-summary,
body[data-theme="light"] .ai-actions,
body[data-theme="light"] .interp-desc{{color:var(--text-soft)!important}}
body[data-theme="light"] .status-chip,
body[data-theme="light"] .details-subpanel,
body[data-theme="light"] .burndown-summary div,
body[data-theme="light"] .burndown-takeaway,
body[data-theme="light"] .scope-breakdown-row,
body[data-theme="light"] .bug-report-meta,
body[data-theme="light"] .bug-report-metric,
body[data-theme="light"] .bug-ticket-card,
body[data-theme="light"] .qa-show-more{{background:rgba(255,255,255,.36)!important;border-color:rgba(123,153,193,.2)!important}}
body[data-theme="light"] .qa-issue-card,
body[data-theme="light"] .signal-card,
body[data-theme="light"] .bug-person-card{{box-shadow:0 16px 36px rgba(100,130,170,.10), inset 0 1px 0 rgba(255,255,255,.55)!important}}
.fab-wrapper{{position:fixed;right:24px;bottom:24px;z-index:9999}}
.fab-dashboard{{width:60px;height:60px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#1a6bff,#00d4aa);text-decoration:none;border:1px solid rgba(255,255,255,.12);
  box-shadow:0 10px 28px rgba(26,107,255,.32),0 6px 16px rgba(0,0,0,.28);
  transition:transform .2s ease,box-shadow .2s ease}}
.fab-dashboard:hover{{transform:translateY(-2px) scale(1.06);
  box-shadow:0 16px 34px rgba(26,107,255,.4),0 8px 18px rgba(0,0,0,.3)}}
.fab-dashboard svg{{width:26px;height:26px;fill:#fff}}
.fab-tooltip{{position:absolute;left:50%;bottom:72px;padding:8px 16px;border-radius:999px;
  background:rgba(10,20,40,.96);color:#e0eaff;font-size:12px;font-weight:700;
  border:1px solid rgba(26,107,255,.3);white-space:nowrap;opacity:0;transform:translateX(-50%) translateY(4px);
  pointer-events:none;transition:opacity .2s ease,transform .2s ease}}
.fab-wrapper:hover .fab-tooltip{{opacity:1;transform:translateX(-50%) translateY(0)}}
</style>
</head>
<body>
<div class="fab-wrapper">
  <a href="http://127.0.0.1:8765/admin" target="_blank" class="fab-dashboard" title="Open Admin Dashboard">
    <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z"/>
    </svg>
  </a>
  <div class="fab-tooltip">Admin Dashboard</div>
</div>
<canvas id="reportParticles" class="report-particles" aria-hidden="true"></canvas>
<div class="container">
  <div class="header">
    <div class="lumofy-logo">
      <div class="logo-mark"></div>
      <div class="logo-text">Lumo<span>fy</span></div>
    </div>
    <h1>Sprint Health Score</h1>
    <p>{date_range} &nbsp;|&nbsp; Day {r.get('elapsed_days','?')}/{r.get('total_days','?')} ({progress_pct}% through sprint)</p>
    <div class="progress-bar-wrap"><div class="progress-bar-fill" style="width:{progress_pct}%"></div></div>
    <div class="header-actions">
      <button type="button" class="theme-toggle" id="themeToggle" aria-label="Toggle color theme">
        <span class="theme-toggle-icon" id="themeToggleIcon">DM</span>
        <span id="themeToggleText">Theme</span>
      </button>
      <a href="http://127.0.0.1:8765/admin" target="_blank" class="admin-cta" title="Open Admin Dashboard">
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z"/>
        </svg>
        <span>Open Admin Dashboard</span>
      </a>
    </div>
  </div>
  {state_banner}{no_data_banner}
  <div class="card score-wrap">
    <div class="score-circle {score_class}">
      <div class="score-number {score_class}">{score}</div>
      <div class="score-label">/100</div>
    </div>
    <div class="health-status">{escape(r['health_label'].title())}</div>
    <div class="health-sub">{escape(r['sprint_name'])} &nbsp;|&nbsp; {escape(r['generated_at'])}</div>
  </div>
  <div class="section-title">Health Signals</div>
  {health_signals_formula_html}
  <div class="signals-grid">{signals_html}</div>
  <div class="section-title">Bug Breakdown</div>
  <div class="card">{bug_cards_html}</div>
  <div class="section-title">Total Scope Burndown</div>
  <div class="card">{burndown_stats}<div class="burndown-layout"><div class="burndown-chart-panel">{burndown_svg}{burndown_breakdown_html}</div><div class="burndown-side-panel">{burndown_explainer_html}{burndown_takeaways_html}</div></div></div>
  <div class="section-title">Developer Activity</div>
  <div class="card"><div class="dev-grid">{dev_activity_html}</div></div>
  <div class="section-title">QA Activity</div>
  <div class="card">{qa_activity_html}</div>
  <div class="section-title">Bug & Enhancement Reports</div>
  <div class="card">{today_bug_reports_html}</div>
  {ai_html}
  <div class="section-title">Weighted Formula</div>
  <div class="card">
    <div class="formula-breakdown">
      <div class="formula-row"><div class="formula-component"><span>Commitment Reliability</span><span class="formula-code">{sigs['commitment']['score']} x {weights['commitment']:.2f}</span></div><strong>= {fb['commitment']}</strong></div>
      <div class="formula-row"><div class="formula-component"><span>Carryover Rate</span><span class="formula-code">{sigs['carryover']['score']} x {weights['carryover']:.2f}</span></div><strong>= {fb['carryover']}</strong></div>
      <div class="formula-row"><div class="formula-component"><span>Cycle Time Stability</span><span class="formula-code">{sigs['cycle_time']['score']} x {weights['cycle_time']:.2f}</span></div><strong>= {fb['cycle_time']}</strong></div>
      <div class="formula-row"><div class="formula-component"><span>Bug Ratio (New Only)</span><span class="formula-code">{sigs['bug_ratio']['score']} x {weights['bug_ratio']:.2f}</span></div><strong>= {fb['bug_ratio']}</strong></div>
      {bd_nudge_html}
    </div>
    <div class="formula-final">
      {fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}
      {f"+ ({r['bd_nudge']:+d})" if r.get('bd_nudge') else ""}
      <div class="value">= {score}</div>
    </div>
  </div>
  {sprint_details_html}
  <div class="section-title">Score Interpretation</div>
  <div class="card">
    <div class="interp-grid">
      <div class="interp-item green"><div class="interp-range green">85-100</div><div class="interp-status">Predictable Sprint</div><div class="interp-desc">Excellent execution and stability</div></div>
      <div class="interp-item yellow"><div class="interp-range yellow">70-84</div><div class="interp-status">Some Instability</div><div class="interp-desc">Good progress, address minor risks</div></div>
      <div class="interp-item orange"><div class="interp-range orange">50-69</div><div class="interp-status">Execution Issues</div><div class="interp-desc">Needs attention on delivery</div></div>
      <div class="interp-item red"><div class="interp-range red">&lt;50</div><div class="interp-status">Sprint Breakdown</div><div class="interp-desc">Critical issues, act now</div></div>
    </div>
  </div>
    <div class="footer">Lumofy QA | Sprint Health Dashboard | {escape(r['generated_at'])}</div>
  </div>
</div>
<script>
(() => {{
  const storageKey = 'sprint-health-theme';
  const themeToggle = document.getElementById('themeToggle');
  const themeToggleText = document.getElementById('themeToggleText');
  const themeToggleIcon = document.getElementById('themeToggleIcon');

  function applyTheme(theme) {{
    document.body.dataset.theme = theme;
    if (themeToggleText) themeToggleText.textContent = theme === 'light' ? 'Dark Mode' : 'Light Mode';
    if (themeToggleIcon) themeToggleIcon.textContent = theme === 'light' ? 'DM' : 'LM';
  }}

  const savedTheme = localStorage.getItem(storageKey);
  const preferredTheme = savedTheme || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  applyTheme(preferredTheme);

  themeToggle?.addEventListener('click', () => {{
    const nextTheme = document.body.dataset.theme === 'light' ? 'dark' : 'light';
    localStorage.setItem(storageKey, nextTheme);
    applyTheme(nextTheme);
  }});

  const particleCanvas = document.getElementById('reportParticles');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  if (particleCanvas) {{
    const particleCtx = particleCanvas.getContext('2d', {{ alpha: true }});
    const DPR_LIMIT = 1.8;
    const iconSprites = [];
    const particles = [];
    let canvasWidth = 0;
    let canvasHeight = 0;
    let centerX = 0;
    let centerY = 0;
    let baseRadius = 0;
    let orbitTime = 0;
    let frameId = 0;
    let startedAt = performance.now();

    function clamp(value, min, max) {{
      return Math.max(min, Math.min(max, value));
    }}

    function easeOutCubic(t) {{
      return 1 - Math.pow(1 - t, 3);
    }}

    function particlePalette() {{
      return document.body.dataset.theme === 'light'
        ? {{
            ink: '#172B4D',
            brand: '#0052CC',
            muted: 'rgba(23, 43, 77, 0.22)',
            glow: 'rgba(0, 82, 204, 0.09)',
            coreA: 'rgba(255,255,255,0.95)',
            coreB: 'rgba(76,154,255,0.18)'
          }}
        : {{
            ink: '#DCE9FF',
            brand: '#4C9AFF',
            muted: 'rgba(220, 233, 255, 0.22)',
            glow: 'rgba(76, 154, 255, 0.12)',
            coreA: 'rgba(255,255,255,0.82)',
            coreB: 'rgba(76,154,255,0.20)'
          }};
    }}

    function makeSprite(drawFn, size) {{
      const offscreen = document.createElement('canvas');
      offscreen.width = size;
      offscreen.height = size;
      const ictx = offscreen.getContext('2d');
      drawFn(ictx, size);
      return offscreen;
    }}

    function rebuildSprites() {{
      const palette = particlePalette();
      iconSprites.length = 0;
      iconSprites.push(
        makeSprite((ictx, size) => {{
          ictx.strokeStyle = palette.brand;
          ictx.lineWidth = size * 0.11;
          ictx.lineCap = 'round';
          ictx.lineJoin = 'round';
          ictx.beginPath();
          ictx.moveTo(size * 0.24, size * 0.54);
          ictx.lineTo(size * 0.43, size * 0.72);
          ictx.lineTo(size * 0.76, size * 0.30);
          ictx.stroke();
        }}, 48),
        makeSprite((ictx, size) => {{
          ictx.fillStyle = palette.brand;
          ictx.beginPath();
          ictx.moveTo(size * 0.50, size * 0.10);
          ictx.lineTo(size * 0.82, size * 0.30);
          ictx.lineTo(size * 0.82, size * 0.70);
          ictx.lineTo(size * 0.50, size * 0.90);
          ictx.lineTo(size * 0.18, size * 0.70);
          ictx.lineTo(size * 0.18, size * 0.30);
          ictx.closePath();
          ictx.fill();
          ictx.clearRect(size * 0.39, size * 0.29, size * 0.22, size * 0.42);
        }}, 48),
        makeSprite((ictx, size) => {{
          ictx.strokeStyle = palette.ink;
          ictx.lineWidth = size * 0.10;
          ictx.lineCap = 'round';
          ictx.beginPath();
          ictx.moveTo(size * 0.24, size * 0.38);
          ictx.lineTo(size * 0.76, size * 0.38);
          ictx.moveTo(size * 0.24, size * 0.52);
          ictx.lineTo(size * 0.64, size * 0.52);
          ictx.moveTo(size * 0.24, size * 0.66);
          ictx.lineTo(size * 0.58, size * 0.66);
          ictx.stroke();
        }}, 48)
      );
    }}

    function resizeParticles() {{
      const dpr = Math.min(window.devicePixelRatio || 1, DPR_LIMIT);
      canvasWidth = window.innerWidth;
      canvasHeight = window.innerHeight;
      centerX = canvasWidth * 0.5;
      centerY = Math.min(380, canvasHeight * 0.27);
      baseRadius = Math.min(canvasWidth, canvasHeight) * 0.14;
      particleCanvas.width = Math.round(canvasWidth * dpr);
      particleCanvas.height = Math.round(canvasHeight * dpr);
      particleCanvas.style.width = `${{canvasWidth}}px`;
      particleCanvas.style.height = `${{canvasHeight}}px`;
      particleCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      rebuildSprites();
      rebuildParticles();
    }}

    function rebuildParticles() {{
      const palette = particlePalette();
      const count = canvasWidth < 900 ? 110 : 150;
      particles.length = 0;
      for (let i = 0; i < count; i += 1) {{
        const ratio = i / count;
        const orbitRadius = 28 + Math.pow(ratio, 1.32) * Math.min(canvasWidth, canvasHeight) * 0.42;
        const isIcon = i % 12 === 0;
        particles.push({{
          angle: Math.random() * Math.PI * 2,
          orbitRadius,
          speed: 0.00045 + Math.random() * 0.0012,
          twist: 0.8 + Math.random() * 1.4,
          drift: (Math.random() - 0.5) * 0.12,
          size: isIcon ? 10 + Math.random() * 6 : 1.8 + Math.random() * 3.2,
          alpha: isIcon ? 0.34 + Math.random() * 0.16 : 0.16 + Math.random() * 0.24,
          sprite: isIcon ? iconSprites[i % iconSprites.length] : null,
          color: i % 4 === 0 ? palette.brand : palette.muted
        }});
      }}
    }}

    function drawGlow(pulse) {{
      const palette = particlePalette();
      const gradient = particleCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, baseRadius * 1.8);
      gradient.addColorStop(0, palette.glow);
      gradient.addColorStop(0.46, document.body.dataset.theme === 'light' ? 'rgba(0,82,204,0.04)' : 'rgba(76,154,255,0.06)');
      gradient.addColorStop(1, 'rgba(0,0,0,0)');
      particleCtx.fillStyle = gradient;
      particleCtx.beginPath();
      particleCtx.arc(centerX, centerY, baseRadius * (1.08 + pulse * 0.05), 0, Math.PI * 2);
      particleCtx.fill();
    }}

    function drawCore(pulse) {{
      const palette = particlePalette();
      const gradient = particleCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, baseRadius * 0.48);
      gradient.addColorStop(0, palette.coreA);
      gradient.addColorStop(0.26, palette.coreB);
      gradient.addColorStop(1, 'rgba(0,0,0,0)');
      particleCtx.fillStyle = gradient;
      particleCtx.beginPath();
      particleCtx.arc(centerX, centerY, baseRadius * (0.12 + pulse * 0.012), 0, Math.PI * 2);
      particleCtx.fill();
    }}

    function drawParticle(particle, elapsed, pulse) {{
      const burst = easeOutCubic(clamp(elapsed / 2200, 0, 1));
      const angle = particle.angle + elapsed * particle.speed * particle.twist;
      const orbit = particle.orbitRadius * (0.84 + burst * 0.16);
      const x = centerX + Math.cos(angle + particle.drift) * orbit;
      const y = centerY + Math.sin(angle) * orbit * 0.72;

      particleCtx.save();
      particleCtx.translate(x, y);
      particleCtx.rotate(angle * 0.82);
      particleCtx.globalAlpha = particle.alpha * (0.88 + pulse * 0.12);

      if (particle.sprite) {{
        const size = particle.size * (1 + pulse * 0.03);
        particleCtx.drawImage(particle.sprite, -size / 2, -size / 2, size, size);
      }} else {{
        particleCtx.fillStyle = particle.color;
        particleCtx.beginPath();
        particleCtx.arc(0, 0, particle.size, 0, Math.PI * 2);
        particleCtx.fill();
      }}

      particleCtx.restore();
    }}

    function renderParticles(now) {{
      particleCtx.clearRect(0, 0, canvasWidth, canvasHeight);
      orbitTime += 16.67;
      const pulse = 0.5 + Math.sin((now - startedAt) * 0.0024) * 0.5;
      drawGlow(pulse);
      for (let i = 0; i < particles.length; i += 1) {{
        drawParticle(particles[i], orbitTime, pulse);
      }}
      drawCore(pulse);

      if (!prefersReducedMotion.matches) {{
        frameId = window.requestAnimationFrame(renderParticles);
      }}
    }}

    function startParticles() {{
      window.cancelAnimationFrame(frameId);
      startedAt = performance.now();
      orbitTime = 0;
      resizeParticles();
      renderParticles(startedAt);
    }}

    startParticles();
    window.addEventListener('resize', resizeParticles, {{ passive: true }});
    prefersReducedMotion.addEventListener('change', startParticles);
  }}

  const roots = Array.from(document.querySelectorAll('.interactive-activity-shell'));
  roots.forEach((root) => {{
    const searchInput = root.querySelector('.qa-search-input');
    const dateDropdown = root.querySelector('[data-date-dropdown="true"]');
    const dateTrigger = dateDropdown?.querySelector('.activity-date-trigger');
    const dateValue = dateDropdown?.querySelector('[data-date-value]');
    const dateOptions = Array.from(dateDropdown?.querySelectorAll('[data-date-option]') || []);
    const panes = Array.from(root.querySelectorAll('.activity-date-pane'));
    let activeFilter = 'all';
    let activeDate = dateOptions.find((option) => option.classList.contains('active'))?.dataset.dateOption || panes[0]?.dataset.date || '';

    function getActivePane() {{
      return panes.find((pane) => pane.dataset.date === activeDate) || panes[0] || null;
    }}

    function applyFilters() {{
      const term = (searchInput?.value || '').trim().toLowerCase();
      const activePane = getActivePane();
      panes.forEach((pane) => pane.classList.toggle('active', pane === activePane));
      if (dateValue) {{
        const activeOption = dateOptions.find((option) => option.dataset.dateOption === activeDate);
        if (activeOption) dateValue.textContent = activeOption.textContent || '';
      }}
      dateOptions.forEach((option) => option.classList.toggle('active', option.dataset.dateOption === activeDate));
      if (!activePane) return;

      const tabs = Array.from(activePane.querySelectorAll('.qa-tab[data-filter]'));
      tabs.forEach((item) => item.classList.toggle('active', (item.dataset.filter || 'all') === activeFilter));

      const sections = Array.from(activePane.querySelectorAll('.qa-tester-section'));
      sections.forEach((section) => {{
        const cards = Array.from(section.querySelectorAll('[data-activity-card="true"]'));
        const showMoreButton = section.querySelector('.qa-show-more');
        const expandLimit = Number(showMoreButton?.dataset.expand || '6');
        const expanded = showMoreButton?.dataset.expanded === 'true';

        let visibleCount = 0;
        cards.forEach((card) => {{
          const matchesFilter = activeFilter === 'all' || card.dataset.type === activeFilter;
          const matchesSearch = !term || (card.dataset.search || '').includes(term);
          const matches = matchesFilter && matchesSearch;
          card.classList.toggle('hidden-by-filter', !matches);

          if (!matches) {{
            card.classList.add('hidden-by-limit');
            return;
          }}

          visibleCount += 1;
          card.classList.toggle('hidden-by-limit', !expanded && visibleCount > expandLimit);
        }});

        const hiddenMatching = cards.filter((card) =>
          !card.classList.contains('hidden-by-filter') && card.classList.contains('hidden-by-limit')
        ).length;

        if (showMoreButton) {{
          if (hiddenMatching > 0) {{
            showMoreButton.classList.remove('hidden');
            showMoreButton.innerHTML = `Show More <span>+${{hiddenMatching}}</span>`;
          }} else {{
            showMoreButton.classList.add('hidden');
          }}
        }}

        section.classList.toggle('hidden-by-filter', visibleCount === 0);
      }});
    }}

    root.addEventListener('click', (event) => {{
      const tab = event.target.closest('.qa-tab[data-filter]');
      if (tab && root.contains(tab)) {{
        activeFilter = tab.dataset.filter || 'all';
        applyFilters();
        return;
      }}
      const dateOption = event.target.closest('[data-date-option]');
      if (dateOption && root.contains(dateOption)) {{
        activeDate = dateOption.dataset.dateOption || activeDate;
        activeFilter = 'all';
        dateDropdown?.classList.remove('open');
        root.closest('.card')?.classList.remove('dropdown-open');
        dateTrigger?.setAttribute('aria-expanded', 'false');
        applyFilters();
        return;
      }}
      const showMore = event.target.closest('.qa-show-more');
      if (showMore && root.contains(showMore)) {{
        showMore.dataset.expanded = 'true';
        applyFilters();
        return;
      }}
      const dateFilter = event.target.closest('.activity-date-filter');
      if (dateDropdown && dateFilter === dateDropdown && !dateOption) {{
        const nextOpen = !dateDropdown?.classList.contains('open');
        dateDropdown?.classList.toggle('open', nextOpen);
        root.closest('.card')?.classList.toggle('dropdown-open', nextOpen);
        dateTrigger.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
        return;
      }}
    }});

    if (searchInput) {{
      searchInput.addEventListener('input', applyFilters);
    }}

    applyFilters();
  }});

  document.addEventListener('click', (event) => {{
    document.querySelectorAll('[data-date-dropdown="true"].open').forEach((dropdown) => {{
      if (!dropdown.contains(event.target)) {{
        dropdown.classList.remove('open');
        dropdown.closest('.card')?.classList.remove('dropdown-open');
        const trigger = dropdown.querySelector('.activity-date-trigger');
        trigger?.setAttribute('aria-expanded', 'false');
      }}
    }});
  }});

  document.addEventListener('keydown', (event) => {{
    if (event.key !== 'Escape') return;
    document.querySelectorAll('[data-date-dropdown="true"].open').forEach((dropdown) => {{
      dropdown.classList.remove('open');
      dropdown.closest('.card')?.classList.remove('dropdown-open');
      const trigger = dropdown.querySelector('.activity-date-trigger');
      trigger?.setAttribute('aria-expanded', 'false');
    }});
  }});
}})();
</script>
</body>
</html>"""

    out = Path(output_path)
    out.write_text(html_text, encoding="utf-8")
    print(f"[ok] HTML report: {out.resolve()}")
    return str(out.resolve())


# ΓÇöΓÇöΓÇö PDF REPORT ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def write_pdf_report(r: dict, output_path: str | None = None) -> str | None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as pdf_canvas
    except Exception as e:
        print(f"[warn] PDF skipped: {e}")
        return None
    if not output_path:
        output_path = f"sprint-health-{datetime.now().strftime('%Y-%m-%d')}.pdf"
    out  = Path(output_path)
    c    = pdf_canvas.Canvas(str(out), pagesize=A4)
    W, H = A4
    y    = H - 50
    fb   = r["formula_breakdown"]
    bd   = r.get("burndown", {})
    lines = [
        "Lumofy ΓÇö Sprint Health Report",
        f"Sprint: {r['sprint_name']}",
        f"Dates:  {r['sprint_start']} ΓåÆ {r['sprint_end']}",
        f"State:  {r['sprint_state'].upper()}",
        "",
        f"Health Score: {r['health_score']}/100  ΓÇö  {r['health_label']}",
        "",
        "Signals:",
        f"  Commitment:  {r['signals']['commitment']['raw']}  ΓåÆ {r['signals']['commitment']['score']} pts",
        f"  Carryover:   {r['signals']['carryover']['raw']}   ΓåÆ {r['signals']['carryover']['score']} pts",
        f"  Cycle Time:  {r['signals']['cycle_time']['raw']}  ΓåÆ {r['signals']['cycle_time']['score']} pts",
        f"  Bug Ratio:   {r['signals']['bug_ratio']['raw']}   ΓåÆ {r['signals']['bug_ratio']['score']} pts",
        "",
        "Bug Breakdown:",
        f"  New Bugs:     {r['new_bugs']}  ({r['new_bugs_done']} resolved)",
        f"  Carried Bugs: {r['carried_bugs']}  (display only)",
        "",
    ]
    if bd:
        lines += [
            "Burndown:",
            f"  Day {bd['elapsed_days']}/{bd['total_days']}  |  {_format_decimal(float(bd['current_remaining']), 0)} scope remaining  |  Ideal: {_format_decimal(float(bd['ideal_remaining']), 0)}",
            f"  Velocity: {bd['velocity']}/day  |  Projected: {bd['projected_end']}",
            f"  Status: {'On track' if bd.get('on_track') else 'Behind'}",
            "",
        ]
    lines += [
        f"Formula: {fb['commitment']} + {fb['carryover']} + {fb['cycle_time']} + {fb['bug_ratio']}"
        + (f" + ({r['bd_nudge']:+d})" if r.get('bd_nudge') else "")
        + f" = {r['health_score']}",
        "",
        "Issue Status:",
    ]
    for k, v in sorted(r["status_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"  {k}: {v}")
    lines += ["", f"Bugs: {r['bugs']}  |  Scope: {r['total']}  |  Generated: {r['generated_at']}"]
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, lines[0])
    y -= 28
    c.setFont("Helvetica", 11)
    for line in lines[1:]:
        if y < 50:
            c.showPage(); c.setFont("Helvetica", 11); y = H - 50
        c.drawString(50, y, line)
        y -= 15
    c.save()
    print(f"[ok] PDF report: {out.resolve()}")
    return str(out.resolve())


# ΓÇöΓÇöΓÇö SLACK SEND ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

def send_to_slack(message: str) -> None:
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json"},
        json={"channel": SLACK_CHANNEL, "text": message, "mrkdwn": True},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Slack error: {result.get('error')}")
    print(f"[ok] Slack ts={result.get('ts')}")


# ΓÇöΓÇöΓÇö MAIN RUN ΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇöΓÇö

