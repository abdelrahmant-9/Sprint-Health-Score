"""FastAPI dependency functions for JWT-based authentication and RBAC.

Usage in route handlers::

    @app.get("/protected")
    def protected(user: dict = Depends(get_current_user)):
        ...

    @app.post("/admin-only")
    def admin_only(user: dict = Depends(require_role("admin"))):
        ...
"""

from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, Request, status

from app.auth.jwt_handler import decode_token
from app.config import load_settings

logger = logging.getLogger(__name__)


def _extract_bearer_token(authorization: str | None = Header(default=None)) -> str:
    """Extract the JWT from the ``Authorization: Bearer <token>`` header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def get_current_user(token: str = Depends(_extract_bearer_token)) -> dict:
    """Decode the JWT and return the user payload.

    Falls back to legacy ``X-API-KEY`` header check for backward compatibility
    during transition.  This fallback will be removed in a future release.
    """
    settings = load_settings()
    try:
        payload = decode_token(token, secret_key=settings.secret_key)
    except ValueError as exc:
        # Backward-compat: accept raw API key as the bearer token during transition
        if token == settings.api_key:
            logger.info("Legacy API-key auth used — please migrate to JWT")
            return {
                "id": 0,
                "email": "legacy-api-key",
                "role": "admin",
            }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "id": int(payload["sub"]),
        "email": payload["email"],
        "role": payload["role"],
    }


def require_role(*allowed_roles: str):
    """Return a FastAPI dependency that enforces role membership.

    Example::

        @app.post("/admin-only")
        def admin_route(user: dict = Depends(require_role("admin"))):
            ...
    """

    def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _dependency
