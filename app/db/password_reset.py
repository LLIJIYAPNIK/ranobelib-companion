"""Access to the ``password_reset_tokens`` table (migrations/0021_password_reset_tokens.sql)
- PR 225's "Забыли пароль?" flow.

Only the token's hash is ever written to the database - never the raw value, which exists
only long enough to put it in the emailed link (app/api/auth.py's request_password_reset())
and is never logged. Same "store the hash, not the secret" treatment already applied to
``users.password_hash``.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg import AsyncConnection


@dataclass(frozen=True)
class PasswordResetToken:
    id: int
    user_id: int
    expires_at: str
    used_at: str | None


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_token(conn: AsyncConnection, user_id: int, ttl_seconds: float) -> str:
    """Creates a new reset token for `user_id` and returns the *raw* token - the only time
    it ever exists outside this function, for the caller to put straight into the emailed
    link. Cryptographically random (`secrets.token_urlsafe`), not guessable/enumerable."""
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    await conn.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) "
        "VALUES (%s, %s, %s, %s)",
        (user_id, _hash_token(raw_token), expires_at.isoformat(), now.isoformat()),
    )
    return raw_token


async def get_valid_token(conn: AsyncConnection, raw_token: str) -> PasswordResetToken | None:
    """The token row for `raw_token`, only if it exists, hasn't already been used, and
    hasn't expired - None for any of those three reasons alike, so callers (see
    app/api/auth.py) don't need to tell a stranger *why* a link no longer works, just that
    it doesn't."""
    cursor = await conn.execute(
        "SELECT id, user_id, expires_at, used_at FROM password_reset_tokens WHERE token_hash = %s",
        (_hash_token(raw_token),),
    )
    row = await cursor.fetchone()
    if row is None or row["used_at"] is not None:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC):
        return None
    return PasswordResetToken(
        id=row["id"], user_id=row["user_id"], expires_at=row["expires_at"], used_at=row["used_at"]
    )


async def mark_token_used(conn: AsyncConnection, token_id: int) -> None:
    """Called once the new password has actually been written - makes this exact token
    unusable for a second POST /password-reset/{token}, whether that's a visitor
    double-submitting, reusing an old email, or a stale browser-history entry."""
    await conn.execute(
        "UPDATE password_reset_tokens SET used_at = %s WHERE id = %s",
        (datetime.now(UTC).isoformat(), token_id),
    )
