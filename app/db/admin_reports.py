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


# --- the card -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReasonCount:
    reason: str
    count: int


@dataclass(frozen=True)
class ReporterNote:
    """One reporter's own explanation. The admin may see who wrote it; the reported
    author never does."""

    reason: str
    details: str
    created_at: str
    reporter_id: int
    reporter: str


@dataclass(frozen=True)
class ObjectReports:
    count: int
    first_at: str | None
    reasons: list[ReasonCount]  # the most common first
    notes: list[ReporterNote]  # newest first, at most DETAILS_SHOWN


async def object_reports(conn: AsyncConnection, target_type: str, target_id: int) -> ObjectReports:
    """The open reports on one object, summed up for the card."""
    params = {"type": target_type, "id": target_id, "limit": DETAILS_SHOWN}
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT reason, COUNT(*) AS n, MIN(created_at) AS first_at FROM reports "
            "WHERE target_type = %(type)s AND target_id = %(id)s AND status = 'open' "
            "GROUP BY reason ORDER BY n DESC, reason",
            params,
        )
        rows = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT r.reason, r.details, r.created_at, r.reporter_user_id AS reporter_id, "
            "COALESCE(u.nickname, u.email) AS reporter FROM reports r "
            "JOIN users u ON u.id = r.reporter_user_id "
            "WHERE r.target_type = %(type)s AND r.target_id = %(id)s AND r.status = 'open' "
            "AND COALESCE(r.details, '') <> '' ORDER BY r.created_at DESC, r.id DESC "
            "LIMIT %(limit)s",
            params,
        )
        notes = [ReporterNote(**row) for row in await cursor.fetchall()]
    return ObjectReports(
        count=sum(row["n"] for row in rows),
        first_at=min((row["first_at"] for row in rows), default=None),
        reasons=[ReasonCount(row["reason"], row["n"]) for row in rows],
        notes=notes,
    )


@dataclass(frozen=True)
class ReportedProfile:
    id: int
    email: str
    nickname: str | None
    bio: str | None
    avatar_path: str | None
    created_at: str


async def reported_profile(conn: AsyncConnection, user_id: int) -> ReportedProfile | None:
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT id, email, nickname, bio, avatar_path, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        row = await cursor.fetchone()
    return ReportedProfile(**row) if row else None


@dataclass(frozen=True)
class AuthorRecord:
    """«История автора»: since when, how much they wrote, and how earlier reports on
    their comments and profile ended. Bans come from app/db/bans.py."""

    user_id: int
    created_at: str
    comments: int
    hidden_comments: int
    past_reports: int  # reports on them already decided
    upheld_reports: int  # of those, the ones that ended in a hide, a cleanup or a ban


async def author_record(conn: AsyncConnection, user_id: int) -> AuthorRecord | None:
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT u.created_at, "
            "(SELECT COUNT(*) FROM comments WHERE user_id = u.id) AS comments, "
            "(SELECT COUNT(*) FROM comments WHERE user_id = u.id "
            "  AND hidden_at IS NOT NULL) AS hidden_comments, "
            "COUNT(r.id) AS past_reports, "
            "COUNT(r.id) FILTER (WHERE r.status = 'actioned') AS upheld_reports "
            "FROM users u LEFT JOIN reports r ON r.status <> 'open' AND ("
            "  (r.target_type = 'profile' AND r.target_id = u.id) OR "
            "  (r.target_type = 'comment' AND r.target_id IN "
            "    (SELECT id FROM comments WHERE user_id = u.id))) "
            "WHERE u.id = %s GROUP BY u.id",
            (user_id,),
        )
        row = await cursor.fetchone()
    return AuthorRecord(user_id=user_id, **row) if row else None
