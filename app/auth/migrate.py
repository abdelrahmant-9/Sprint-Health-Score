"""Migrate legacy auth_users.json to the new SQLite-based user store.

Run once after upgrading to the new authentication system::

    python -m app.auth.migrate

This reads the existing ``auth_users.json``, re-hashes each user's password
with bcrypt (since the old SHA-256 hashes cannot be reversed, users whose
plaintext passwords are unknown will need to reset them), and inserts them
into the ``users`` table.

For users whose original plaintext is unavailable the script stores a
placeholder hash and marks them for password reset via a log message.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

from app.auth.password import hash_password
from app.auth.service import create_user, get_user_by_email
from app.config import load_settings
from app.storage import init_schema

logger = logging.getLogger(__name__)

# Known plaintext passwords for the default accounts (from .env defaults).
# Only used to re-hash known defaults — NOT stored anywhere.
_KNOWN_SHA256_TO_PLAIN: dict[str, str] = {
    hashlib.sha256(b"admin1234").hexdigest(): "admin1234",
    hashlib.sha256(b"test1234").hexdigest(): "test1234",
}


def migrate(auth_json_path: Path | None = None) -> None:
    """Read auth_users.json and insert users into SQLite with bcrypt hashes."""
    settings = load_settings()
    db_path = settings.sqlite_path
    init_schema(db_path)

    json_path = auth_json_path or Path(__file__).resolve().parents[2] / "auth_users.json"
    if not json_path.exists():
        logger.info("No auth_users.json found at %s — nothing to migrate.", json_path)
        return

    data = json.loads(json_path.read_text(encoding="utf-8"))
    users = data.get("users", {})
    migrated = 0
    skipped = 0

    for email, info in users.items():
        email = email.strip().lower()
        existing = get_user_by_email(db_path, email)
        if existing:
            logger.info("User '%s' already exists in DB — skipped.", email)
            skipped += 1
            continue

        old_hash = info.get("password", "")
        role = info.get("role", "user")

        # Try to recover plaintext from known defaults
        plaintext = _KNOWN_SHA256_TO_PLAIN.get(old_hash)
        if plaintext:
            result = create_user(db_path, email=email, password=plaintext, role=role)
            if result:
                logger.info("Migrated user '%s' (role=%s) with re-hashed password.", email, role)
                migrated += 1
            else:
                logger.warning("Failed to migrate user '%s'.", email)
        else:
            # Cannot reverse SHA-256 — create with a temporary password
            temp_password = "ChangeMe!2026"
            result = create_user(db_path, email=email, password=temp_password, role=role)
            if result:
                logger.warning(
                    "Migrated user '%s' (role=%s) with TEMPORARY password '%s'. "
                    "User must change their password on first login.",
                    email,
                    role,
                    temp_password,
                )
                migrated += 1

    logger.info("Migration complete: %s migrated, %s skipped.", migrated, skipped)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    migrate()
