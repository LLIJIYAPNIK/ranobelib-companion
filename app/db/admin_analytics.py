"""«Аналитика» (PR 346): per-title numbers for the admin panel - the titles list and a
title's chapter-by-chapter retention. Built on the metrics layer (app/db/admin_stats.py):
the same periods, read-only transactions with the admin timeout, and short cache.

Only application data lives here - who read which chapter (``activity_events``), who
added what (``library_entries``). A title's name, cover and chapter order are SDK data;
the caller puts these numbers onto the SDK's table of contents (app/admin_analytics.py),
never sorting chapter numbers itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection

from app.db.admin_data import _read_only
from app.db.admin_stats import Comparison, build_period, cached, compare, last_days

# The list shows the most active titles, not every title ever opened.
TITLES_LIMIT = 50

# Sort keys the list accepts -> the SQL column they order by (a whitelist: nothing from
# the request reaches the query otherwise).
SORTS = {
    "readers": "readers",
    "chapters": "chapters",
    "library": "library_adds",
    "time": "seconds",
}
DEFAULT_SORT = "readers"


@dataclass(frozen=True)
class TitleRow:
    """One title's activity in a period: unique readers (and in the previous period, for
    the trend), chapters opened, library adds and seconds of reading."""

    slug_url: str
    readers: int
    previous_readers: int
    chapters: int
    library_adds: int
    seconds: int

    @property
    def comparison(self) -> Comparison:
        return compare(self.readers, self.previous_readers)


async def title_rows(
    conn: AsyncConnection,
    key: str,
    tz: str,
    sort: str = DEFAULT_SORT,
    descending: bool = True,
    limit: int = TITLES_LIMIT,
    now: datetime | None = None,
) -> list[TitleRow]:
    """The titles with any reading or library adds in period `key`, ordered by `sort`
    (a key of SORTS; anything else is DEFAULT_SORT), at most `limit` of them."""
    column = SORTS.get(sort, SORTS[DEFAULT_SORT])
    direction = "DESC" if descending else "ASC"
    period = build_period(key, tz, now)
    previous = period.previous()
    async with _read_only(conn):
        cursor = await conn.execute(
            "WITH activity AS ("
            "  SELECT slug_url,"
            "  COUNT(DISTINCT user_id) FILTER (WHERE kind = 'chapter_read' "
            "    AND created_at >= %(split)s) AS readers,"
            "  COUNT(DISTINCT user_id) FILTER (WHERE kind = 'chapter_read' "
            "    AND created_at < %(split)s) AS previous_readers,"
            "  COUNT(*) FILTER (WHERE kind = 'chapter_read' AND created_at >= %(split)s) "
            "    AS chapters,"
            "  COALESCE(SUM(seconds) FILTER (WHERE kind = 'heartbeat' "
            "    AND created_at >= %(split)s), 0) AS seconds"
            "  FROM activity_events WHERE kind IN ('chapter_read', 'heartbeat')"
            "  AND created_at >= %(start)s AND created_at < %(end)s GROUP BY slug_url"
            "), added AS ("
            "  SELECT slug_url, COUNT(*) AS library_adds FROM library_entries"
            "  WHERE added_at >= %(split)s AND added_at < %(end)s GROUP BY slug_url"
            ") "
            "SELECT * FROM ("
            "  SELECT COALESCE(a.slug_url, l.slug_url) AS slug_url,"
            "  COALESCE(a.readers, 0) AS readers,"
            "  COALESCE(a.previous_readers, 0) AS previous_readers,"
            "  COALESCE(a.chapters, 0) AS chapters,"
            "  COALESCE(l.library_adds, 0) AS library_adds,"
            "  COALESCE(a.seconds, 0) AS seconds"
            "  FROM activity a FULL JOIN added l ON l.slug_url = a.slug_url"
            ") t WHERE readers + library_adds + seconds > 0 "
            f"ORDER BY {column} {direction}, readers DESC, slug_url LIMIT %(limit)s",
            {
                "split": period.start.isoformat(),
                "start": previous.start.isoformat(),
                "end": period.end.isoformat(),
                "limit": limit,
            },
        )
        rows = await cursor.fetchall()
    return [
        TitleRow(
            slug_url=row["slug_url"],
            readers=row["readers"],
            previous_readers=row["previous_readers"],
            chapters=row["chapters"],
            library_adds=row["library_adds"],
            seconds=int(row["seconds"]),
        )
        for row in rows
    ]


async def cached_title_rows(
    conn: AsyncConnection, key: str, tz: str, sort: str, descending: bool
) -> list[TitleRow]:
    return await cached(
        ("titles", key, tz, sort, str(descending)),
        lambda: title_rows(conn, key, tz, sort, descending),
    )


@dataclass(frozen=True)
class TitleTotals:
    """One title's numbers for a period against the previous one."""

    readers: Comparison
    chapters: Comparison
    seconds: Comparison
    library_adds: Comparison


async def title_totals(
    conn: AsyncConnection, slug_url: str, key: str, tz: str, now: datetime | None = None
) -> TitleTotals:
    period = build_period(key, tz, now)
    previous = period.previous()
    params = {
        "slug": slug_url,
        "split": period.start.isoformat(),
        "start": previous.start.isoformat(),
        "end": period.end.isoformat(),
    }
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT "
            "COUNT(DISTINCT user_id) FILTER (WHERE kind = 'chapter_read' "
            "  AND created_at >= %(split)s) AS readers, "
            "COUNT(DISTINCT user_id) FILTER (WHERE kind = 'chapter_read' "
            "  AND created_at < %(split)s) AS previous_readers, "
            "COUNT(*) FILTER (WHERE kind = 'chapter_read' AND created_at >= %(split)s) "
            "  AS chapters, "
            "COUNT(*) FILTER (WHERE kind = 'chapter_read' AND created_at < %(split)s) "
            "  AS previous_chapters, "
            "COALESCE(SUM(seconds) FILTER (WHERE kind = 'heartbeat' "
            "  AND created_at >= %(split)s), 0) AS seconds, "
            "COALESCE(SUM(seconds) FILTER (WHERE kind = 'heartbeat' "
            "  AND created_at < %(split)s), 0) AS previous_seconds "
            "FROM activity_events WHERE slug_url = %(slug)s "
            "AND kind IN ('chapter_read', 'heartbeat') "
            "AND created_at >= %(start)s AND created_at < %(end)s",
            params,
        )
        activity = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT COUNT(*) FILTER (WHERE added_at >= %(split)s) AS adds, "
            "COUNT(*) FILTER (WHERE added_at < %(split)s) AS previous_adds "
            "FROM library_entries WHERE slug_url = %(slug)s "
            "AND added_at >= %(start)s AND added_at < %(end)s",
            params,
        )
        library = await cursor.fetchone()
    return TitleTotals(
        readers=compare(activity["readers"], activity["previous_readers"]),
        chapters=compare(activity["chapters"], activity["previous_chapters"]),
        seconds=compare(int(activity["seconds"]), int(activity["previous_seconds"])),
        library_adds=compare(library["adds"], library["previous_adds"]),
    )


@dataclass(frozen=True)
class ChapterReach:
    """Retention for one title, over all time: ``cohort`` is everyone who opened its first
    chapter, ``reached`` how many of them opened each (volume, number) - only chapters
    someone of the cohort opened are present."""

    cohort: int
    reached: dict[tuple[str, str], int]


async def chapter_reach(
    conn: AsyncConnection, slug_url: str, first: tuple[str, str]
) -> ChapterReach:
    """`first` is the first chapter of the SDK's table of contents - which chapter is
    "first" is the SDK's call, never a sort of chapter numbers here."""
    async with _read_only(conn):
        cursor = await conn.execute(
            "WITH cohort AS ("
            "  SELECT DISTINCT user_id FROM activity_events"
            "  WHERE slug_url = %(slug)s AND kind = 'chapter_read'"
            "  AND volume = %(volume)s AND number = %(number)s"
            ") "
            "SELECT e.volume, e.number, COUNT(DISTINCT e.user_id) AS readers "
            "FROM activity_events e JOIN cohort c ON c.user_id = e.user_id "
            "WHERE e.slug_url = %(slug)s AND e.kind = 'chapter_read' "
            "GROUP BY e.volume, e.number",
            {"slug": slug_url, "volume": first[0], "number": first[1]},
        )
        rows = await cursor.fetchall()
    reached = {(row["volume"], row["number"]): row["readers"] for row in rows}
    return ChapterReach(cohort=reached.get(first, 0), reached=reached)


# --- «Поведение читателей» (PR 347) -------------------------------------------------------


async def reading_heatmap(
    conn: AsyncConnection, key: str, tz: str, now: datetime | None = None
) -> list[list[int]]:
    """Chapters opened in period `key` by local weekday (Monday first) and hour - a 7 x 24
    grid, zero where nobody read. `tz` must be a zone Postgres knows too (the caller
    checks: app/timezones.py is_valid_timezone())."""
    period = build_period(key, tz, now)
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT EXTRACT(ISODOW FROM local) AS weekday, EXTRACT(HOUR FROM local) AS hour, "
            "COUNT(*) AS n FROM ("
            "  SELECT created_at::timestamptz AT TIME ZONE %(tz)s AS local FROM activity_events"
            "  WHERE kind = 'chapter_read' AND created_at >= %(start)s AND created_at < %(end)s"
            ") e GROUP BY weekday, hour",
            {"tz": tz, "start": period.start.isoformat(), "end": period.end.isoformat()},
        )
        rows = await cursor.fetchall()
    grid = [[0] * 24 for _ in range(7)]
    for row in rows:
        grid[int(row["weekday"]) - 1][int(row["hour"])] = row["n"]
    return grid


@dataclass(frozen=True)
class DayVisitors:
    """One local day's active users (any activity event): ``new`` registered that same
    day, the rest came back."""

    day: str  # YYYY-MM-DD on the admin's clock
    new: int
    returning: int


NEW_AND_RETURNING_DAYS = 14


async def new_and_returning(
    conn: AsyncConnection, tz: str, days: int = NEW_AND_RETURNING_DAYS, now: datetime | None = None
) -> list[DayVisitors]:
    """Every one of the last `days` local days, oldest first. A user active on a day is
    new if that day is the local day they registered, otherwise returning."""
    period = last_days(days, tz, now)
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT day, COUNT(*) FILTER (WHERE day = registered) AS new, "
            "COUNT(*) FILTER (WHERE day <> registered) AS returning FROM ("
            "  SELECT DISTINCT e.user_id,"
            "  to_char(e.created_at::timestamptz AT TIME ZONE %(tz)s, 'YYYY-MM-DD') AS day,"
            "  to_char(u.created_at::timestamptz AT TIME ZONE %(tz)s, 'YYYY-MM-DD') AS registered"
            "  FROM activity_events e JOIN users u ON u.id = e.user_id"
            "  WHERE e.kind IN ('chapter_read', 'heartbeat')"
            "  AND e.created_at >= %(start)s AND e.created_at < %(end)s"
            ") active GROUP BY day",
            {"tz": tz, "start": period.start.isoformat(), "end": period.end.isoformat()},
        )
        rows = {row["day"]: row for row in await cursor.fetchall()}
    result = []
    for label in period.labels:
        day = label.date().isoformat()
        row = rows.get(day)
        result.append(DayVisitors(day, row["new"] if row else 0, row["returning"] if row else 0))
    return result


async def last_read_positions(
    conn: AsyncConnection, slugs: list[str]
) -> dict[str, list[tuple[str, str]]]:
    """Each title's library entries that have a reading position - (volume, number) of
    the last chapter read - for the share who finished it (app/admin_analytics.py)."""
    if not slugs:
        return {}
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT slug_url, last_read_volume, last_read_number FROM library_entries "
            "WHERE slug_url = ANY(%s) AND last_read_volume IS NOT NULL "
            "AND last_read_number IS NOT NULL",
            (slugs,),
        )
        rows = await cursor.fetchall()
    positions: dict[str, list[tuple[str, str]]] = {slug: [] for slug in slugs}
    for row in rows:
        positions[row["slug_url"]].append((row["last_read_volume"], row["last_read_number"]))
    return positions
