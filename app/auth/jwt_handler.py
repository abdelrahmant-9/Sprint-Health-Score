"""JWT access and refresh token utilities.

Access tokens are short-lived (configurable, default 15 min) and carry the
user identity and role.  Refresh tokens live longer (default 7 days) and are
used solely to obtain new access tokens without re-entering credentials.

The module delegates to ``python-jose`` for encoding/decoding so that
algorithm selection (HS256) and expiry validation happen in a well-tested
library rather than hand-rolled code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from jose import ExpiredSignatureError, JWTError, jwt

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


def create_access_token(
    *,
    user_id: int,
    email: str,
    role: str,
    secret_key: str,
    expire_minutes: int = 15,
) -> str:
    """Return a signed JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def create_refresh_token(
    *,
    user_id: int,
    secret_key: str,
    expire_days: int = 7,
) -> str:
    """Return a signed JWT refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=expire_days),
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def decode_token(token: str, *, secret_key: str) -> dict:
    """Decode and validate a JWT token.

    Returns the full payload dict on success.

    Raises
    ------
    ValueError
        When the token is expired, malformed, or fails signature verification.
    """
    try:
        return jwt.decode(token, secret_key, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        raise ValueError("Token has expired")
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}")
