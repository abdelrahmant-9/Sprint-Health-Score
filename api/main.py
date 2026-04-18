"""FastAPI service exposing sprint health endpoints with JWT authentication."""

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.auth.dependencies import get_current_user, require_role
from app.auth.schemas import (
    CreateUserRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    TokenResponse,
)
from app.auth.service import (
    authenticate,
    blacklist_token,
    create_user,
    delete_user,
    issue_tokens,
    list_users,
    log_audit_event,
    refresh_access_token,
)
from app.config import load_settings
from app.logging_config import setup_logging
from app.service import (
    calculate_health_snapshot,
    get_daily_activity,
    get_weekly_activity,
    render_health_report_html,
)
from app.storage import close_all_connections, init_schema, list_recent_results, save_sprint_result
from app.notifications import send_slack_message


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

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


app = FastAPI(title="Sprint Health Service", version="2.0.0", lifespan=lifespan)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

_settings_for_cors = load_settings()
_origins = [o.strip() for o in _settings_for_cors.cors_allowed_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

MAX_PAYLOAD_SIZE = 2 * 1024 * 1024  # 2MB

@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    """Reject payloads larger than MAX_PAYLOAD_SIZE."""
    if request.method in ["POST", "PUT", "PATCH"]:
        if "content-length" in request.headers:
            try:
                length = int(request.headers["content-length"])
                if length > MAX_PAYLOAD_SIZE:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": "Payload too large"}
                    )
            except ValueError:
                pass
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Inject security headers on every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Rate limiting helper
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return str(request.client.host) if request.client else "unknown"


def _safe_error_detail(exc: Exception, debug: bool = False) -> str:
    """Return error detail — full message in debug mode, generic otherwise."""
    if debug:
        return str(exc)
    return "An internal error occurred. Check server logs for details."


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check() -> dict:
    """Liveness/readiness probe for orchestrators."""
    return {"status": "ok"}


@app.get("/metrics")
def get_metrics() -> Response:
    """Provide application metrics to Prometheus."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, login_data: LoginRequest = Body(...)) -> dict:
    """Authenticate with email/password and receive JWT tokens."""
    settings = load_settings()
    user = authenticate(
        settings.sqlite_path, 
        login_data.email, 
        login_data.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", "")
    )
    if not user:
        log_audit_event(
            settings.sqlite_path,
            event_type="LOGIN_FAILED",
            user_email=login_data.email,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    tokens = issue_tokens(
        user,
        secret_key=settings.secret_key,
        access_expire_minutes=settings.access_token_expire_minutes,
        refresh_expire_days=settings.refresh_token_expire_days,
    )
    log_audit_event(
        settings.sqlite_path,
        event_type="LOGIN_SUCCESS",
        user_email=login_data.email,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return tokens


@app.post("/auth/refresh", response_model=TokenResponse)
@limiter.limit("5/minute")
def refresh(body: RefreshRequest, request: Request) -> dict:
    """Exchange a valid refresh token for a new token pair."""
    settings = load_settings()
    try:
        tokens = refresh_access_token(
            settings.sqlite_path,
            body.refresh_token,
            secret_key=settings.secret_key,
            access_expire_minutes=settings.access_token_expire_minutes,
            refresh_expire_days=settings.refresh_token_expire_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return tokens


@app.post("/auth/logout", response_model=MessageResponse)
def logout(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    """Blacklist the current access token."""
    settings = load_settings()
    auth_header = request.headers.get("authorization", "")
    _, _, token = auth_header.partition(" ")
    if token:
        blacklist_token(settings.sqlite_path, token)
    log_audit_event(
        settings.sqlite_path,
        event_type="LOGOUT",
        user_email=user.get("email", ""),
        ip_address=_client_ip(request),
    )
    return {"message": "Logged out successfully"}


# ---------------------------------------------------------------------------
# User management endpoints (admin only)
# ---------------------------------------------------------------------------

@app.get("/auth/users")
def get_users(user: dict = Depends(require_role("admin"))) -> dict:
    """List all users (admin only)."""
    settings = load_settings()
    users = list_users(settings.sqlite_path)
    return {"users": users}


@app.post("/auth/users", status_code=status.HTTP_201_CREATED)
def add_user(
    body: CreateUserRequest,
    request: Request,
    user: dict = Depends(require_role("admin")),
) -> dict:
    """Create a new user account (admin only)."""
    settings = load_settings()
    result = create_user(
        settings.sqlite_path,
        email=body.email,
        password=body.password,
        role=body.role,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")
    log_audit_event(
        settings.sqlite_path,
        event_type="USER_CREATED",
        user_email=user.get("email", ""),
        ip_address=_client_ip(request),
        details=f"Created user {body.email} with role {body.role}",
    )
    return {"message": f"User {body.email} created", "user_id": result["id"]}


@app.delete("/auth/users/{email}")
def remove_user(
    email: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
) -> dict:
    """Delete a user account (admin only)."""
    settings = load_settings()
    if not delete_user(settings.sqlite_path, email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    log_audit_event(
        settings.sqlite_path,
        event_type="USER_DELETED",
        user_email=user.get("email", ""),
        ip_address=_client_ip(request),
        details=f"Deleted user {email}",
    )
    return {"message": f"User {email} deleted"}


# ---------------------------------------------------------------------------
# Protected sprint health endpoints
# ---------------------------------------------------------------------------

@app.get("/health-score")
def get_health_score(user: dict = Depends(get_current_user)) -> dict:
    """Return score, completion rate, and score breakdown."""
    settings = load_settings()
    try:
        snapshot = calculate_health_snapshot(settings)
        return {
            "score": snapshot["score"],
            "completion_rate": snapshot["completion_rate"],
            "breakdown": snapshot["breakdown"],
        }
    except Exception as exc:
        logger.exception("Failed to compute health score")
        raise HTTPException(status_code=500, detail=_safe_error_detail(exc, settings.debug)) from exc


@app.get("/report", response_class=HTMLResponse)
def get_report(user: dict = Depends(get_current_user)) -> HTMLResponse:
    """Return rendered HTML report."""
    settings = load_settings()
    try:
        html = render_health_report_html(settings)
        return HTMLResponse(content=html, status_code=200)
    except Exception as exc:
        logger.exception("Failed to render report")
        raise HTTPException(status_code=500, detail=_safe_error_detail(exc, settings.debug)) from exc


@app.get("/activity")
def activity(user: dict = Depends(get_current_user)) -> dict:
    """Return today's developer/tester activity summary."""
    settings = load_settings()
    try:
        return get_daily_activity(settings)
    except Exception as exc:
        logger.exception("Failed to compute daily activity")
        raise HTTPException(status_code=500, detail=_safe_error_detail(exc, settings.debug)) from exc


@app.get("/activity/weekly")
def weekly_activity(user: dict = Depends(get_current_user)) -> dict:
    """Return the current Sunday-Thursday work-week activity summary."""
    settings = load_settings()
    try:
        return get_weekly_activity(settings)
    except Exception as exc:
        logger.exception("Failed to compute weekly activity")
        raise HTTPException(status_code=500, detail=_safe_error_detail(exc, settings.debug)) from exc


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
@limiter.limit("1/minute")
def run_health_calculation(
    request: Request,
    user: dict = Depends(require_role("admin", "editor")),
) -> dict:
    """Trigger sprint health calculation, persist result, return summary."""
    settings = load_settings()
    try:
        snapshot = calculate_health_snapshot(settings)
        daily_activity_payload = get_daily_activity(settings)
        save_sprint_result(settings.sqlite_path, snapshot)
        send_slack_message(settings, _build_run_notification(snapshot["score"], daily_activity_payload))
        risk_alerts = _build_risk_alerts(daily_activity_payload)
        if risk_alerts:
            send_slack_message(settings, "\n".join(risk_alerts))
        log_audit_event(
            settings.sqlite_path,
            event_type="HEALTH_RUN_TRIGGERED",
            user_email=user.get("email", ""),
            ip_address=_client_ip(request),
            details=f"Score: {snapshot['score']}",
        )
        return {
            "status": "ok",
            "score": snapshot["score"],
            "completion_rate": snapshot["completion_rate"],
            "breakdown": snapshot["breakdown"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to run health calculation")
        raise HTTPException(status_code=500, detail=_safe_error_detail(exc, settings.debug)) from exc


@app.get("/results")
def get_results(
    limit: int = Query(default=20, ge=1, le=200),
    user: dict = Depends(get_current_user),
) -> dict:
    """Return recent stored sprint results from SQLite."""
    settings = load_settings()
    try:
        rows = list_recent_results(settings.sqlite_path, limit=limit)
        return {"count": len(rows), "results": rows}
    except Exception as exc:
        logger.exception("Failed to list results")
        raise HTTPException(status_code=500, detail=_safe_error_detail(exc, settings.debug)) from exc
