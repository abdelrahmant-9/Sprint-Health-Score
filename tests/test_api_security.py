"""Tests for API authentication, authorization, and rate limiting."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from app.auth.service import create_user
from app.auth.jwt_handler import create_access_token
from app.storage import init_schema


SECRET = "test-secret-key-at-least-16-chars"


def _setup_env(monkeypatch):
    """Set required environment variables for tests."""
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("SECRET_KEY", SECRET)


def _create_test_user(db_path, email="admin@test.com", role="admin"):
    """Create a test user and return an access token."""
    init_schema(db_path)
    user = create_user(db_path, email=email, password="testpass", role=role)
    token = create_access_token(
        user_id=user["id"], email=user["email"], role=user["role"],
        secret_key=SECRET, expire_minutes=5,
    )
    return token


def test_health_endpoint_is_public(monkeypatch) -> None:
    _setup_env(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_protected_endpoint_rejects_no_auth(monkeypatch) -> None:
    _setup_env(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/health-score")
    assert response.status_code == 401


def test_protected_endpoint_rejects_invalid_token(monkeypatch) -> None:
    _setup_env(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/health-score", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401


def test_legacy_api_key_still_works(monkeypatch) -> None:
    """Backward compatibility: passing the API_KEY as a bearer token should still work."""
    _setup_env(monkeypatch)
    monkeypatch.setattr(
        "api.main.calculate_health_snapshot",
        lambda _settings: {"score": 80, "completion_rate": 82.5, "breakdown": {}},
    )
    with TestClient(app) as client:
        response = client.get("/health-score", headers={"Authorization": "Bearer test-api-key"})
    assert response.status_code == 200


def test_login_returns_tokens(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as client:
        # First create a user via the DB directly
        from app.config import load_settings
        settings = load_settings()
        init_schema(settings.sqlite_path)
        create_user(settings.sqlite_path, email="login@test.com", password="mypass", role="admin")

        response = client.post("/auth/login", json={"email": "login@test.com", "password": "mypass"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_rejects_wrong_password(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as client:
        from app.config import load_settings
        settings = load_settings()
        init_schema(settings.sqlite_path)
        create_user(settings.sqlite_path, email="wrong@test.com", password="correct")

        response = client.post("/auth/login", json={"email": "wrong@test.com", "password": "incorrect"})
    assert response.status_code == 401


def test_run_requires_admin_role(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(
        "api.main.calculate_health_snapshot",
        lambda _settings: {"score": 80, "completion_rate": 82.5, "breakdown": {}, "report": {"sprint": {"name": "S1"}}},
    )
    monkeypatch.setattr("api.main.get_daily_activity", lambda _s: {"developers": [], "testers": [], "bugs_today": 0, "top_developer": {"name": "", "completed": 0}, "top_tester": {"name": "", "bugs_closed": 0}, "insights": []})
    monkeypatch.setattr("api.main.save_sprint_result", lambda *_a, **_k: 1)
    monkeypatch.setattr("api.main.send_slack_message", lambda *_a, **_k: None)

    from app.config import load_settings
    settings = load_settings()

    # Create a "user" role account (not admin)
    user_token = _create_test_user(settings.sqlite_path, email="viewer@test.com", role="user")

    with TestClient(app) as client:
        response = client.post("/run", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 403


def test_run_is_rate_limited(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(
        "api.main.calculate_health_snapshot",
        lambda _settings: {"score": 80, "completion_rate": 82.5, "breakdown": {}, "report": {"sprint": {"name": "S1"}}},
    )
    monkeypatch.setattr("api.main.get_daily_activity", lambda _s: {"developers": [], "testers": [], "bugs_today": 0, "top_developer": {"name": "", "completed": 0}, "top_tester": {"name": "", "bugs_closed": 0}, "insights": []})
    monkeypatch.setattr("api.main.save_sprint_result", lambda *_a, **_k: 1)
    monkeypatch.setattr("api.main.send_slack_message", lambda *_a, **_k: None)

    from app.config import load_settings
    settings = load_settings()
    admin_token = _create_test_user(settings.sqlite_path, email="ratelim@test.com", role="admin")

    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {admin_token}"}
        first = client.post("/run", headers=headers)
        second = client.post("/run", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
