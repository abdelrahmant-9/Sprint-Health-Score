"""Bcrypt password hashing utilities.

Replaces the previous SHA-256 approach with salted bcrypt hashing.
Each call to ``hash_password`` produces a unique hash even for the same
input, making rainbow-table attacks infeasible.
"""

from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain* with a random salt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return ``True`` when *plain* matches the stored bcrypt *hashed* value."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
