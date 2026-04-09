import codecs, os

def finalize():
    # Read the original source with the correct encoding
    try:
        with codecs.open('main_sprint_health_2.py', 'r', 'utf-16le') as f:
            lines = f.read().splitlines()
    except Exception as e:
        print(f"Error reading source: {e}")
        return

    header = [
        "import os, json, datetime, socket, subprocess, sys, argparse, hashlib, time",
        "import math",
        "from typing import List, Dict, Any",
        "from pathlib import Path",
        "",
        "# High-Fidelity UI Logic Restored from Main Branch",
        ""
    ]

    # Block 1: Basic Helpers
    # _format_decimal is at 266 (index 265:267)
    # format_duration_hours is at 1072 (index 1071:1088)
    extra_helpers = lines[265:267] + [""] + lines[1071:1088] + [""]
    
    # Block 2: Slack Helpers 
    # format_slack_message is at 3590 (index 3589:3714)
    slack_helpers = lines[3589:3714] + [""]

    # Block 3: UI Helpers (The bulk)
    # From line 1090 to 5085
    ui_helpers = lines[1089:5085]

    final_content = header + extra_helpers + slack_helpers + ui_helpers
    
    with codecs.open('dashboard_ui.py', 'w', 'utf-8') as f:
        f.write('\n'.join(final_content))

    print("dashboard_ui.py finalized with correct encoding and all helpers.")

if __name__ == "__main__":
    finalize()
