"""«Модерация» (PR 358): the queue of reported objects and what the moderator needs to
decide on one. Read-only, under the admin timeout (PR 326's transaction), like every
other admin query; resolving and reopening reports live in app/db/reports.py.

The queue groups open reports by object: the most reported first, then the one waiting
longest. A reporter has at most one open report per object (app/db/reports.py), so the
count is already one per person.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import AsyncConnection

from app.db.admin_data import _read_only

COMMENT = "comment"
PROFILE = "profile"
TARGET_TYPES = (COMMENT, PROFILE)

# How many objects the queue lists - far more than a day's worth on this site; past it
# the screen says that more are waiting.
QUEUE_LIMIT = 200
# The reporters' own explanations shown on the card, newest first.
DETAILS_SHOWN = 10


@dataclass(frozen=True)
class QueueItem:
    target_type: str
    target_id: int
    reports: int
    first_at: str  # the oldest open report - how long it has waited
    last_at: str
    top_reason: str
    # The object as it is now. None when it no longer exists (a comment deleted for good,
    # an account gone) - the reports can then only be dismissed.
    author_id: int | None
    author: str | None
    text: str | None  # a comment's body; a profile's «о себе»
    hidden: bool  # a comment hidden already (PR 350)
    deleted: bool  # a comment deleted by its author


@dataclass(frozen=True)
class Queue:
    items: list[QueueItem]
    total: int  # objects waiting, all of them, not just the ones listed
    counts: dict[str, int]  # per target type


async def queue(
    conn: AsyncConnection, target_type: str | None = None, limit: int = QUEUE_LIMIT
) -> Queue:
    type_where = "AND r.target_type = %(type)s " if target_type in TARGET_TYPES else ""
    params = {"type": target_type, "limit": limit}
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT target_type, COUNT(DISTINCT target_id) AS n FROM reports "
            "WHERE status = 'open' GROUP BY target_type"
        )
        counts = {row["target_type"]: row["n"] for row in await cursor.fetchall()}
        cursor = await conn.execute(
            "SELECT r.target_type, r.target_id, COUNT(*) AS reports, "
            "MIN(r.created_at) AS first_at, MAX(r.created_at) AS last_at, "
            "mode() WITHIN GROUP (ORDER BY r.reason) AS top_reason "
            f"FROM reports r WHERE r.status = 'open' {type_where}"
            "GROUP BY r.target_type, r.target_id "
            "ORDER BY reports DESC, first_at ASC, r.target_type, r.target_id "
            "LIMIT %(limit)s",
            params,
        )
        groups = await cursor.fetchall()
        comments = await _comments(
            conn, [g["target_id"] for g in groups if g["target_type"] == COMMENT]
        )
        profiles = await _profiles(
            conn, [g["target_id"] for g in groups if g["target_type"] == PROFILE]
        )
    items = []
    for group in groups:
        found = (comments if group["target_type"] == COMMENT else profiles).get(group["target_id"])
        items.append(
            QueueItem(
                target_type=group["target_type"],
                target_id=group["target_id"],
                reports=group["reports"],
                first_at=group["first_at"],
                last_at=group["last_at"],
                top_reason=group["top_reason"],
                author_id=found["author_id"] if found else None,
                author=found["author"] if found else None,
                text=found["text"] if found else None,
                hidden=bool(found and found["hidden"]),
                deleted=bool(found and found["deleted"]),
            )
        )
    total = sum(counts.values()) if target_type not in TARGET_TYPES else counts.get(target_type, 0)
    return Queue(items=items, total=total, counts=counts)


async def _comments(conn: AsyncConnection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    cursor = await conn.execute(
        "SELECT c.id, c.user_id AS author_id, COALESCE(u.nickname, u.email) AS author, "
        "c.body AS text, c.hidden_at IS NOT NULL AS hidden, c.is_deleted = 1 AS deleted "
        "FROM comments c JOIN users u ON u.id = c.user_id WHERE c.id = ANY(%s)",
        (ids,),
    )
    return {row["id"]: row for row in await cursor.fetchall()}


async def _profiles(conn: AsyncConnection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    cursor = await conn.execute(
        "SELECT id, id AS author_id, COALESCE(nickname, email) AS author, bio AS text, "
        "false AS hidden, false AS deleted FROM users WHERE id = ANY(%s)",
        (ids,),
    )
    return {row["id"]: row for row in await cursor.fetchall()}
