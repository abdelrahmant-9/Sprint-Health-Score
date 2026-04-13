"""FastAPI service exposing sprint health endpoints."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.logging_config import setup_logging
from app.service import calculate_health_snapshot, render_health_report_html
from app.storage import init_schema, list_recent_results, save_sprint_result


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize logging and database on startup."""
    settings = load_settings()
    setup_logging(settings.log_level)
    init_schema(settings.sqlite_path)
    logger.info("API startup complete sqlite_path=%s debug=%s", settings.sqlite_path, settings.debug)
    yield
    logger.info("API shutdown")


app = FastAPI(title="Sprint Health Service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log each HTTP request with method, path, status, and duration."""
    start = time.perf_counter()
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


@app.post("/run")
def run_health_calculation() -> dict:
    """Trigger sprint health calculation, persist result, return summary."""
    try:
        settings = load_settings()
        snapshot = calculate_health_snapshot(settings)
        save_sprint_result(settings.sqlite_path, snapshot)
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
