import re, codecs

def extract():
    # Read old logic
    try:
        with codecs.open('main_sprint_health_2.py', 'r', 'utf-16le') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    # List of functions to extract (exact or partial names)
    # Based on research, we need these specifically for imports 
    # and the UI logic discovered in previous steps.
    functions = [
        '_format_decimal',
        'format_duration_hours',
        'format_slack_message',
        'format_slack_site_message',
        '_person_avatar_html',
        '_issue_tag_html',
        '_build_stats_html',
        '_signal_card_html',
        '_qa_tester_section_html',
        '_activity_card_html',
        '_build_dev_activity_html',
        '_build_qa_activity_html',
        '_issue_row_html',
        '_build_burndown_svg',
        '_build_burndown_explainer_html',
        '_build_remaining_scope_breakdown_html',
        '_build_burndown_takeaways_html',
        '_build_progress_donut_svg',
        '_build_age_distribution_chart_html',
        '_build_issue_type_breakdown_panel',
        '_build_assignee_workload_panel',
        '_build_sprint_details_html',
        '_build_cycle_time_medians_panel_html',
        '_build_blocked_time_ratio_panel_html',
        '_render_activity_date_select',
        '_build_todays_bug_reports_html',
        'write_html_report'
    ]
    
    results = {}
    lines = content.splitlines()
    
    for func in functions:
        # Find start line
        start_line = -1
        for i, line in enumerate(lines):
            if line.startswith(f'def {func}'):
                start_line = i
                break
        
        if start_line != -1:
            # Find end by finding next def at same indentation (0) or end of file
            block = [lines[start_line]]
            for i in range(start_line + 1, len(lines)):
                if lines[i].startswith('def ') or (lines[i] and not lines[i].startswith(' ') and len(lines[i].strip()) > 0):
                    # We hit the next function or top-level block
                    # But we must be careful with nested defs (which start with '    def')
                    if lines[i].startswith('def '):
                         break
                block.append(lines[i])
            
            results[func] = "\n".join(block).strip()
            print(f"Extracted {func}")
        else:
            print(f"Failed to find {func}")

    # Combine into a new dashboard_ui.py content
    header = """import os, json, datetime, socket, subprocess, sys, argparse, hashlib, time
import math
from typing import List, Dict, Any
from pathlib import Path

# High-Fidelity UI Logic Restored from Main Branch
"""
    
    body = "\n\n".join(results.values())
    
    with open('dashboard_ui.py', 'w', encoding='utf-8') as f:
        f.write(header + "\n" + body)

    print("Logic extraction completed. dashboard_ui.py updated.")

if __name__ == "__main__":
    extract()
