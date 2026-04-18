"""Authentication service — user management, login, lockout, and token lifecycle.

All user data lives in the SQLite database managed by ``app.storage``.
This module provides the business-logic layer that the API routes and admin
dashboard delegate to for any authentication or user-management action.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.auth.jwt_handler import create_access_token, create_refresh_token, decode_token
from app.auth.password import hash_password, verify_password

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal DB helpers
# ---------------------------------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a short-lived SQLite connection with row-factory enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def create_user(
    db_path: Path,
    *,
    email: str,
    password: str,
    role: str = "user",
) -> dict | None:
    """Create a new user and return the user dict, or ``None`` if the email exists."""
    email = email.strip().lower()
    if not email or not password:
        return None
    hashed = hash_password(password)
    conn = _connect(db_path)
    try:
        with conn:
            try:
                conn.execute(
                    """
                    INSERT INTO users (email, password_hash, role, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (email, hashed, role, _utcnow()),
                )
            except sqlite3.IntegrityError:
                return None
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_email(db_path: Path, email: str) -> dict | None:
    """Return user dict or ``None``."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(db_path: Path, user_id: int) -> dict | None:
    """Return user dict or ``None``."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users(db_path: Path) -> list[dict]:
    """Return all users (without password hashes)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, email, role, created_at, last_login_at, failed_attempts, locked_until FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_user(db_path: Path, email: str) -> bool:
    """Delete a user by email.  Returns ``True`` if a row was removed."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM users WHERE email = ?", (email.strip().lower(),)
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def update_user_role(db_path: Path, email: str, role: str) -> bool:
    """Change a user's role."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE users SET role = ? WHERE email = ?",
                (role, email.strip().lower()),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Authentication & lockout
# ---------------------------------------------------------------------------

def _is_locked(user: dict) -> bool:
    """Return ``True`` when the user account is currently locked."""
    locked_until = user.get("locked_until")
    if not locked_until:
        return False
    try:
        return datetime.fromisoformat(locked_until) > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def _record_failed_attempt(db_path: Path, email: str) -> None:
    """Increment failed attempts and lock if threshold exceeded."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE users SET failed_attempts = failed_attempts + 1 WHERE email = ?",
                (email,),
            )
            row = conn.execute(
                "SELECT failed_attempts FROM users WHERE email = ?", (email,)
            ).fetchone()
            if row and int(row["failed_attempts"]) >= MAX_FAILED_ATTEMPTS:
                lock_until = (
                    datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
                ).isoformat()
                conn.execute(
                    "UPDATE users SET locked_until = ? WHERE email = ?",
                    (lock_until, email),
                )
                logger.warning("Account locked for %s until %s", email, lock_until)
    finally:
        conn.close()


def _clear_failed_attempts(db_path: Path, email: str) -> None:
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE email = ?",
                (_utcnow(), email),
            )
    finally:
        conn.close()


def authenticate(
    db_path: Path,
    email: str,
    password: str,
) -> dict | None:
    """Verify credentials.  Returns user dict on success, ``None`` on failure.

    Also enforces the brute-force lockout policy.
    """
    email = email.strip().lower()
    user = get_user_by_email(db_path, email)
    if not user:
        logger.info("Login attempt for non-existent email=%s", email)
        return None

    if _is_locked(user):
        logger.warning("Login attempt for locked account email=%s", email)
        return None

    if not verify_password(password, user["password_hash"]):
        _record_failed_attempt(db_path, email)
        logger.info("Invalid password for email=%s", email)
        return None

    _clear_failed_attempts(db_path, email)
    logger.info("Successful authentication for email=%s", email)
    return user


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------

def issue_tokens(
    user: dict,
    *,
    secret_key: str,
    access_expire_minutes: int = 15,
    refresh_expire_days: int = 7,
) -> dict:
    """Issue a fresh access/refresh token pair for *user*."""
    access = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        secret_key=secret_key,
        expire_minutes=access_expire_minutes,
    )
    refresh = create_refresh_token(
        user_id=user["id"],
        secret_key=secret_key,
        expire_days=refresh_expire_days,
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


def refresh_access_token(
    db_path: Path,
    refresh_token: str,
    *,
    secret_key: str,
    access_expire_minutes: int = 15,
    refresh_expire_days: int = 7,
) -> dict:
    """Validate a refresh token and return a new token pair.

    Raises ``ValueError`` on invalid/expired/blacklisted tokens.
    """
    payload = decode_token(refresh_token, secret_key=secret_key)
    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")

    user_id = int(payload["sub"])

    # Check blacklist
    if is_token_blacklisted(db_path, refresh_token):
        raise ValueError("Token has been revoked")

    user = get_user_by_id(db_path, user_id)
    if not user:
        raise ValueError("User no longer exists")

    # Blacklist the old refresh token (rotation)
    blacklist_token(db_path, refresh_token)

    return issue_tokens(
        user,
        secret_key=secret_key,
        access_expire_minutes=access_expire_minutes,
        refresh_expire_days=refresh_expire_days,
    )


# ---------------------------------------------------------------------------
# Token blacklist
# ---------------------------------------------------------------------------

def blacklist_token(db_path: Path, token: str) -> None:
    """Add a token to the blacklist so it cannot be reused."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO token_blacklist (token, blacklisted_at) VALUES (?, ?)",
                (token, _utcnow()),
            )
    finally:
        conn.close()


def is_token_blacklisted(db_path: Path, token: str) -> bool:
    """Return ``True`` when *token* has been revoked."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM token_blacklist WHERE token = ?", (token,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def cleanup_expired_blacklist(db_path: Path, max_age_days: int = 14) -> int:
    """Remove blacklist entries older than *max_age_days*.  Returns count removed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM token_blacklist WHERE blacklisted_at < ?", (cutoff,)
            )
            return cursor.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def log_audit_event(
    db_path: Path,
    *,
    event_type: str,
    user_email: str = "",
    ip_address: str = "",
    user_agent: str = "",
    details: str = "",
) -> None:
    """Write an audit log entry to the database."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO audit_log (timestamp, event_type, user_email, ip_address, user_agent, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (_utcnow(), event_type, user_email, ip_address, user_agent, details),
            )
    finally:
        conn.close()


def list_audit_events(db_path: Path, limit: int = 100) -> list[dict]:
    """Return the most recent audit log entries."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 500),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
