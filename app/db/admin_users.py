"""«Пользователи» (PR 348): the admin's list of accounts and one account's summary.

Read-only (PR 326's transaction with the admin timeout). The actions on an account - end
its sessions, confirm its email, send a password reset - live in the route and reuse the
site's own functions (app/db/users.py, the reset email), each recorded in the action log.

Secrets never leave here: only the columns below are selected - no password hash, no
session_version, no tokens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from psycopg import AsyncConnection

from app.db.admin_data import _read_only
from app.db.admin_stats import build_period

PAGE_SIZE = 25

# Sort keys the list accepts -> the SQL they order by (a whitelist).
SORTS = {
    "id": "u.id",
    "email": "u.email",
    "nickname": "u.nickname",
    "created_at": "u.created_at",
    "last_active": "a.last_active",
    "library": "library",
    "time": "seconds",
}
DEFAULT_SORT = "id"


@dataclass(frozen=True)
class UserRow:
    id: int
    email: str
    nickname: str | None
    created_at: str
    email_verified_at: str | None
    last_active: str | None
    library: int
    seconds: int  # reading time in the selected period


@dataclass(frozen=True)
class UserPage:
    rows: list[UserRow]
    total: int
    page: int
    pages: int


_FROM = (
    "FROM users u "
    "LEFT JOIN ("
    "  SELECT user_id, MAX(created_at) AS last_active,"
    "  COALESCE(SUM(seconds) FILTER (WHERE kind = 'heartbeat' AND created_at >= %(start)s"
    "    AND created_at < %(end)s), 0) AS seconds"
    "  FROM activity_events GROUP BY user_id"
    ") a ON a.user_id = u.id "
    "LEFT JOIN ("
    "  SELECT user_id, COUNT(*) AS library FROM library_entries GROUP BY user_id"
    ") l ON l.user_id = u.id "
)


def _where(
    query: str, verified: bool | None, active: bool | None, library: bool | None
) -> tuple[str, dict[str, object]]:
    conditions = []
    params: dict[str, object] = {}
    query = query.strip()
    if query:
        # position() rather than LIKE: nothing in the search text acts as a wildcard.
        search = "position(%(q)s in lower(u.email)) > 0 OR position(%(q)s in lower("
        search += "coalesce(u.nickname, ''))) > 0"
        params["q"] = query.lower()
        if query.isdigit() and len(query) <= 9:
            search += " OR u.id = %(qid)s"
            params["qid"] = int(query)
        conditions.append(f"({search})")
    if verified is not None:
        conditions.append(
            "u.email_verified_at IS NOT NULL" if verified else "u.email_verified_at IS NULL"
        )
    if active is not None:
        last = "(a.last_active IS NOT NULL AND a.last_active >= %(start)s)"
        conditions.append(last if active else f"NOT {last}")
    if library is not None:
        conditions.append(
            "COALESCE(l.library, 0) > 0" if library else "COALESCE(l.library, 0) = 0"
        )
    return (f"WHERE {' AND '.join(conditions)} " if conditions else ""), params


async def list_users(
    conn: AsyncConnection,
    *,
    key: str,
    tz: str,
    query: str = "",
    verified: bool | None = None,
    active: bool | None = None,
    library: bool | None = None,
    sort: str = DEFAULT_SORT,
    descending: bool = True,
    page: int = 1,
) -> UserPage:
    """One page of accounts. «Активен за период» and the reading time use period `key`
    in zone `tz`; the search matches email or nickname (case-insensitive substring) or,
    for digits, the id."""
    period = build_period(key, tz)
    where, params = _where(query, verified, active, library)
    params.update(start=period.start.isoformat(), end=period.end.isoformat())
    order = SORTS.get(sort, SORTS[DEFAULT_SORT])
    direction = "DESC" if descending else "ASC"
    async with _read_only(conn):
        cursor = await conn.execute(f"SELECT COUNT(*) AS n {_FROM}{where}", params)
        total = (await cursor.fetchone())["n"]
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(max(page, 1), pages)
        cursor = await conn.execute(
            "SELECT u.id, u.email, u.nickname, u.created_at, u.email_verified_at, "
            "a.last_active, COALESCE(l.library, 0) AS library, "
            f"COALESCE(a.seconds, 0) AS seconds {_FROM}{where}"
            f"ORDER BY {order} {direction} NULLS LAST, u.id DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {**params, "limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE},
        )
        rows = await cursor.fetchall()
    return UserPage(
        rows=[
            UserRow(
                id=row["id"],
                email=row["email"],
                nickname=row["nickname"],
                created_at=row["created_at"],
                email_verified_at=row["email_verified_at"],
                last_active=row["last_active"],
                library=row["library"],
                seconds=int(row["seconds"]),
            )
            for row in rows
        ],
        total=total,
        page=page,
        pages=pages,
    )


@dataclass(frozen=True)
class UserSummary:
    """The drawer's overview of one account: who, since when, and what they've done -
    all time, and the reading for the selected period."""

    id: int
    email: str
    nickname: str | None
    created_at: str
    email_verified_at: str | None
    timezone: str | None
    last_active: str | None
    library: int
    chapters: int
    seconds: int
    chapters_period: int
    seconds_period: int
    downloads: int
    comments: int


async def user_summary(
    conn: AsyncConnection, user_id: int, key: str, tz: str
) -> UserSummary | None:
    period = build_period(key, tz)
    params = {"id": user_id, "start": period.start.isoformat(), "end": period.end.isoformat()}
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT u.id, u.email, u.nickname, u.created_at, u.email_verified_at, u.timezone, "
            "(SELECT MAX(created_at) FROM activity_events WHERE user_id = u.id) AS last_active, "
            "(SELECT COUNT(*) FROM library_entries WHERE user_id = u.id) AS library, "
            "(SELECT COUNT(*) FROM download_history WHERE user_id = u.id) AS downloads, "
            "(SELECT COUNT(*) FROM comments WHERE user_id = u.id) AS comments "
            "FROM users u WHERE u.id = %(id)s",
            params,
        )
        user = await cursor.fetchone()
        if user is None:
            return None
        cursor = await conn.execute(
            "SELECT COUNT(*) FILTER (WHERE kind = 'chapter_read') AS chapters, "
            "COALESCE(SUM(seconds) FILTER (WHERE kind = 'heartbeat'), 0) AS seconds, "
            "COUNT(*) FILTER (WHERE kind = 'chapter_read' AND created_at >= %(start)s "
            "  AND created_at < %(end)s) AS chapters_period, "
            "COALESCE(SUM(seconds) FILTER (WHERE kind = 'heartbeat' "
            "  AND created_at >= %(start)s AND created_at < %(end)s), 0) AS seconds_period "
            "FROM activity_events WHERE user_id = %(id)s",
            params,
        )
        activity = await cursor.fetchone()
    return UserSummary(
        **user,
        chapters=activity["chapters"],
        seconds=int(activity["seconds"]),
        chapters_period=activity["chapters_period"],
        seconds_period=int(activity["seconds_period"]),
    )
