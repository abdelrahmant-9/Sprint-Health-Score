"""Application entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, load_settings
from app.jira_client import JiraClient
from app.metrics import calculate_metrics
from app.notifications import send_slack_message
from app.report import (
    build_report_payload,
    format_console_report,
    write_html_report,
    write_pdf_report,
)
from app.scheduler import run_once, run_watch
from app.scoring import calculate_health_score


def configure_logging(level: str, debug: bool = False) -> None:
    """Configure process-wide logging."""
    resolved_level = logging.DEBUG if debug else getattr(logging, level, logging.INFO)
    logging.basicConfig(
        level=resolved_level,
        format='%(asctime)s %(levelname)s %(name)s - %(message)s',
    )


def _resolve_report_output_path(base_path: Path, report_format: str) -> Path:
    """Build output path by requested report format."""
    suffix = ".pdf" if report_format == "pdf" else ".html"
    return base_path.with_suffix(suffix)


def run_cycle(settings: Settings, output_json: bool = False, notify: bool = False) -> dict:
    """Run one sprint-health cycle and return report payload."""
    logger = logging.getLogger(__name__)
    logger.info("Starting sprint health report generation")

    client = JiraClient(settings=settings)
    issues, sprint = client.fetch_sprint_issues()

    sprint_start = None
    sprint_start_raw = sprint.get("startDate")
    if sprint_start_raw:
        sprint_start = datetime.fromisoformat(str(sprint_start_raw).replace("Z", "+00:00")).astimezone(timezone.utc)

    metrics = calculate_metrics(issues=issues, sprint_start=sprint_start)
    scores = calculate_health_score(metrics)
    report = build_report_payload(sprint=sprint, metrics=metrics, scores=scores)
    report_path = _resolve_report_output_path(settings.report_output_path, settings.report_format)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if settings.report_format == "pdf":
        write_pdf_report(report, report_path)
    else:
        write_html_report(report, report_path)

    if output_json:
        logger.info("JSON report payload: %s", json.dumps(report, ensure_ascii=False))
    else:
        logger.info("Report summary\n%s", format_console_report(report))
    logger.info("Report artifact generated at %s", report_path)
    if notify:
        send_slack_message(settings, format_console_report(report))
    return report


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Sprint health report")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--notify", action="store_true", help="Send Slack notification")
    parser.add_argument("--mode", choices=["once", "watch"], default=None, help="Execution mode override")
    parser.add_argument("--interval", type=int, default=None, help="Watch interval override in seconds")
    parser.add_argument("--format", choices=["html", "pdf"], default=None, help="Output format override")
    args = parser.parse_args()

    settings = load_settings()
    if args.format:
        settings = settings.model_copy(update={"report_format": args.format})
    configure_logging(settings.log_level, debug=settings.debug)
    logger = logging.getLogger(__name__)
    mode = args.mode or settings.run_mode
    interval = max(10, args.interval or settings.watch_interval_seconds)

    def task() -> dict:
        return run_cycle(settings=settings, output_json=args.json, notify=args.notify)
    if mode == "watch":
        run_watch(task, interval_seconds=interval)
    else:
        run_once(task)
    logger.info("Execution finished for mode=%s", mode)


if __name__ == "__main__":
    main()
