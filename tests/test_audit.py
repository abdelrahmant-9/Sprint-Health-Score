"""Tests for the audit logging subsystem."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.auth.service import log_audit_event, list_audit_events
from app.storage import init_schema


class TestAuditLogger:
    def _db(self, tmp_path: Path) -> Path:
        """Create a temporary SQLite DB with the auth schema."""
        db = tmp_path / "audit_test.db"
        init_schema(db)
        return db

    def test_log_and_list_events(self, tmp_path):
        db = self._db(tmp_path)
        log_audit_event(
            db, 
            event_type="LOGIN_SUCCESS", 
            user_email="admin@test.com", 
            ip_address="192.168.1.5",
            user_agent="pytest",
        )
        events = list_audit_events(db, limit=10)
        
        assert len(events) == 1
        event = events[0]
        assert event["event_type"] == "LOGIN_SUCCESS"
        assert event["user_email"] == "admin@test.com"
        assert event["ip_address"] == "192.168.1.5"
        assert event["user_agent"] == "pytest"
        assert event["details"] is None

    def test_events_returned_in_descending_order(self, tmp_path):
        db = self._db(tmp_path)
        log_audit_event(db, "EVENT_1", "u1@test.com")
        time.sleep(0.01)  # Ensure distinct timestamps
        log_audit_event(db, "EVENT_2", "u2@test.com")
        
        events = list_audit_events(db, limit=10)
        assert len(events) == 2
        assert events[0]["event_type"] == "EVENT_2"
        assert events[1]["event_type"] == "EVENT_1"

    def test_list_events_respects_limit(self, tmp_path):
        db = self._db(tmp_path)
        for i in range(5):
            log_audit_event(db, f"TYPE_{i}", "user@test.com")
            
        events = list_audit_events(db, limit=3)
        assert len(events) == 3
        # Should be the 3 most recent
        assert events[0]["event_type"] == "TYPE_4"
        assert events[1]["event_type"] == "TYPE_3"
        assert events[2]["event_type"] == "TYPE_2"

    def test_log_event_with_details(self, tmp_path):
        db = self._db(tmp_path)
        log_audit_event(db, "CONFIG_CHANGED", "admin@test.com", details="Updated threshold to 80")
        
        events = list_audit_events(db, limit=1)
        assert events[0]["details"] == "Updated threshold to 80"
