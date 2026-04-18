"""Report assembly and formatting utilities."""

from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone

from app.metrics import SprintMetrics
from app.scoring import ScoreBreakdown


logger = logging.getLogger(__name__)


def health_label(score: int) -> str:
    """Map numeric score to a human-friendly quality label."""
    if score >= 85:
        return "Predictable sprint"
    if score >= 70:
        return "Some instability"
    if score >= 50:
        return "Execution issues"
    return "Sprint breakdown"


def build_report_payload(
    sprint: dict,
    metrics: SprintMetrics,
    scores: ScoreBreakdown,
    analytics: dict | None = None,
) -> dict:
    """Build report payload that can be logged, sent, or rendered."""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sprint": {
            "id": sprint.get("id"),
            "name": sprint.get("name"),
            "state": sprint.get("state"),
            "startDate": sprint.get("startDate"),
            "endDate": sprint.get("endDate"),
        },
        "metrics": asdict(metrics),
        "scores": asdict(scores),
        "health_label": health_label(scores.final_score),
    }
    if analytics:
        payload["analytics"] = analytics
    logger.info(
        "Generated report for sprint=%s score=%s total_issues=%s",
        sprint.get("name"),
        scores.final_score,
        metrics.total_items,
    )
    return payload


def format_console_report(report: dict) -> str:
    """Convert report payload into concise console output."""
    metrics = report["metrics"]
    scores = report["scores"]
    lines = [
        f"Sprint: {report['sprint']['name']} ({report['sprint']['state']})",
        f"Health Score: {scores['final_score']}/100 - {report['health_label']}",
        (
            "Signals: "
            f"commitment={scores['commitment']}, "
            f"carryover={scores['carryover']}, "
            f"cycle_time={scores['cycle_time']}, "
            f"bug_ratio={scores['bug_ratio']}"
        ),
        (
            "Metrics: "
            f"total={metrics['total_items']}, "
            f"completed={metrics['completed_items']}, "
            f"new_bugs={metrics['new_bug_count']}, "
            f"avg_cycle_days={metrics['avg_cycle_time_days']}"
        ),
    ]
    return "\n".join(lines)


def render_html_report(report: dict) -> str:
    """Render report as reusable HTML markup."""
    metrics = report["metrics"]
    scores = report["scores"]
    sprint = report["sprint"]
    analytics = report.get("analytics") or {}
    cycle_time = analytics.get("cycle_time") or {}
    bug_metrics = analytics.get("bugs") or {}
    blocked_ratio = analytics.get("blocked_ratio")
    health_score = int(analytics.get("health_score", scores["final_score"]))

    def score_color(score: int) -> str:
        if score >= 85:
            return "#22C55E"
        if score >= 70:
            return "#FACC15"
        if score >= 50:
            return "#FB923C"
        return "#EF4444"

    def score_class(score: int) -> str:
        if score >= 85:
            return "green"
        if score >= 70:
            return "yellow"
        if score >= 50:
            return "orange"
        return "red"

    def pct_bar(score: int) -> str:
        return f"width:{max(0, min(100, int(score)))}%; background:{score_color(score)};"

    commitment_value = round((metrics["completed_scope"] / metrics["committed_scope"]) * 100, 1) if metrics.get("committed_scope") else 0.0
    carryover_value = round((metrics["carryover_scope"] / metrics["committed_scope"]) * 100, 1) if metrics.get("committed_scope") else 0.0
    bug_value = round(float(metrics.get("bug_ratio_pct", 0.0) or 0.0), 1)
    cycle_value = cycle_time.get("current_avg") if isinstance(cycle_time, dict) else metrics.get("avg_cycle_time_days")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sprint Health Report</title>
  <style>
    :root {{
      --bg-page:#0A0F1E; --bg-surface:#111827; --bg-elevated:#1A2235; --bg-overlay:#243049;
      --brand-primary:#3B82F6; --brand-soft:rgba(59,130,246,0.12); --green:#22C55E;
      --yellow:#FACC15; --orange:#FB923C; --red:#EF4444; --teal:#14B8A6;
      --text-primary:#F1F5F9; --text-secondary:#94A3B8; --text-muted:#475569;
      --border:rgba(148,163,184,0.10); --radius-lg:16px; --radius-xl:22px;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family:'Segoe UI', Tahoma, sans-serif;
      color:var(--text-primary);
      background:
        radial-gradient(circle at top left, rgba(59,130,246,0.12), transparent 26%),
        linear-gradient(180deg, var(--bg-overlay) 0%, var(--bg-page) 20%, var(--bg-page) 100%);
    }}
    .page {{ max-width:1200px; margin:0 auto; padding:32px 24px 56px; }}
    .hero, .card, .formula-result {{ background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--radius-xl); }}
    .hero {{ padding:28px 32px; margin-bottom:20px; }}
    .eyebrow {{ font-size:11px; text-transform:uppercase; letter-spacing:0.08em; font-weight:700; color:var(--text-muted); }}
    .hero-grid {{ display:grid; grid-template-columns:1.2fr 1fr; gap:28px; align-items:end; margin-top:14px; }}
    .score-display {{ font-size:72px; font-weight:900; letter-spacing:-0.04em; line-height:0.92; }}
    .score-display.red {{ color:var(--red); }}
    .score-display.yellow {{ color:var(--yellow); }}
    .score-display.orange {{ color:var(--orange); }}
    .score-display.green {{ color:var(--green); }}
    .score-display span {{ font-size:20px; color:var(--text-muted); font-weight:500; }}
    .muted {{ color:var(--text-muted); }}
    .sub {{ color:var(--text-secondary); font-size:14px; line-height:1.6; }}
    .score-track {{ position:relative; margin-top:24px; padding-top:26px; }}
    .score-bar {{ height:8px; border-radius:999px; background:linear-gradient(90deg, var(--red) 0%, var(--orange) 25%, var(--yellow) 55%, var(--green) 85%, var(--green) 100%); position:relative; }}
    .needle {{ position:absolute; top:0; left:{health_score}%; transform:translateX(-50%); display:flex; flex-direction:column; align-items:center; gap:8px; }}
    .needle-chip {{ padding:4px 10px; border-radius:999px; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.10); font-size:11px; font-weight:700; }}
    .needle-dot {{ width:14px; height:14px; border-radius:50%; background:#fff; box-shadow:0 0 0 3px rgba(255,255,255,0.12); }}
    .zones {{ margin-top:16px; display:grid; grid-template-columns:repeat(4,1fr); gap:12px; font-size:12px; }}
    .zone.active {{ font-weight:800; font-size:13px; }}
    .cards4 {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:16px; margin-bottom:20px; }}
    .card {{ padding:24px; position:relative; overflow:hidden; }}
    .accent {{ position:absolute; top:0; left:0; right:0; height:4px; }}
    .icon-circle {{ width:40px; height:40px; border-radius:10px; display:flex; align-items:center; justify-content:center; background:var(--bg-elevated); margin-bottom:16px; font-size:18px; }}
    .signal-score {{ font-size:52px; font-weight:900; letter-spacing:-0.04em; margin:6px 0 8px; }}
    .status-row {{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:14px; flex-wrap:wrap; color:var(--text-secondary); font-size:14px; }}
    .chip {{ padding:2px 8px; border-radius:8px; font-size:12px; font-weight:700; }}
    .chip.blue {{ background:rgba(37,99,235,0.18); color:#93C5FD; }}
    .chip.yellow {{ background:rgba(250,204,21,0.18); color:#FDE68A; }}
    .chip.green {{ background:rgba(34,197,94,0.18); color:#86EFAC; }}
    .chip.red {{ background:rgba(239,68,68,0.18); color:#FCA5A5; }}
    .target {{ padding-top:12px; border-top:1px solid var(--border); font-size:12px; color:var(--text-muted); }}
    .formula-grid {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:16px; margin-bottom:20px; }}
    .formula-card {{ border-radius:16px; padding:18px; border-top:2px solid transparent; border:1px solid var(--border); }}
    .mini-bar {{ height:5px; margin-top:14px; background:var(--bg-elevated); border-radius:999px; overflow:hidden; }}
    .mini-bar > span {{ display:block; height:100%; border-radius:999px; }}
    .formula-result {{ padding:28px 32px; display:flex; justify-content:space-between; align-items:center; gap:18px; margin-bottom:20px; }}
    .formula {{ font-family:'Consolas','Courier New',monospace; color:var(--text-muted); font-size:13px; }}
    .result-score {{ font-size:52px; font-weight:900; letter-spacing:-0.04em; background:linear-gradient(90deg, #3B82F6, #22C55E); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }}
    .meta-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:16px; }}
    .meta-card {{ background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--radius-lg); padding:20px 24px; }}
    .meta-label {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-muted); margin-bottom:10px; }}
    .meta-value {{ font-size:28px; font-weight:700; color:var(--text-primary); margin-bottom:8px; }}
    @media (max-width: 960px) {{
      .hero-grid, .cards4, .formula-grid, .meta-grid, .zones {{ grid-template-columns:1fr; }}
      .formula-result {{ flex-direction:column; align-items:flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <div class="eyebrow">Sprint Health Report</div>
      <div class="hero-grid">
        <div>
          <h1 style="margin:0 0 8px; font-size:28px;">{sprint.get("name")} ({sprint.get("state")})</h1>
          <p class="sub">Generated at {report["generated_at"]}</p>
          <div class="score-display {score_class(health_score)}">{health_score}<span>/100</span></div>
          <p class="sub">{report["health_label"]}</p>
        </div>
        <div>
          <p class="sub">A focused report on commitment, carryover, cycle time, and bug pressure for the current sprint.</p>
        </div>
      </div>
      <div class="score-track">
        <div class="needle">
          <div class="needle-chip">{health_score}/100</div>
          <div class="needle-dot"></div>
        </div>
        <div class="score-bar"></div>
        <div class="zones">
          <div class="zone {'active' if health_score < 50 else ''}" style="color:var(--red);">&lt;50 Sprint breakdown</div>
          <div class="zone {'active' if 50 <= health_score < 70 else ''}" style="color:var(--orange);">50-69 Execution issues</div>
          <div class="zone {'active' if 70 <= health_score < 85 else ''}" style="color:var(--yellow);">70-84 Some instability</div>
          <div class="zone {'active' if health_score >= 85 else ''}" style="color:var(--green);">85-100 Predictable sprint</div>
        </div>
      </div>
    </section>
    <section class="cards4">
      <div class="card">
        <div class="accent" style="background:var(--brand-primary);"></div>
        <div class="icon-circle" style="color:var(--brand-primary);">C</div>
        <div class="eyebrow">Commitment</div>
        <div class="signal-score" style="color:{score_color(scores['commitment'])};">{scores["commitment"]}</div>
        <div class="status-row"><span>{commitment_value}% delivered</span><span class="chip blue">{metrics["completed_items"]}/{metrics["total_items"]}</span></div>
        <div class="target">Target range: 85-95% commitment reliability</div>
      </div>
      <div class="card">
        <div class="accent" style="background:var(--yellow);"></div>
        <div class="icon-circle" style="color:var(--yellow);">R</div>
        <div class="eyebrow">Carryover</div>
        <div class="signal-score" style="color:{score_color(scores['carryover'])};">{scores["carryover"]}</div>
        <div class="status-row"><span>{carryover_value}% carried over</span><span class="chip yellow">{metrics["carried_over_items"]} items</span></div>
        <div class="target">Target range: under 10% carryover</div>
      </div>
      <div class="card">
        <div class="accent" style="background:var(--green);"></div>
        <div class="icon-circle" style="color:var(--green);">T</div>
        <div class="eyebrow">Cycle Time</div>
        <div class="signal-score" style="color:{score_color(scores['cycle_time'])};">{scores["cycle_time"]}</div>
        <div class="status-row"><span>{cycle_value if cycle_value is not None else 'N/A'} days avg</span><span class="chip green">{cycle_time.get('trend', 'stable')}</span></div>
        <div class="target">Target range: within 10% of recent cycle-time baseline</div>
      </div>
      <div class="card">
        <div class="accent" style="background:var(--red);"></div>
        <div class="icon-circle" style="color:var(--red);">B</div>
        <div class="eyebrow">Bug Ratio</div>
        <div class="signal-score" style="color:{score_color(scores['bug_ratio'])};">{scores["bug_ratio"]}</div>
        <div class="status-row"><span>{bug_value}% bug ratio</span><span class="chip red">{metrics["new_bug_count"]} new bugs</span></div>
        <div class="target">Target range: under 15% bug ratio</div>
      </div>
    </section>
    <section class="formula-grid">
      <div class="formula-card" style="background:rgba(37,99,235,0.06); border-top-color:var(--brand-primary);">
        <div class="eyebrow">Commitment</div>
        <div class="sub">{scores["commitment"]} × 0.35</div>
        <div class="mini-bar"><span style="{pct_bar(scores['commitment'])}"></span></div>
      </div>
      <div class="formula-card" style="background:rgba(234,179,8,0.06); border-top-color:var(--yellow);">
        <div class="eyebrow">Carryover</div>
        <div class="sub">{scores["carryover"]} × 0.25</div>
        <div class="mini-bar"><span style="{pct_bar(scores['carryover'])}"></span></div>
      </div>
      <div class="formula-card" style="background:rgba(34,197,94,0.06); border-top-color:var(--green);">
        <div class="eyebrow">Cycle Time</div>
        <div class="sub">{scores["cycle_time"]} × 0.20</div>
        <div class="mini-bar"><span style="{pct_bar(scores['cycle_time'])}"></span></div>
      </div>
      <div class="formula-card" style="background:rgba(239,68,68,0.06); border-top-color:var(--red);">
        <div class="eyebrow">Bug Ratio</div>
        <div class="sub">{scores["bug_ratio"]} × 0.20</div>
        <div class="mini-bar"><span style="{pct_bar(scores['bug_ratio'])}"></span></div>
      </div>
    </section>
    <section class="formula-result">
      <div>
        <div class="eyebrow">Weighted Formula</div>
        <div class="formula">(commitment × 0.35) + (carryover × 0.25) + (cycle_time × 0.20) + (bug_ratio × 0.20)</div>
      </div>
      <div style="text-align:right;">
        <div class="eyebrow">Final Health Score</div>
        <div class="result-score">{scores["final_score"]}<span style="font-size:20px; color:var(--text-muted); -webkit-text-fill-color:var(--text-muted);">/100</span></div>
      </div>
    </section>
    <section class="meta-grid">
      <div class="meta-card">
        <div class="meta-label">Average Cycle Time</div>
        <div class="meta-value">{metrics["avg_cycle_time_days"] if metrics["avg_cycle_time_days"] is not None else "N/A"}</div>
        <div class="sub">Average story cycle time in days for completed work.</div>
      </div>
      <div class="meta-card">
        <div class="meta-label">Blocked Ratio</div>
        <div class="meta-value">{blocked_ratio if blocked_ratio is not None else "N/A"}</div>
        <div class="sub">Percentage of total cycle time spent in blocked status.</div>
      </div>
      <div class="meta-card">
        <div class="meta-label">Bug Analytics</div>
        <div class="meta-value">{bug_metrics.get("top_bug_engineer") or "N/A"}</div>
        <div class="sub">Top bug engineer. Avg bugs per story: {bug_metrics.get("avg_per_story", "N/A")}.</div>
      </div>
    </section>
  </div>
</body>
</html>"""


def write_html_report(report: dict, output_path: Path) -> Path:
    """Write HTML report to disk and return path."""
    html = render_html_report(report)
    output_path.write_text(html, encoding="utf-8")
    logger.info("HTML report written to %s", output_path)
    return output_path


def write_pdf_report(report: dict, output_path: Path) -> Path:
    """Write PDF report to disk and return path."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as pdf_canvas
    except Exception as exc:
        logger.error("PDF export unavailable because reportlab is missing: %s", exc)
        raise RuntimeError("PDF export unavailable") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = pdf_canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4
    y = height - 48
    for line in format_console_report(report).splitlines():
        canvas.drawString(48, y, line)
        y -= 16
    canvas.save()
    logger.info("PDF report written to %s", output_path)
    return output_path
