"""FastAPI service exposing sprint health endpoints."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.logging_config import setup_logging
from app.service import calculate_health_snapshot, get_daily_activity, get_weekly_activity, render_health_report_html
from app.storage import close_all_connections, init_schema, list_recent_results, save_sprint_result
from app.notifications import send_slack_message


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize logging and database on startup."""
    settings = load_settings()
    setup_logging(settings.log_level)
    init_schema(settings.sqlite_path)
    app.state.active_requests = 0
    app.state.run_lock = threading.Lock()
    app.state.last_run_at = 0.0
    logger.info("API startup complete sqlite_path=%s debug=%s", settings.sqlite_path, settings.debug)
    yield
    logger.info("API stopping cleanly active_requests=%s", getattr(app.state, "active_requests", 0))
    close_all_connections()
    logger.info("API shutdown complete")


app = FastAPI(title="Sprint Health Service", version="1.0.0", lifespan=lifespan)


def _require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-KEY")) -> None:
    """Validate API key for protected endpoints."""
    settings = load_settings()
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _enforce_run_rate_limit(request: Request) -> None:
    """Limit POST /run to one request per 10 seconds per API process."""
    now = time.monotonic()
    run_lock = request.app.state.run_lock
    with run_lock:
        last_run_at = float(getattr(request.app.state, "last_run_at", 0.0))
        if now - last_run_at < 10.0:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Run endpoint is rate limited")
        request.app.state.last_run_at = now


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log each HTTP request with method, path, status, and duration."""
    start = time.perf_counter()
    request.app.state.active_requests = getattr(request.app.state, "active_requests", 0) + 1
    try:
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s -> %s %.2fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response
    finally:
        request.app.state.active_requests = max(0, getattr(request.app.state, "active_requests", 1) - 1)


@app.get("/health")
def health_check() -> dict:
    """Liveness/readiness probe for orchestrators."""
    return {"status": "ok"}


@app.get("/health-score")
def get_health_score() -> dict:
    """Return score, completion rate, and score breakdown."""
    try:
        snapshot = calculate_health_snapshot(load_settings())
        return {
            "score": snapshot["score"],
            "completion_rate": snapshot["completion_rate"],
            "breakdown": snapshot["breakdown"],
        }
    except Exception as exc:
        logger.exception("Failed to compute health score")
        raise HTTPException(status_code=500, detail=f"Failed to compute health score: {exc}") from exc


@app.get("/report", response_class=HTMLResponse)
def get_report() -> HTMLResponse:
    """Return rendered HTML report."""
    try:
        html = render_health_report_html(load_settings())
        return HTMLResponse(content=html, status_code=200)
    except Exception as exc:
        logger.exception("Failed to render report")
        raise HTTPException(status_code=500, detail=f"Failed to render report: {exc}") from exc


@app.get("/activity")
def activity(_: None = Depends(_require_api_key)) -> dict:
    """Return today's developer/tester activity summary."""
    try:
        return get_daily_activity(load_settings())
    except Exception as exc:
        logger.exception("Failed to compute daily activity")
        raise HTTPException(status_code=500, detail=f"Failed to compute daily activity: {exc}") from exc


@app.get("/activity/weekly")
def weekly_activity(_: None = Depends(_require_api_key)) -> dict:
    """Return the current Sunday-Thursday work-week activity summary."""
    try:
        return get_weekly_activity(load_settings())
    except Exception as exc:
        logger.exception("Failed to compute weekly activity")
        raise HTTPException(status_code=500, detail=f"Failed to compute weekly activity: {exc}") from exc


def _build_run_notification(score: int, daily_activity: dict) -> str:
    """Build Slack summary for completed run."""
    top_developer = daily_activity.get("top_developer") or {}
    performer = str(top_developer.get("name") or "No top performer yet")
    performer_completed = int(top_developer.get("completed", 0) or 0)
    headline = f"Sprint health run completed. Score: {score}/100."
    activity_summary = (
        f"Bugs today: {int(daily_activity.get('bugs_today', 0) or 0)}. "
        f"Top performer: {performer} ({performer_completed} tasks)."
    )
    insights = daily_activity.get("insights") or []
    insight_block = "\n".join(f"- {item}" for item in insights[:3])
    return f"{headline}\n{activity_summary}\nKey insights:\n{insight_block}"


def _build_risk_alerts(daily_activity: dict) -> list[str]:
    """Return Slack alert lines for risky daily conditions."""
    alerts: list[str] = []
    insights = [str(item) for item in (daily_activity.get("insights") or [])]
    for insight in insights:
        normalized = insight.lower()
        if "high bug creation" in normalized:
            alerts.append(f"Warning: {insight}")
        elif "no tester verification activity" in normalized:
            alerts.append("Warning: Low testing activity detected.")
        elif "low completed task volume" in normalized:
            alerts.append("Warning: Low team activity detected.")
    return alerts


@app.post("/run")
def run_health_calculation(request: Request, _: None = Depends(_require_api_key)) -> dict:
    """Trigger sprint health calculation, persist result, return summary."""
    try:
        _enforce_run_rate_limit(request)
        settings = load_settings()
        snapshot = calculate_health_snapshot(settings)
        daily_activity_payload = get_daily_activity(settings)
        save_sprint_result(settings.sqlite_path, snapshot)
        send_slack_message(settings, _build_run_notification(snapshot["score"], daily_activity_payload))
        risk_alerts = _build_risk_alerts(daily_activity_payload)
        if risk_alerts:
            send_slack_message(settings, "\n".join(risk_alerts))
        return {
            "status": "ok",
            "score": snapshot["score"],
            "completion_rate": snapshot["completion_rate"],
            "breakdown": snapshot["breakdown"],
        }
    except Exception as exc:
        logger.exception("Failed to run health calculation")
        raise HTTPException(status_code=500, detail=f"Failed to run health calculation: {exc}") from exc


@app.get("/results")
def get_results(limit: int = Query(default=20, ge=1, le=200)) -> dict:
    """Return recent stored sprint results from SQLite."""
    try:
        settings = load_settings()
        rows = list_recent_results(settings.sqlite_path, limit=limit)
        return {"count": len(rows), "results": rows}
    except Exception as exc:
        logger.exception("Failed to list results")
        raise HTTPException(status_code=500, detail=f"Failed to list results: {exc}") from exc
