"""The admin panel's global search (PR 353): accounts and comments matching what was typed
in the top bar. Read-only with the admin timeout (PR 326's transaction), a few rows per
kind, and only columns that are safe to show - never a hash, token or session column
(the queries name what they select; nothing is ``SELECT *``).

Titles from the SDK's catalog aren't searched: the panel has no screen for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import AsyncConnection

from app.db.admin_data import _read_only

# Digits that fit an INTEGER id column.
_MAX_ID_DIGITS = 9


@dataclass(frozen=True)
class UserHit:
    id: int
    email: str
    nickname: str | None


@dataclass(frozen=True)
class CommentHit:
    id: int
    body: str
    author: str
    slug_url: str
    is_deleted: bool


def _id(query: str) -> int | None:
    return int(query) if query.isdigit() and len(query) <= _MAX_ID_DIGITS else None


async def search_users(conn: AsyncConnection, query: str, limit: int) -> list[UserHit]:
    """By email or nickname (case-insensitive substring - position(), so nothing in the
    text acts as a wildcard) or, for digits, the exact id; the id match first."""
    params = {"q": query.lower(), "id": _id(query), "limit": limit}
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT id, email, nickname FROM users "
            "WHERE id = %(id)s OR position(%(q)s in lower(email)) > 0 "
            "OR position(%(q)s in lower(coalesce(nickname, ''))) > 0 "
            "ORDER BY (id = %(id)s) DESC NULLS LAST, id DESC LIMIT %(limit)s",
            params,
        )
        rows = await cursor.fetchall()
    return [UserHit(row["id"], row["email"], row["nickname"]) for row in rows]


async def search_comments(conn: AsyncConnection, query: str, limit: int) -> list[CommentHit]:
    """By text or, for digits, the exact id - newest first. A comment deleted by its
    author keeps its row but not its text in the results."""
    params = {"q": query.lower(), "id": _id(query), "limit": limit}
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT c.id, c.body, c.slug_url, c.is_deleted, u.nickname, u.email "
            "FROM comments c JOIN users u ON u.id = c.user_id "
            "WHERE c.id = %(id)s OR (c.is_deleted = 0 AND position(%(q)s in lower(c.body)) > 0) "
            "ORDER BY (c.id = %(id)s) DESC NULLS LAST, c.id DESC LIMIT %(limit)s",
            params,
        )
        rows = await cursor.fetchall()
    return [
        CommentHit(
            id=row["id"],
            body="" if row["is_deleted"] else row["body"],
            author=row["nickname"] or row["email"],
            slug_url=row["slug_url"],
            is_deleted=bool(row["is_deleted"]),
        )
        for row in rows
    ]
