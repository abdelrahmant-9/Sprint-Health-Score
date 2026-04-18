"""SQLite persistence for sprint health calculation results."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_OPEN_CONNECTIONS: set[sqlite3.Connection] = set()
_CONNECTIONS_LOCK = threading.Lock()


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open SQLite connection with row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    with _CONNECTIONS_LOCK:
        _OPEN_CONNECTIONS.add(conn)
    return conn


def _discard_connection(conn: sqlite3.Connection) -> None:
    """Remove a connection from the open-connection registry."""
    with _CONNECTIONS_LOCK:
        _OPEN_CONNECTIONS.discard(conn)


def close_all_connections() -> None:
    """Close any tracked SQLite connections that remain open."""
    with _CONNECTIONS_LOCK:
        connections = list(_OPEN_CONNECTIONS)
        _OPEN_CONNECTIONS.clear()
    for conn in connections:
        try:
            conn.close()
        except sqlite3.Error as exc:
            logger.warning("Failed to close SQLite connection cleanly: %s", exc)
    logger.info("Closed %s tracked SQLite connection(s)", len(connections))


def init_schema(db_path: Path) -> None:
    """Create all application tables if they do not exist."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sprint_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    sprint_id INTEGER,
                    sprint_name TEXT,
                    score INTEGER NOT NULL,
                    completion_rate REAL NOT NULL,
                    breakdown_json TEXT NOT NULL,
                    report_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL,
                    last_login_at TEXT,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    user_email TEXT NOT NULL DEFAULT '',
                    ip_address TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    blacklisted_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics_override (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT NOT NULL UNIQUE,
                    value REAL,
                    updated_at TEXT NOT NULL
                )
                """
            )
    finally:
        _discard_connection(conn)
        conn.close()
    logger.debug("SQLite schema ensured at %s", db_path)


def save_sprint_result(db_path: Path, snapshot: dict[str, Any]) -> int:
    """Persist a sprint health snapshot and return row id."""
    from datetime import datetime, timezone

    report = snapshot.get("report") or {}
    sprint = report.get("sprint") or {}
    breakdown = snapshot.get("breakdown") or {}
    init_schema(db_path)
    created = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO sprint_results (
                    created_at, sprint_id, sprint_name, score, completion_rate,
                    breakdown_json, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created,
                    sprint.get("id"),
                    sprint.get("name"),
                    int(snapshot["score"]),
                    float(snapshot["completion_rate"]),
                    json.dumps(breakdown, ensure_ascii=False),
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            row_id = int(cur.lastrowid)
    finally:
        _discard_connection(conn)
        conn.close()
    logger.info("Stored sprint result id=%s sprint=%s score=%s", row_id, sprint.get("name"), snapshot["score"])
    return row_id


def list_recent_results(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Return most recent stored sprint results (newest first)."""
    init_schema(db_path)
    limit = max(1, min(500, limit))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, sprint_id, sprint_name, score, completion_rate, breakdown_json
            FROM sprint_results
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        _discard_connection(conn)
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "sprint_id": row["sprint_id"],
                "sprint_name": row["sprint_name"],
                "score": row["score"],
                "completion_rate": row["completion_rate"],
                "breakdown": json.loads(row["breakdown_json"]),
            }
        )
    return out


def list_recent_reports(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent stored sprint results including parsed report payloads."""
    init_schema(db_path)
    limit = max(1, min(500, limit))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, sprint_id, sprint_name, score, completion_rate, breakdown_json, report_json
            FROM sprint_results
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        _discard_connection(conn)
        conn.close()

    payloads: list[dict[str, Any]] = []
    for row in rows:
        payloads.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "sprint_id": row["sprint_id"],
                "sprint_name": row["sprint_name"],
                "score": row["score"],
                "completion_rate": row["completion_rate"],
                "breakdown": json.loads(row["breakdown_json"]),
                "report": json.loads(row["report_json"]),
            }
        )
    return payloads
