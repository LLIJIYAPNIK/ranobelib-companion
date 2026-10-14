"""Account blocks, ``user_bans`` (migrations/0030_user_bans.sql, PR 349).

Set and lifted by the admin (app/api/admin.py); enforced at login (app/api/auth.py) and
on every request (``get_current_user()`` in app/auth/dependencies.py), which turns a
blocked account into a guest. A block with a term simply stops being active when it
runs out - ``active_ban()`` compares ``expires_at`` with now; nothing has to run.

At most one active block per account: ``ban_user()`` refuses a second rather than
stacking them, so «Снять блокировку» always means one row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg import AsyncConnection

# The terms the admin picks from -> days (None: until lifted).
BAN_TERMS: dict[str, int | None] = {"1d": 1, "7d": 7, "30d": 30, "forever": None}
REASON_LIMIT = 500


@dataclass(frozen=True)
class Ban:
    id: int
    user_id: int
    reason: str | None
    created_at: str
    expires_at: str | None
    lifted_at: str | None
    lifted_reason: str | None

    @property
    def permanent(self) -> bool:
        return self.expires_at is None


class AlreadyBanned(Exception):
    """The account already has an active block - lift it first."""


_COLUMNS = "id, user_id, reason, created_at, expires_at, lifted_at, lifted_reason"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def active_ban(
    conn: AsyncConnection, user_id: int, now: datetime | None = None
) -> Ban | None:
    """The block in force right now, if any: not lifted, and with no end or one still
    ahead. (ISO-8601 UTC strings compare in time order, see app/timezones.py.)"""
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM user_bans WHERE user_id = %s AND lifted_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > %s) ORDER BY id DESC LIMIT 1",
        (user_id, _now(now).isoformat()),
    )
    row = await cursor.fetchone()
    return Ban(**row) if row else None


async def ban_user(
    conn: AsyncConnection,
    user_id: int,
    *,
    term: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> Ban:
    """Blocks the account for `term` (a key of BAN_TERMS). `reason` is shown to the
    person at login and on the block notice - empty means none. Raises AlreadyBanned if
    a block is already in force, KeyError for an unknown term."""
    days = BAN_TERMS[term]
    at = _now(now)
    if await active_ban(conn, user_id, at) is not None:
        raise AlreadyBanned
    reason = (reason or "").strip()[:REASON_LIMIT] or None
    expires = (at + timedelta(days=days)).isoformat() if days is not None else None
    cursor = await conn.execute(
        "INSERT INTO user_bans (user_id, reason, created_at, expires_at) "
        f"VALUES (%s, %s, %s, %s) RETURNING {_COLUMNS}",
        (user_id, reason, at.isoformat(), expires),
    )
    return Ban(**await cursor.fetchone())


async def lift_ban(
    conn: AsyncConnection,
    user_id: int,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> Ban | None:
    """Ends the active block now, with an optional note why. None if there was none
    (already lifted, or its term ran out)."""
    at = _now(now)
    ban = await active_ban(conn, user_id, at)
    if ban is None:
        return None
    cursor = await conn.execute(
        "UPDATE user_bans SET lifted_at = %s, lifted_reason = %s "
        f"WHERE id = %s AND lifted_at IS NULL RETURNING {_COLUMNS}",
        (at.isoformat(), (reason or "").strip()[:REASON_LIMIT] or None, ban.id),
    )
    row = await cursor.fetchone()
    return Ban(**row) if row else None


async def ban_history(conn: AsyncConnection, user_id: int) -> list[Ban]:
    """Every block the account ever had, newest first."""
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM user_bans WHERE user_id = %s ORDER BY id DESC", (user_id,)
    )
    return [Ban(**row) for row in await cursor.fetchall()]
