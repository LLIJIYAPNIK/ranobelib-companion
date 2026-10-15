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
from datetime import UTC, datetime, timedelta

from psycopg import AsyncConnection


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


async def reports_filed_last_day(
    conn: AsyncConnection, reporter_user_id: int, now: datetime | None = None
) -> int:
    """How many reports the reader filed in the 24 hours before `now`, whatever became
    of them - a dismissed report still counts toward the daily limit."""
    since = (_now(now) - timedelta(days=1)).isoformat()
    cursor = await conn.execute(
        "SELECT COUNT(*) AS n FROM reports WHERE reporter_user_id = %s AND created_at > %s",
        (reporter_user_id, since),
    )
    return (await cursor.fetchone())["n"]


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


# --- decisions in «Модерация» (PR 358) ----------------------------------------------------

DISMISSED = "dismissed"
ACTIONED = "actioned"


async def resolve_reports(
    conn: AsyncConnection,
    target_type: str,
    target_id: int,
    *,
    status: str,
    resolution: str,
    now: datetime | None = None,
) -> list[int]:
    """Closes every open report on the object - ``dismissed`` («Одобрить») or
    ``actioned`` - with what was done. Returns their ids, which «Отменить» reopens."""
    cursor = await conn.execute(
        "UPDATE reports SET status = %s, resolution = %s, resolved_at = %s "
        "WHERE target_type = %s AND target_id = %s AND status = 'open' RETURNING id",
        (status, resolution, _now(now).isoformat(), target_type, target_id),
    )
    return sorted(row["id"] for row in await cursor.fetchall())


async def reopen_reports(conn: AsyncConnection, report_ids: list[int]) -> list[int]:
    """Undoes resolve_reports(): the reports are open again. One whose reporter has
    since filed a new open report on the same object stays closed - that newer one is
    already in the queue. Returns the ids reopened."""
    if not report_ids:
        return []
    cursor = await conn.execute(
        "UPDATE reports r SET status = 'open', resolution = NULL, resolved_at = NULL "
        "WHERE r.id = ANY(%s) AND r.status <> 'open' AND NOT EXISTS ("
        "  SELECT 1 FROM reports o WHERE o.status = 'open' "
        "  AND o.reporter_user_id = r.reporter_user_id "
        "  AND o.target_type = r.target_type AND o.target_id = r.target_id"
        ") RETURNING r.id",
        (list(report_ids),),
    )
    return sorted(row["id"] for row in await cursor.fetchall())
