"""The user's own calendar day for activity stats (PR 322).

Every ``created_at`` stays a UTC ISO-8601 string. Only the day boundaries move: "today",
the streak and the per-day buckets follow ``users.timezone`` (an IANA name), falling back
to UTC - the pre-PR 322 behavior - when it's unset or no longer known.

Two helpers cover every query in app/db/activity.py and app/db/downloads.py:

- ``day_start_utc(day, tz)`` - the UTC ISO timestamp of that local date's midnight, for
  ``created_at >= %s`` comparisons (string order = time order for these timestamps, so
  the existing comparisons keep working unchanged, just with a different boundary);
- ``LOCAL_DAY_SQL`` - the SQL expression for the local YYYY-MM-DD of ``created_at``,
  taking the zone name as a parameter, for GROUP BY day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

from psycopg import AsyncConnection

DEFAULT_TIMEZONE = "UTC"

# created_at is TEXT (ISO-8601 with +00:00); timestamptz -> local wall time -> date.
LOCAL_DAY_SQL = "to_char((created_at::timestamptz AT TIME ZONE %s), 'YYYY-MM-DD')"


@cache
def _python_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def timezone_choices() -> list[str]:
    """Every IANA zone Python knows, sorted - the manual picker's options."""
    return sorted(_python_zones())


async def is_valid_timezone(conn: AsyncConnection, name: str) -> bool:
    """Known to both sides that use it: Python (today's date) and Postgres (AT TIME ZONE
    in the day queries). A name only one of them knows would 500 every activity page."""
    if name not in _python_zones():
        return False
    cursor = await conn.execute("SELECT 1 FROM pg_timezone_names WHERE name = %s", (name,))
    return await cursor.fetchone() is not None


def user_timezone(name: str | None) -> str:
    """The zone name to use for a user's `timezone` column value."""
    if name and name in _python_zones():
        return name
    return DEFAULT_TIMEZONE


def local_today(tz: str = DEFAULT_TIMEZONE) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def day_start_utc(day: date, tz: str = DEFAULT_TIMEZONE) -> str:
    """UTC ISO timestamp of `day`'s local midnight in `tz`."""
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(tz)).astimezone(UTC).isoformat()


def local_date(timestamp: str, tz: str = DEFAULT_TIMEZONE) -> date:
    """The local calendar date of a stored UTC ISO timestamp."""
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo(tz)).date()
