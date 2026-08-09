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

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ChapterReadCount:
    slug_url: str
    chapters_read: int


def record_chapter_read(
    conn: sqlite3.Connection, user_id: int, slug_url: str, volume: str, number: str
) -> None:
    """One row per chapter open - re-reading the same chapter later the same day counts
    again, same as a "recently played" list would. No dedup: this is an activity feed,
    not a read/unread flag (that's `library.last_read_*`, PR 14)."""
    conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, created_at) "
        "VALUES (?, 'chapter_read', ?, ?, ?, ?)",
        (user_id, slug_url, volume, number, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def record_heartbeat(conn: sqlite3.Connection, user_id: int, slug_url: str, seconds: int) -> None:
    conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
        "VALUES (?, 'heartbeat', ?, ?, ?)",
        (user_id, slug_url, seconds, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def list_chapters_read_today(conn: sqlite3.Connection, user_id: int) -> list[ChapterReadCount]:
    """Titles read today, most recently read first."""
    rows = conn.execute(
        "SELECT slug_url, COUNT(*) AS chapters_read FROM activity_events "
        "WHERE user_id = ? AND kind = 'chapter_read' AND created_at >= ? "
        "GROUP BY slug_url ORDER BY MAX(created_at) DESC",
        (user_id, _today_start()),
    ).fetchall()
    return [
        ChapterReadCount(slug_url=row["slug_url"], chapters_read=row["chapters_read"])
        for row in rows
    ]


def total_active_seconds_today(conn: sqlite3.Connection, user_id: int) -> int:
    """Sum of heartbeat ticks today - the "активное время чтения" stat."""
    row = conn.execute(
        "SELECT COALESCE(SUM(seconds), 0) AS total FROM activity_events "
        "WHERE user_id = ? AND kind = 'heartbeat' AND created_at >= ?",
        (user_id, _today_start()),
    ).fetchone()
    return row["total"]


def _today_start() -> str:
    return datetime.now(UTC).date().isoformat()
