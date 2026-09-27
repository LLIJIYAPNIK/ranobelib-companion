"""Access to the ``email_verification_tokens`` table (migrations/0023_email_verification.sql)
- PR 246's email confirmation at registration.

Same shape as ``app/db/password_reset.py`` (PR 225): only hashes are ever written to the
database, and the raw secrets exist only long enough to go into the email
(app/api/auth.py). One email carries two secrets for the same row: a long link token and
a 6-digit code for typing on another device. Using either one consumes the row.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg import AsyncConnection

# Wrong codes a single email's row accepts before the code stops working. A 6-digit code
# has only a million values, so without a cap it could be guessed. The per-IP limiter in
# app/api/auth.py is a second layer on top; this one follows the row itself.
MAX_FAILED_CODE_ATTEMPTS = 5


@dataclass(frozen=True)
class IssuedVerification:
    """The raw secrets for one freshly created row - for the email only, never stored."""

    token: str
    code: str


@dataclass(frozen=True)
class EmailVerificationToken:
    id: int
    user_id: int


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _hash_code(token_hash: str, code: str) -> str:
    # Salted with the row's own token hash, so equal codes on different rows don't share
    # a hash.
    return hashlib.sha256(f"{token_hash}:{code}".encode()).hexdigest()


async def create_token(
    conn: AsyncConnection, user_id: int, ttl_seconds: float
) -> IssuedVerification:
    """Creates a row for `user_id` and returns its raw link token and code.

    Any earlier unused rows for the same user are marked used first, so only the newest
    email works. Otherwise every resend would add another 5 code guesses to the budget.
    """
    now = datetime.now(UTC)
    await conn.execute(
        "UPDATE email_verification_tokens SET used_at = %s WHERE user_id = %s AND used_at IS NULL",
        (now.isoformat(), user_id),
    )
    raw_token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    token_hash = _hash_token(raw_token)
    await conn.execute(
        "INSERT INTO email_verification_tokens "
        "(user_id, token_hash, code_hash, expires_at, created_at) VALUES (%s, %s, %s, %s, %s)",
        (
            user_id,
            token_hash,
            _hash_code(token_hash, code),
            (now + timedelta(seconds=ttl_seconds)).isoformat(),
            now.isoformat(),
        ),
    )
    return IssuedVerification(token=raw_token, code=code)


def _is_expired(expires_at: str) -> bool:
    return datetime.fromisoformat(expires_at) < datetime.now(UTC)


async def get_valid_token(conn: AsyncConnection, raw_token: str) -> EmailVerificationToken | None:
    """The row for a link token, only if it exists, is unused and unexpired. Returns None
    for all three failures alike, same as password_reset.get_valid_token()."""
    cursor = await conn.execute(
        "SELECT id, user_id, expires_at, used_at FROM email_verification_tokens "
        "WHERE token_hash = %s",
        (_hash_token(raw_token),),
    )
    row = await cursor.fetchone()
    if row is None or row["used_at"] is not None or _is_expired(row["expires_at"]):
        return None
    return EmailVerificationToken(id=row["id"], user_id=row["user_id"])


async def check_code(
    conn: AsyncConnection, user_id: int, code: str
) -> EmailVerificationToken | None:
    """The row `code` unlocks for `user_id`, or None.

    Only the user's current row counts: the newest one that's unused, unexpired and under
    the attempt cap. A wrong code counts against that row. Codes are only checked
    against the account waiting in the visitor's own session (see app/api/auth.py), never
    looked up globally, so a guess can only ever hit one account."""
    code = "".join(code.split())
    cursor = await conn.execute(
        "SELECT id, user_id, token_hash, code_hash, failed_code_attempts, expires_at "
        "FROM email_verification_tokens WHERE user_id = %s AND used_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    if (
        row is None
        or _is_expired(row["expires_at"])
        or row["failed_code_attempts"] >= MAX_FAILED_CODE_ATTEMPTS
    ):
        return None
    if hmac.compare_digest(_hash_code(row["token_hash"], code), row["code_hash"]):
        return EmailVerificationToken(id=row["id"], user_id=row["user_id"])
    await conn.execute(
        "UPDATE email_verification_tokens SET failed_code_attempts = failed_code_attempts + 1 "
        "WHERE id = %s",
        (row["id"],),
    )
    return None


async def mark_token_used(conn: AsyncConnection, token_id: int) -> None:
    """Consumes the row, for its link and its code alike."""
    await conn.execute(
        "UPDATE email_verification_tokens SET used_at = %s WHERE id = %s",
        (datetime.now(UTC).isoformat(), token_id),
    )
