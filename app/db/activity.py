"""Access to the ``activity_events`` table (see migrations/0004_activity_events.sql).

Two event kinds land here: "chapter_read" (one row per chapter opened, written from
app/api/chapters.py alongside record_progress()) and "heartbeat" (a rolling tally of
active reading seconds, written from POST /activity/heartbeat while a chapter page stays
open and visible - see app/api/activity.py). The third signal the "Активность" section
needs - downloads - isn't duplicated here: it already lives in ``download_history`` from
PR 17 (see app/db/downloads.py), so the aggregation in app/api/activity.py reads that
table directly instead of re-recording the same event under a different name.

"Today" and every per-day bucket below are the user's own calendar day (PR 322): each
function takes the user's IANA zone name (`tz`, see app/timezones.py - UTC when unset,
the pre-PR 322 behavior). created_at itself stays a UTC ISO timestamp; only the day
boundaries are computed in `tz`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from psycopg import AsyncConnection

from app.timezones import DEFAULT_TIMEZONE, LOCAL_DAY_SQL, day_start_utc, local_today


@dataclass(frozen=True)
class ChapterReadCount:
    slug_url: str
    chapters_read: int


@dataclass(frozen=True)
class ChapterReadEvent:
    slug_url: str
    volume: str | None
    number: str | None
    created_at: str


async def record_chapter_read(
    conn: AsyncConnection, user_id: int, slug_url: str, volume: str, number: str
) -> None:
    """One row per chapter open - re-reading the same chapter later the same day counts
    again, same as a "recently played" list would. No dedup: this is an activity feed,
    not a read/unread flag (that's `library.last_read_*`, PR 14)."""
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, created_at) "
        "VALUES (%s, 'chapter_read', %s, %s, %s, %s)",
        (user_id, slug_url, volume, number, datetime.now(UTC).isoformat()),
    )


async def record_heartbeat(
    conn: AsyncConnection,
    user_id: int,
    slug_url: str,
    seconds: int,
    event_id: str | None = None,
    created_at: str | None = None,
) -> None:
    """PR 332: `event_id` - the device queue's id for this tick - makes a resend a no-op
    (the second copy of a request whose answer was lost), so seconds aren't counted
    twice; `created_at` - when it was read, for a tick that waited offline."""
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at, event_id) "
        "VALUES (%s, 'heartbeat', %s, %s, %s, %s) "
        "ON CONFLICT (user_id, event_id) DO NOTHING",
        (user_id, slug_url, seconds, created_at or datetime.now(UTC).isoformat(), event_id),
    )


async def list_chapters_read_today(
    conn: AsyncConnection, user_id: int, tz: str = DEFAULT_TIMEZONE
) -> list[ChapterReadCount]:
    """Titles read today, most recently read first."""
    cursor = await conn.execute(
        "SELECT slug_url, COUNT(*) AS chapters_read FROM activity_events "
        "WHERE user_id = %s AND kind = 'chapter_read' AND created_at >= %s "
        "GROUP BY slug_url ORDER BY MAX(created_at) DESC",
        (user_id, _today_start(tz)),
    )
    rows = await cursor.fetchall()
    return [
        ChapterReadCount(slug_url=row["slug_url"], chapters_read=row["chapters_read"])
        for row in rows
    ]


async def total_active_seconds_today(
    conn: AsyncConnection, user_id: int, tz: str = DEFAULT_TIMEZONE
) -> int:
    """Sum of heartbeat ticks today - the "активное время чтения" stat."""
    cursor = await conn.execute(
        "SELECT COALESCE(SUM(seconds), 0) AS total FROM activity_events "
        "WHERE user_id = %s AND kind = 'heartbeat' AND created_at >= %s",
        (user_id, _today_start(tz)),
    )
    row = await cursor.fetchone()
    return row["total"]


async def daily_reading_activity(
    conn: AsyncConnection, user_id: int, weeks: int = 52, tz: str = DEFAULT_TIMEZONE
) -> dict[str, int]:
    """Chapters read per calendar day (in `tz`, same boundary every other "day" query in
    this module uses) for the trailing `weeks` weeks up to and including today - PR 136's
    profile heatmap. A day with no chapter_read events at all is simply absent from the
    returned dict rather than present with 0 - the caller (app/api/profile.py) fills in
    every day of the grid it renders, including ones with no data here, from this."""
    cursor = await conn.execute(
        # PR 322: the local day of created_at (ISO-8601 TEXT, see LOCAL_DAY_SQL) rather
        # than its first 10 characters, which were the UTC date.
        f"SELECT {LOCAL_DAY_SQL} AS day, COUNT(*) AS n FROM activity_events "
        "WHERE user_id = %s AND kind = 'chapter_read' AND created_at >= %s "
        "GROUP BY day",
        (tz, user_id, _window_start(weeks, tz)),
    )
    rows = await cursor.fetchall()
    return {row["day"]: row["n"] for row in rows}


async def daily_active_seconds(
    conn: AsyncConnection, user_id: int, weeks: int = 52, tz: str = DEFAULT_TIMEZONE
) -> dict[str, int]:
    """Active reading seconds (heartbeat ticks, same signal total_active_seconds_today()
    sums for "today" alone) per calendar day, for the same trailing `weeks` weeks/`tz`
    boundary as daily_reading_activity() right above - PR 140's addition to the profile
    heatmap's tooltip, which otherwise only ever showed a chapter count with no sense of
    how long that reading actually took. Same "day with nothing at all is absent, not
    present with 0" convention as daily_reading_activity()."""
    cursor = await conn.execute(
        f"SELECT {LOCAL_DAY_SQL} AS day, SUM(seconds) AS total FROM activity_events "
        "WHERE user_id = %s AND kind = 'heartbeat' AND created_at >= %s "
        "GROUP BY day",
        (tz, user_id, _window_start(weeks, tz)),
    )
    rows = await cursor.fetchall()
    return {row["day"]: row["total"] for row in rows}


async def daily_titles_read(
    conn: AsyncConnection, user_id: int, weeks: int = 52, tz: str = DEFAULT_TIMEZONE
) -> dict[str, list[str]]:
    """Which titles (unique slug_url) were read on each calendar day, most recently read
    within that day first - PR 159's addition to the profile heatmap tooltip, which
    otherwise only ever said *how much* was read on a given day, never *what*. Same
    trailing `weeks`/`tz`-day boundary as daily_reading_activity() above; a day with no
    chapter_read events at all is simply absent, same convention as that function too.
    Display names aren't resolved here - slug_url is app data, a title's name is SDK data
    (see app/db/library.py's own docstring on this split), so the caller
    (app/api/profile.py) looks those up itself through app/services/client.py."""
    cursor = await conn.execute(
        f"SELECT {LOCAL_DAY_SQL} AS day, slug_url, MAX(created_at) AS last_read "
        "FROM activity_events WHERE user_id = %s AND kind = 'chapter_read' AND created_at >= %s "
        "GROUP BY day, slug_url ORDER BY day, last_read DESC",
        (tz, user_id, _window_start(weeks, tz)),
    )
    rows = await cursor.fetchall()
    titles: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        titles[row["day"]].append(row["slug_url"])
    return dict(titles)


async def list_recent_chapter_reads(
    conn: AsyncConnection, user_id: int, limit: int = 10
) -> list[ChapterReadEvent]:
    """The latest chapter_read events, newest first - the reading half of the
    "История событий" feed on the Активность page (PR 276). Unlike the per-day
    aggregates above, this keeps each event's own volume/number/timestamp."""
    cursor = await conn.execute(
        "SELECT slug_url, volume, number, created_at FROM activity_events "
        "WHERE user_id = %s AND kind = 'chapter_read' "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (user_id, limit),
    )
    rows = await cursor.fetchall()
    return [
        ChapterReadEvent(
            slug_url=row["slug_url"],
            volume=row["volume"],
            number=row["number"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


@dataclass(frozen=True)
class TitleDay:
    """One title on one local day: chapters opened and reading seconds (heartbeat ticks)
    spent in it - a day of «Активность»'s month calendar, what was read that day."""

    slug_url: str
    chapters: int
    seconds: int


async def daily_title_activity(
    conn: AsyncConnection, user_id: int, start: date, end: date, tz: str = DEFAULT_TIMEZONE
) -> dict[str, list[TitleDay]]:
    """Per local day from `start` to `end` inclusive (in `tz`), the titles read that day -
    chapters and reading seconds each, the most recently read first. A day with nothing is
    absent, as in daily_reading_activity(). One query for the whole range: both event kinds
    carry slug_url, so a title read only by time (a long chapter, no new one opened) shows
    too."""
    cursor = await conn.execute(
        f"SELECT {LOCAL_DAY_SQL} AS day, slug_url, "
        "COUNT(*) FILTER (WHERE kind = 'chapter_read') AS chapters, "
        "COALESCE(SUM(seconds) FILTER (WHERE kind = 'heartbeat'), 0) AS seconds, "
        "MAX(created_at) AS last_at "
        "FROM activity_events WHERE user_id = %s AND created_at >= %s AND created_at < %s "
        "GROUP BY day, slug_url ORDER BY day, last_at DESC",
        (tz, user_id, day_start_utc(start, tz), day_start_utc(end + timedelta(days=1), tz)),
    )
    rows = await cursor.fetchall()
    days: dict[str, list[TitleDay]] = defaultdict(list)
    for row in rows:
        if row["chapters"] or row["seconds"]:
            days[row["day"]].append(
                TitleDay(
                    slug_url=row["slug_url"],
                    chapters=row["chapters"],
                    seconds=int(row["seconds"]),
                )
            )
    return dict(days)


@dataclass(frozen=True)
class SiteTitleReaders:
    slug_url: str
    readers: int
    chapters: int


async def site_top_titles(
    conn: AsyncConnection, since: str, limit: int = 6
) -> list[SiteTitleReaders]:
    """The titles the most different people opened a chapter of since `since` (a UTC ISO
    timestamp), across every account - «Читают на сайте» on «Активность». Only counts:
    who read what stays private. Ties go to more chapters, then slug_url, so the order is
    stable."""
    cursor = await conn.execute(
        "SELECT slug_url, COUNT(DISTINCT user_id) AS readers, COUNT(*) AS chapters "
        "FROM activity_events WHERE kind = 'chapter_read' AND created_at >= %s "
        "GROUP BY slug_url ORDER BY readers DESC, chapters DESC, slug_url LIMIT %s",
        (since, limit),
    )
    rows = await cursor.fetchall()
    return [
        SiteTitleReaders(slug_url=row["slug_url"], readers=row["readers"], chapters=row["chapters"])
        for row in rows
    ]


async def last_chapter_read_at(conn: AsyncConnection, user_ids: list[int]) -> dict[int, str]:
    """When each of these users last opened a chapter (UTC ISO), keyed by user_id - one
    query for a whole friends list. A user who never read anything is absent."""
    if not user_ids:
        return {}
    cursor = await conn.execute(
        "SELECT user_id, MAX(created_at) AS last_at FROM activity_events "
        "WHERE kind = 'chapter_read' AND user_id = ANY(%s) GROUP BY user_id",
        (user_ids,),
    )
    rows = await cursor.fetchall()
    return {row["user_id"]: row["last_at"] for row in rows}


def _today_start(tz: str) -> str:
    return day_start_utc(local_today(tz), tz)


def _window_start(weeks: int, tz: str) -> str:
    """UTC timestamp of the local midnight `weeks` weeks back, today included."""
    return day_start_utc(local_today(tz) - timedelta(days=weeks * 7 - 1), tz)


def reading_streak_days(daily_counts: dict[str, int], tz: str = DEFAULT_TIMEZONE) -> int:
    """Consecutive days with at least one chapter read, counting backward from today - or
    from yesterday if today doesn't have a chapter yet, so an existing streak doesn't
    briefly read as 0 just because the day has rolled over before today's first
    chapter. `tz` must be the zone `daily_counts` was bucketed in. Stops at the first day
    with nothing. Pure - operates on daily_reading_activity()'s own returned dict
    (PR 200's "N дней подряд" friend-activity stat), issuing no query of its own."""
    today = local_today(tz)
    current = today if daily_counts.get(today.isoformat(), 0) > 0 else today - timedelta(days=1)
    streak = 0
    while daily_counts.get(current.isoformat(), 0) > 0:
        streak += 1
        current -= timedelta(days=1)
    return streak
