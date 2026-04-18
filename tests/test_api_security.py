"""Tests for API authentication, authorization, and rate limiting."""

from __future__ import annotations

import os

os.environ["DEBUG"] = "false"
os.environ["JIRA_EMAIL"] = "user@example.com"
os.environ["JIRA_API_TOKEN"] = "token"
os.environ["API_KEY"] = "test-api-key"
os.environ["SECRET_KEY"] = "test-secret-key-at-least-16-chars"

from fastapi.testclient import TestClient

from api.main import app
from app.auth.service import create_user, get_user_by_email
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


def test_openapi_schema_builds_successfully(monkeypatch) -> None:
    _setup_env(monkeypatch)
    schema = app.openapi()
    assert "/auth/login" in schema["paths"]
    login_request = schema["paths"]["/auth/login"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert login_request["$ref"].endswith("/LoginRequest")


def test_openapi_json_endpoint_is_available(monkeypatch) -> None:
    _setup_env(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["paths"]["/auth/login"]["post"]["responses"]["200"]


def test_docs_endpoint_is_available(monkeypatch) -> None:
    _setup_env(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/docs")
    assert response.status_code == 200
    assert "Swagger UI" in response.text


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


def test_login_openapi_request_shape_is_still_json_body(monkeypatch) -> None:
    _setup_env(monkeypatch)
    schema = app.openapi()
    request_body = schema["paths"]["/auth/login"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    login_schema = schema["components"]["schemas"][request_body["$ref"].rsplit("/", 1)[-1]]
    assert set(login_schema["required"]) == {"email", "password"}
    assert login_schema["properties"]["email"]["format"] == "email"


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


def test_super_admin_can_manage_users(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))

    from app.config import load_settings

    settings = load_settings()
    init_schema(settings.sqlite_path)
    super_admin_token = _create_test_user(settings.sqlite_path, email="root@test.com", role="super_admin")

    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {super_admin_token}"}

        create_response = client.post(
            "/auth/users",
            headers=headers,
            json={"email": "managed@test.com", "password": "pass123", "role": "user"},
        )
        assert create_response.status_code == 201
        created_payload = create_response.json()
        created_user = created_payload["user"]
        user_id = created_user["id"]
        assert created_user["email"] == "managed@test.com"
        assert created_user["role"] == "user"
        assert "password_hash" not in created_user

        list_response = client.get("/auth/users", headers=headers)
        assert list_response.status_code == 200
        listed_users = list_response.json()
        assert any(user["email"] == "managed@test.com" for user in listed_users)

        role_response = client.put(
            f"/auth/users/{user_id}/role",
            headers=headers,
            json={"role": "admin"},
        )
        assert role_response.status_code == 200
        assert role_response.json()["user"]["role"] == "admin"

        lock_response = client.put(f"/auth/users/{user_id}/lock", headers=headers)
        assert lock_response.status_code == 200
        assert lock_response.json()["user"]["locked_until"] is not None

        unlock_response = client.put(f"/auth/users/{user_id}/unlock", headers=headers)
        assert unlock_response.status_code == 200
        assert unlock_response.json()["user"]["locked_until"] is None
        assert unlock_response.json()["user"]["failed_attempts"] == 0

        delete_response = client.delete(f"/auth/users/{user_id}", headers=headers)
        assert delete_response.status_code == 200

    assert get_user_by_email(settings.sqlite_path, "managed@test.com") is None


def test_admin_can_view_users_but_cannot_modify(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))

    from app.config import load_settings

    settings = load_settings()
    init_schema(settings.sqlite_path)
    admin_token = _create_test_user(settings.sqlite_path, email="admin@test.com", role="admin")
    target_user = create_user(settings.sqlite_path, email="readonly@test.com", password="pass123", role="user")

    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {admin_token}"}

        list_response = client.get("/auth/users", headers=headers)
        assert list_response.status_code == 200
        assert any(user["email"] == "readonly@test.com" for user in list_response.json())

        create_response = client.post(
            "/auth/users",
            headers=headers,
            json={"email": "blocked@test.com", "password": "pass123", "role": "user"},
        )
        assert create_response.status_code == 403

        role_response = client.put(
            f"/auth/users/{target_user['id']}/role",
            headers=headers,
            json={"role": "admin"},
        )
        assert role_response.status_code == 403

        lock_response = client.put(f"/auth/users/{target_user['id']}/lock", headers=headers)
        assert lock_response.status_code == 403

        unlock_response = client.put(f"/auth/users/{target_user['id']}/unlock", headers=headers)
        assert unlock_response.status_code == 403

        delete_response = client.delete(f"/auth/users/{target_user['id']}", headers=headers)
        assert delete_response.status_code == 403


def test_regular_user_cannot_access_user_management(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))

    from app.config import load_settings

    settings = load_settings()
    init_schema(settings.sqlite_path)
    user_token = _create_test_user(settings.sqlite_path, email="member@test.com", role="user")

    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {user_token}"}
        response = client.get("/auth/users", headers=headers)

    assert response.status_code == 403


def test_super_admin_cannot_delete_or_lock_self(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))

    from app.config import load_settings

    settings = load_settings()
    init_schema(settings.sqlite_path)
    super_admin_token = _create_test_user(settings.sqlite_path, email="self@test.com", role="super_admin")
    super_admin = get_user_by_email(settings.sqlite_path, "self@test.com")
    assert super_admin is not None

    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {super_admin_token}"}
        lock_response = client.put(f"/auth/users/{super_admin['id']}/lock", headers=headers)
        delete_response = client.delete(f"/auth/users/{super_admin['id']}", headers=headers)
        role_response = client.put(
            f"/auth/users/{super_admin['id']}/role",
            headers=headers,
            json={"role": "admin"},
        )

    assert lock_response.status_code == 400
    assert delete_response.status_code == 400
    assert role_response.status_code == 400


def test_metrics_json_endpoint_is_read_only_for_user_and_updatable_by_admin(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))

    from app.config import load_settings

    settings = load_settings()
    init_schema(settings.sqlite_path)
    user_token = _create_test_user(settings.sqlite_path, email="reader@test.com", role="user")
    admin_token = _create_test_user(settings.sqlite_path, email="metric-admin@test.com", role="admin")

    sample_metrics = [
        {
            "metric_name": "completed_scope",
            "base_value": 16.0,
            "override_value": 18.5,
            "value": 18.5,
            "updated_at": "2026-04-18T00:00:00+00:00",
        }
    ]

    monkeypatch.setattr("api.main.get_metrics_catalog", lambda _settings: sample_metrics)
    monkeypatch.setattr(
        "api.main.update_metric_override",
        lambda _settings, metric_name, value: {
            "metric_name": metric_name,
            "base_value": 16.0,
            "override_value": value,
            "value": value,
            "updated_at": "2026-04-18T00:00:00+00:00",
        },
    )

    with TestClient(app) as client:
        read_headers = {"Authorization": f"Bearer {user_token}", "Accept": "application/json"}
        read_response = client.get("/metrics?format=json", headers=read_headers)
        assert read_response.status_code == 200
        assert read_response.json()[0]["metric_name"] == "completed_scope"

        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        update_response = client.put(
            "/metrics/completed_scope",
            headers=admin_headers,
            json={"value": 22.0},
        )
        assert update_response.status_code == 200
        assert update_response.json()["value"] == 22.0


def test_regular_user_cannot_update_metrics(monkeypatch, tmp_path) -> None:
    _setup_env(monkeypatch)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))

    from app.config import load_settings

    settings = load_settings()
    init_schema(settings.sqlite_path)
    user_token = _create_test_user(settings.sqlite_path, email="readonly-metric@test.com", role="user")
    monkeypatch.setattr(
        "api.main.update_metric_override",
        lambda *_args, **_kwargs: {
            "metric_name": "completed_scope",
            "base_value": 16.0,
            "override_value": 22.0,
            "value": 22.0,
            "updated_at": "2026-04-18T00:00:00+00:00",
        },
    )

    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {user_token}"}
        response = client.put("/metrics/completed_scope", headers=headers, json={"value": 22.0})

    assert response.status_code == 403


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
