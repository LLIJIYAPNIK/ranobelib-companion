"""Access to the ``activity_events`` table (see migrations/0004_activity_events.sql).

Two event kinds land here: "chapter_read" (one row per chapter opened, written from
app/api/chapters.py alongside record_progress()) and "heartbeat" (a rolling tally of
active reading seconds, written from POST /activity/heartbeat while a chapter page stays
open and visible - see app/api/activity.py). The third signal the "Активность" section
needs - downloads - isn't duplicated here: it already lives in ``download_history`` from
PR 17 (see app/db/downloads.py), so the aggregation in app/api/activity.py reads that
table directly instead of re-recording the same event under a different name.

"Today" everywhere below means the UTC calendar date, matching the UTC timestamps these
rows (and download_history's) are written with.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg import Connection


@dataclass(frozen=True)
class ChapterReadCount:
    slug_url: str
    chapters_read: int


def record_chapter_read(
    conn: Connection, user_id: int, slug_url: str, volume: str, number: str
) -> None:
    """One row per chapter open - re-reading the same chapter later the same day counts
    again, same as a "recently played" list would. No dedup: this is an activity feed,
    not a read/unread flag (that's `library.last_read_*`, PR 14)."""
    conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, created_at) "
        "VALUES (%s, 'chapter_read', %s, %s, %s, %s)",
        (user_id, slug_url, volume, number, datetime.now(UTC).isoformat()),
    )


def record_heartbeat(conn: Connection, user_id: int, slug_url: str, seconds: int) -> None:
    conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
        "VALUES (%s, 'heartbeat', %s, %s, %s)",
        (user_id, slug_url, seconds, datetime.now(UTC).isoformat()),
    )


def list_chapters_read_today(conn: Connection, user_id: int) -> list[ChapterReadCount]:
    """Titles read today, most recently read first."""
    rows = conn.execute(
        "SELECT slug_url, COUNT(*) AS chapters_read FROM activity_events "
        "WHERE user_id = %s AND kind = 'chapter_read' AND created_at >= %s "
        "GROUP BY slug_url ORDER BY MAX(created_at) DESC",
        (user_id, _today_start()),
    ).fetchall()
    return [
        ChapterReadCount(slug_url=row["slug_url"], chapters_read=row["chapters_read"])
        for row in rows
    ]


def total_active_seconds_today(conn: Connection, user_id: int) -> int:
    """Sum of heartbeat ticks today - the "активное время чтения" stat."""
    row = conn.execute(
        "SELECT COALESCE(SUM(seconds), 0) AS total FROM activity_events "
        "WHERE user_id = %s AND kind = 'heartbeat' AND created_at >= %s",
        (user_id, _today_start()),
    ).fetchone()
    return row["total"]


def daily_reading_activity(conn: Connection, user_id: int, weeks: int = 52) -> dict[str, int]:
    """Chapters read per calendar day (UTC, same boundary every other "day" query in this
    module uses) for the trailing `weeks` weeks up to and including today - PR 136's
    profile heatmap. A day with no chapter_read events at all is simply absent from the
    returned dict rather than present with 0 - the caller (app/api/profile.py) fills in
    every day of the grid it renders, including ones with no data here, from this."""
    start = (datetime.now(UTC).date() - timedelta(days=weeks * 7 - 1)).isoformat()
    rows = conn.execute(
        # LEFT(created_at, 10) rather than SQLite's date(created_at): created_at is stored
        # as an ISO-8601 TEXT column (see CLAUDE.md's PR 191 entry on what did/didn't get
        # rewritten to native types), and Postgres has no date(text) function to call on
        # it directly the way SQLite does - but since every timestamp is written with
        # datetime.isoformat(), its first 10 characters are always exactly its YYYY-MM-DD
        # date, identical to what date() used to return.
        "SELECT LEFT(created_at, 10) AS day, COUNT(*) AS n FROM activity_events "
        "WHERE user_id = %s AND kind = 'chapter_read' AND created_at >= %s "
        "GROUP BY day",
        (user_id, start),
    ).fetchall()
    return {row["day"]: row["n"] for row in rows}


def daily_active_seconds(conn: Connection, user_id: int, weeks: int = 52) -> dict[str, int]:
    """Active reading seconds (heartbeat ticks, same signal total_active_seconds_today()
    sums for "today" alone) per calendar day, for the same trailing `weeks` weeks/UTC
    boundary as daily_reading_activity() right above - PR 140's addition to the profile
    heatmap's tooltip, which otherwise only ever showed a chapter count with no sense of
    how long that reading actually took. Same "day with nothing at all is absent, not
    present with 0" convention as daily_reading_activity()."""
    start = (datetime.now(UTC).date() - timedelta(days=weeks * 7 - 1)).isoformat()
    rows = conn.execute(
        "SELECT LEFT(created_at, 10) AS day, SUM(seconds) AS total FROM activity_events "
        "WHERE user_id = %s AND kind = 'heartbeat' AND created_at >= %s "
        "GROUP BY day",
        (user_id, start),
    ).fetchall()
    return {row["day"]: row["total"] for row in rows}


def daily_titles_read(
    conn: Connection, user_id: int, weeks: int = 52
) -> dict[str, list[str]]:
    """Which titles (unique slug_url) were read on each calendar day, most recently read
    within that day first - PR 159's addition to the profile heatmap tooltip, which
    otherwise only ever said *how much* was read on a given day, never *what*. Same
    trailing `weeks`/UTC-day boundary as daily_reading_activity() above; a day with no
    chapter_read events at all is simply absent, same convention as that function too.
    Display names aren't resolved here - slug_url is app data, a title's name is SDK data
    (see app/db/library.py's own docstring on this split), so the caller
    (app/api/profile.py) looks those up itself through app/services/client.py."""
    start = (datetime.now(UTC).date() - timedelta(days=weeks * 7 - 1)).isoformat()
    rows = conn.execute(
        "SELECT LEFT(created_at, 10) AS day, slug_url, MAX(created_at) AS last_read "
        "FROM activity_events WHERE user_id = %s AND kind = 'chapter_read' AND created_at >= %s "
        "GROUP BY day, slug_url ORDER BY day, last_read DESC",
        (user_id, start),
    ).fetchall()
    titles: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        titles[row["day"]].append(row["slug_url"])
    return dict(titles)


def _today_start() -> str:
    return datetime.now(UTC).date().isoformat()
