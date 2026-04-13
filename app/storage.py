"""SQLite persistence for sprint health calculation results."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open SQLite connection with row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(db_path: Path) -> None:
    """Create sprint_results table if it does not exist."""
    with _connect(db_path) as conn:
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
        conn.commit()
    logger.debug("SQLite schema ensured at %s", db_path)


def save_sprint_result(db_path: Path, snapshot: dict[str, Any]) -> int:
    """Persist a sprint health snapshot and return row id."""
    from datetime import datetime, timezone

    report = snapshot.get("report") or {}
    sprint = report.get("sprint") or {}
    breakdown = snapshot.get("breakdown") or {}
    init_schema(db_path)
    created = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
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
        conn.commit()
        row_id = int(cur.lastrowid)
    logger.info("Stored sprint result id=%s sprint=%s score=%s", row_id, sprint.get("name"), snapshot["score"])
    return row_id


def list_recent_results(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Return most recent stored sprint results (newest first)."""
    init_schema(db_path)
    limit = max(1, min(500, limit))
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, sprint_id, sprint_name, score, completion_rate, breakdown_json
            FROM sprint_results
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
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
