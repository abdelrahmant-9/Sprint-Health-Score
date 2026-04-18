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
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sprint Health Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; color: #1f2937; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; margin-bottom: 14px; }}
    h1, h2 {{ margin: 0 0 10px 0; }}
    .muted {{ color: #6b7280; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px; text-align: left; }}
    th {{ background: #f3f4f6; }}
  </style>
</head>
<body>
  <h1>Sprint Health Report</h1>
  <p class="muted">Generated at: {report["generated_at"]}</p>
  <div class="card">
    <h2>{sprint.get("name")} ({sprint.get("state")})</h2>
    <p>Health Score: <strong>{scores["final_score"]}/100</strong> - {report["health_label"]}</p>
  </div>
  <div class="card">
    <h2>Signals</h2>
    <table>
      <tr><th>Signal</th><th>Score</th></tr>
      <tr><td>Commitment</td><td>{scores["commitment"]}</td></tr>
      <tr><td>Carryover</td><td>{scores["carryover"]}</td></tr>
      <tr><td>Cycle Time</td><td>{scores["cycle_time"]}</td></tr>
      <tr><td>Bug Ratio</td><td>{scores["bug_ratio"]}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>Metrics</h2>
    <table>
      <tr><th>Metric</th><th>Value</th></tr>
      <tr><td>Total Items</td><td>{metrics["total_items"]}</td></tr>
      <tr><td>Completed Items</td><td>{metrics["completed_items"]}</td></tr>
      <tr><td>Carried Over Items</td><td>{metrics["carried_over_items"]}</td></tr>
      <tr><td>New Bugs</td><td>{metrics["new_bug_count"]}</td></tr>
      <tr><td>Average Cycle Time (days)</td><td>{metrics["avg_cycle_time_days"]}</td></tr>
    </table>
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
