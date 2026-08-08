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


def get_user_by_email(conn: sqlite3.Connection, email: str) -> User | None:
    row = conn.execute(
        "SELECT id, email, password_hash, created_at FROM users WHERE email = ?",
        (_normalize_email(email),),
    ).fetchone()
    return _row_to_user(row) if row is not None else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute(
        "SELECT id, email, password_hash, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return _row_to_user(row) if row is not None else None


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
    )


def _normalize_email(email: str) -> str:
    return email.strip().lower()
