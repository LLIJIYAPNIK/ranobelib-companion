"""Readers' reports, ``reports`` (migrations/0032_reports.sql, PR 356).

Filed through ``POST /reports`` (app/api/reports.py), which checks who may report what;
this module only stores them. A reporter has at most one open report per object - a
repeat replaces its reason and details instead of adding a row, so the moderation queue
counts people, not clicks.

Nothing here hands a report to the reported author: who reported is for the admin panel
only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import AsyncConnection

TARGET_TYPES = ("comment", "profile")


@dataclass(frozen=True)
class Report:
    id: int
    reporter_user_id: int
    target_type: str
    target_id: int
    reason: str
    details: str | None
    status: str
    resolution: str | None
    resolved_at: str | None
    created_at: str


_COLUMNS = (
    "id, reporter_user_id, target_type, target_id, reason, details, status, resolution, "
    "resolved_at, created_at"
)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


@dataclass(frozen=True)
class CommentTarget:
    """What ``POST /reports`` needs to know about a reported comment."""

    user_id: int
    hidden: bool
    deleted: bool


async def comment_target(conn: AsyncConnection, comment_id: int) -> CommentTarget | None:
    cursor = await conn.execute(
        "SELECT user_id, hidden_at, is_deleted FROM comments WHERE id = %s", (comment_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return CommentTarget(
        user_id=row["user_id"],
        hidden=row["hidden_at"] is not None,
        deleted=bool(row["is_deleted"]),
    )


async def open_report(
    conn: AsyncConnection, reporter_user_id: int, target_type: str, target_id: int
) -> Report | None:
    """This reporter's open report on the object, if there is one."""
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM reports WHERE reporter_user_id = %s AND target_type = %s "
        "AND target_id = %s AND status = 'open'",
        (reporter_user_id, target_type, target_id),
    )
    row = await cursor.fetchone()
    return Report(**row) if row else None


async def file_report(
    conn: AsyncConnection,
    reporter_user_id: int,
    target_type: str,
    target_id: int,
    *,
    reason: str,
    details: str | None = None,
    now: datetime | None = None,
) -> tuple[Report, bool]:
    """Files a report, or updates the reporter's open one on the same object. Returns it
    and whether it is new. A report already closed by the admin doesn't count - the
    next one is new. Validating the reason, details and target is the caller's job."""
    cursor = await conn.execute(
        "INSERT INTO reports (reporter_user_id, target_type, target_id, reason, details, "
        "created_at) VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (reporter_user_id, target_type, target_id) WHERE status = 'open' "
        "DO UPDATE SET reason = EXCLUDED.reason, details = EXCLUDED.details "
        f"RETURNING {_COLUMNS}, (xmax = 0) AS created",
        (
            reporter_user_id,
            target_type,
            target_id,
            reason,
            details or None,
            _now(now).isoformat(),
        ),
    )
    row = await cursor.fetchone()
    created = row.pop("created")
    return Report(**row), created
