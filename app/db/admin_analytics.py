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
from app.db.admin_stats import Comparison, build_period, cached, compare

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
