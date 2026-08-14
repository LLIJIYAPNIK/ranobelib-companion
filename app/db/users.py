"""Access to the ``users`` table (see migrations/0001_users.sql)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class User:
    id: int
    email: str
    password_hash: str
    created_at: str
    nickname: str | None = None
    bio: str | None = None
    avatar_path: str | None = None


def create_user(conn: sqlite3.Connection, email: str, password_hash: str) -> User:
    """Raises ``sqlite3.IntegrityError`` if `email` is already registered - callers
    (see app/api/auth.py) are expected to check `get_user_by_email` first and turn that
    into a form error, but the UNIQUE constraint is the actual source of truth against
    a races between two concurrent registrations."""
    email = _normalize_email(email)
    created_at = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (email, password_hash, created_at),
    )
    conn.commit()
    return User(
        id=cursor.lastrowid, email=email, password_hash=password_hash, created_at=created_at
    )


def update_user_account(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    email: str,
    nickname: str | None,
    bio: str | None,
) -> User:
    """Raises ``sqlite3.IntegrityError`` if `email` collides with a *different* account -
    same defense-in-depth as `create_user`: callers (see app/api/settings.py) check
    `get_user_by_email` first and turn that into a form error, this is the race-safe
    backstop. Unlike email, `nickname` isn't unique (see PR 90 in CLAUDE.md's roadmap -
    nothing in this app looks a user up by nickname, so uniqueness would be an
    artificial restriction)."""
    conn.execute(
        "UPDATE users SET email = ?, nickname = ?, bio = ? WHERE id = ?",
        (_normalize_email(email), nickname, bio, user_id),
    )
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


def update_user_avatar(conn: sqlite3.Connection, user_id: int, avatar_path: str) -> User:
    """Called after a validated image has already been written to disk (see
    `app/auth/avatar.py`'s `save_avatar`) - this just records the resulting filename."""
    conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_path, user_id))
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


def update_user_password(conn: sqlite3.Connection, user_id: int, password_hash: str) -> User:
    """No uniqueness concerns here unlike `update_user_account` - `password_hash` isn't a
    unique column, so there's nothing to check before writing it. Callers (see
    app/api/settings.py) are expected to have already verified the visitor's *current*
    password with `verify_password` before calling this."""
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


def get_user_by_email(conn: sqlite3.Connection, email: str) -> User | None:
    row = conn.execute(
        "SELECT id, email, password_hash, created_at, nickname, bio, avatar_path "
        "FROM users WHERE email = ?",
        (_normalize_email(email),),
    ).fetchone()
    return _row_to_user(row) if row is not None else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute(
        "SELECT id, email, password_hash, created_at, nickname, bio, avatar_path "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return _row_to_user(row) if row is not None else None


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
        nickname=row["nickname"],
        bio=row["bio"],
        avatar_path=row["avatar_path"],
    )


def _normalize_email(email: str) -> str:
    return email.strip().lower()
