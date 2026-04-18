"""Tests for the authentication system — password hashing, JWT, service layer."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ["DEBUG"] = "false"
os.environ["JIRA_EMAIL"] = "user@example.com"
os.environ["JIRA_API_TOKEN"] = "token"
os.environ["API_KEY"] = "test-api-key"
os.environ["SECRET_KEY"] = "test-secret-key-at-least-16-chars"

from app.auth.password import hash_password, verify_password
from app.auth.jwt_handler import create_access_token, create_refresh_token, decode_token
from app.metrics import (
    SprintMetrics,
    apply_metric_overrides,
    get_metric,
    get_override_from_db,
    list_metric_rows,
    set_override_in_db,
)
from app.auth.service import (
    authenticate,
    blacklist_token,
    cleanup_expired_blacklist,
    create_user,
    delete_user,
    get_user_by_email,
    is_token_blacklisted,
    issue_tokens,
    lock_user,
    list_users,
    log_audit_event,
    list_audit_events,
    refresh_access_token,
    unlock_user,
    update_user_role,
)
from app.storage import init_schema


SECRET = "test-secret-key-at-least-16-chars"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPassword:
    def test_hash_and_verify(self):
        plain = "my$ecureP@ss"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # different salts

    def test_wrong_password_rejected(self):
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_malformed_hash_rejected(self):
        assert not verify_password("anything", "not-a-bcrypt-hash")

    def test_empty_hash_rejected(self):
        assert not verify_password("anything", "")


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


class TestJWT:
    def test_access_token_roundtrip(self):
        token = create_access_token(
            user_id=42, email="user@test.com", role="admin",
            secret_key=SECRET, expire_minutes=5,
        )
        payload = decode_token(token, secret_key=SECRET)
        assert payload["sub"] == "42"
        assert payload["email"] == "user@test.com"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_refresh_token_roundtrip(self):
        token = create_refresh_token(user_id=7, secret_key=SECRET, expire_days=1)
        payload = decode_token(token, secret_key=SECRET)
        assert payload["sub"] == "7"
        assert payload["type"] == "refresh"

    def test_expired_token_rejected(self):
        token = create_access_token(
            user_id=1, email="x@x.com", role="user",
            secret_key=SECRET, expire_minutes=0,
        )
        # Token with 0 minutes expires immediately
        with pytest.raises(ValueError, match="expired"):
            decode_token(token, secret_key=SECRET)

    def test_wrong_secret_rejected(self):
        token = create_access_token(
            user_id=1, email="x@x.com", role="user",
            secret_key=SECRET, expire_minutes=5,
        )
        with pytest.raises(ValueError, match="Invalid token"):
            decode_token(token, secret_key="completely-different-secret")

    def test_garbage_token_rejected(self):
        with pytest.raises(ValueError, match="Invalid token"):
            decode_token("not.a.jwt", secret_key=SECRET)


# ---------------------------------------------------------------------------
# Auth service — user CRUD
# ---------------------------------------------------------------------------


class TestUserCRUD:
    def _db(self, tmp_path: Path) -> Path:
        db = tmp_path / "test.db"
        init_schema(db)
        return db

    def test_create_and_retrieve(self, tmp_path):
        db = self._db(tmp_path)
        user = create_user(db, email="New@Test.Com", password="pass123", role="admin")
        assert user is not None
        assert user["email"] == "new@test.com"  # normalized
        assert user["role"] == "admin"

        fetched = get_user_by_email(db, "NEW@test.com")
        assert fetched is not None
        assert fetched["id"] == user["id"]

    def test_duplicate_email_rejected(self, tmp_path):
        db = self._db(tmp_path)
        create_user(db, email="dup@test.com", password="pass1")
        result = create_user(db, email="dup@test.com", password="pass2")
        assert result is None

    def test_list_users(self, tmp_path):
        db = self._db(tmp_path)
        create_user(db, email="a@test.com", password="pass1")
        create_user(db, email="b@test.com", password="pass2", role="admin")
        users = list_users(db)
        assert len(users) == 2
        # Should not contain password hashes
        assert "password_hash" not in users[0]

    def test_delete_user(self, tmp_path):
        db = self._db(tmp_path)
        create_user(db, email="del@test.com", password="pass1")
        assert delete_user(db, "del@test.com")
        assert get_user_by_email(db, "del@test.com") is None

    def test_delete_nonexistent_returns_false(self, tmp_path):
        db = self._db(tmp_path)
        assert not delete_user(db, "nobody@test.com")

    def test_update_user_role_by_id(self, tmp_path):
        db = self._db(tmp_path)
        user = create_user(db, email="role@test.com", password="pass123", role="user")
        updated = update_user_role(db, user["id"], "admin")
        assert updated is not None
        assert updated["role"] == "admin"
        fetched = get_user_by_email(db, "role@test.com")
        assert fetched is not None
        assert fetched["role"] == "admin"

    def test_lock_and_unlock_user_by_id(self, tmp_path):
        db = self._db(tmp_path)
        user = create_user(db, email="lockable@test.com", password="correct", role="user")
        locked = lock_user(db, user["id"])
        assert locked is not None
        assert locked["locked_until"] is not None
        assert authenticate(db, "lockable@test.com", "correct") is None

        unlocked = unlock_user(db, user["id"])
        assert unlocked is not None
        assert unlocked["locked_until"] is None
        assert unlocked["failed_attempts"] == 0
        assert authenticate(db, "lockable@test.com", "correct") is not None

    def test_delete_user_by_id(self, tmp_path):
        db = self._db(tmp_path)
        user = create_user(db, email="remove-by-id@test.com", password="pass1")
        assert delete_user(db, user["id"])
        assert get_user_by_email(db, "remove-by-id@test.com") is None


class TestMetricOverrides:
    def test_metric_override_persists_and_applies(self, tmp_path):
        db = tmp_path / "metrics.db"
        init_schema(db)
        base_metrics = SprintMetrics(
            total_items=10,
            completed_items=8,
            carried_over_items=2,
            committed_scope=20.0,
            completed_scope=16.0,
            carryover_scope=4.0,
            bug_count=3,
            new_bug_count=1,
            bug_ratio_pct=5.0,
            avg_cycle_time_days=2.5,
        )

        set_override_in_db(db, "completed_scope", 18.5)
        set_override_in_db(db, "total_items", 12)

        assert get_override_from_db(db, "completed_scope") == 18.5
        assert get_metric("completed_scope", base_metrics, db) == 18.5
        assert get_metric("total_items", base_metrics, db) == 12

        effective_metrics = apply_metric_overrides(base_metrics, db)
        assert effective_metrics.completed_scope == 18.5
        assert effective_metrics.total_items == 12

        rows = list_metric_rows(base_metrics, db)
        rows_by_name = {row["metric_name"]: row for row in rows}
        assert rows_by_name["completed_scope"]["value"] == 18.5
        assert rows_by_name["completed_scope"]["base_value"] == 16.0
        assert rows_by_name["completed_scope"]["override_value"] == 18.5


# ---------------------------------------------------------------------------
# Auth service — authentication & lockout
# ---------------------------------------------------------------------------


class TestAuthentication:
    def _db(self, tmp_path: Path) -> Path:
        db = tmp_path / "auth.db"
        init_schema(db)
        return db

    def test_valid_credentials(self, tmp_path):
        db = self._db(tmp_path)
        create_user(db, email="user@test.com", password="correct")
        result = authenticate(db, "user@test.com", "correct")
        assert result is not None
        assert result["email"] == "user@test.com"

    def test_wrong_password(self, tmp_path):
        db = self._db(tmp_path)
        create_user(db, email="user@test.com", password="correct")
        result = authenticate(db, "user@test.com", "wrong")
        assert result is None

    def test_nonexistent_user(self, tmp_path):
        db = self._db(tmp_path)
        result = authenticate(db, "nobody@test.com", "any")
        assert result is None

    def test_lockout_after_max_failures(self, tmp_path):
        db = self._db(tmp_path)
        create_user(db, email="lock@test.com", password="correct")
        # 5 failed attempts should trigger lockout
        for _ in range(5):
            authenticate(db, "lock@test.com", "wrong")
        # Even correct password should fail while locked
        result = authenticate(db, "lock@test.com", "correct")
        assert result is None

    def test_successful_login_clears_failures(self, tmp_path):
        db = self._db(tmp_path)
        create_user(db, email="retry@test.com", password="correct")
        # 3 failures (below threshold)
        for _ in range(3):
            authenticate(db, "retry@test.com", "wrong")
        # Successful login should clear counter
        result = authenticate(db, "retry@test.com", "correct")
        assert result is not None
        user = get_user_by_email(db, "retry@test.com")
        assert user["failed_attempts"] == 0


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------


class TestTokenLifecycle:
    def _db(self, tmp_path: Path) -> Path:
        db = tmp_path / "token.db"
        init_schema(db)
        return db

    def test_issue_tokens(self, tmp_path):
        db = self._db(tmp_path)
        user = create_user(db, email="tok@test.com", password="pass")
        tokens = issue_tokens(user, secret_key=SECRET)
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"

        # Access token should decode
        payload = decode_token(tokens["access_token"], secret_key=SECRET)
        assert payload["email"] == "tok@test.com"
        assert payload["type"] == "access"

    def test_refresh_rotates_tokens(self, tmp_path):
        db = self._db(tmp_path)
        user = create_user(db, email="rot@test.com", password="pass")
        tokens = issue_tokens(user, secret_key=SECRET)
        new_tokens = refresh_access_token(
            db, tokens["refresh_token"],
            secret_key=SECRET,
        )
        assert new_tokens["access_token"] != tokens["access_token"]
        # Old refresh token should be blacklisted
        assert is_token_blacklisted(db, tokens["refresh_token"])

    def test_reused_refresh_token_rejected(self, tmp_path):
        db = self._db(tmp_path)
        user = create_user(db, email="reuse@test.com", password="pass")
        tokens = issue_tokens(user, secret_key=SECRET)
        # First refresh succeeds
        refresh_access_token(db, tokens["refresh_token"], secret_key=SECRET)
        # Second use of same refresh token should fail
        with pytest.raises(ValueError, match="revoked"):
            refresh_access_token(db, tokens["refresh_token"], secret_key=SECRET)

    def test_blacklist_token(self, tmp_path):
        db = self._db(tmp_path)
        blacklist_token(db, "some-token-value")
        assert is_token_blacklisted(db, "some-token-value")
        assert not is_token_blacklisted(db, "other-token")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_log_and_list_events(self, tmp_path):
        db = tmp_path / "audit.db"
        init_schema(db)
        log_audit_event(db, event_type="LOGIN_SUCCESS", user_email="a@test.com", ip_address="1.2.3.4")
        log_audit_event(db, event_type="LOGIN_FAILED", user_email="b@test.com")
        events = list_audit_events(db, limit=10)
        assert len(events) == 2
        assert events[0]["event_type"] == "LOGIN_FAILED"  # newest first
        assert events[1]["event_type"] == "LOGIN_SUCCESS"
