"""Access to the ``download_history`` table (see migrations/0003_download_history.sql).

One row per finished (or failed) whole-title download - written once a `DownloadJob`
reaches a terminal state (see app/jobs/download.py). The in-memory job itself (still
needed to serve the exported file, see app/jobs/store.py) is a separate concern from this
permanent per-user record.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class DownloadHistoryEntry:
    id: int
    user_id: int
    slug_url: str
    fmt: str
    status: str
    chapter_count: int | None
    error: str | None
    finished_at: str


def record_download(
    conn: sqlite3.Connection,
    user_id: int,
    slug_url: str,
    fmt: str,
    status: str,
    chapter_count: int | None,
    error: str | None,
) -> None:
    conn.execute(
        "INSERT INTO download_history "
        "(user_id, slug_url, fmt, status, chapter_count, error, finished_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, slug_url, fmt, status, chapter_count, error, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def delete_entry(conn: sqlite3.Connection, entry_id: int, user_id: int) -> bool:
    """True if a row was actually deleted. Scoping the DELETE by `user_id` as well as
    `id` means an id that exists but belongs to another user deletes nothing and reports
    back the same as an id that doesn't exist at all - the caller turns both into a 404,
    not a 403, so a visitor can't use this to probe which entry ids belong to someone
    else (same reasoning as `_get_job_or_404` in app/api/downloads.py)."""
    cursor = conn.execute(
        "DELETE FROM download_history WHERE id = ? AND user_id = ?",
        (entry_id, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_download_history(
    conn: sqlite3.Connection, user_id: int, limit: int = 20
) -> list[DownloadHistoryEntry]:
    """Most recently finished first."""
    rows = conn.execute(
        "SELECT * FROM download_history WHERE user_id = ? "
        "ORDER BY finished_at DESC, id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [_row_to_entry(row) for row in rows]


def list_download_history_today(
    conn: sqlite3.Connection, user_id: int
) -> list[DownloadHistoryEntry]:
    """Today's finished downloads (UTC calendar date) - the "скачано сегодня" part of the
    Активность section (see app/api/activity.py). Includes both "done" and "error"
    entries, same as `list_download_history()` - a failed download still happened today.
    """
    today = datetime.now(UTC).date().isoformat()
    rows = conn.execute(
        "SELECT * FROM download_history WHERE user_id = ? AND finished_at >= ? "
        "ORDER BY finished_at DESC, id DESC",
        (user_id, today),
    ).fetchall()
    return [_row_to_entry(row) for row in rows]


def _row_to_entry(row: sqlite3.Row) -> DownloadHistoryEntry:
    return DownloadHistoryEntry(
        id=row["id"],
        user_id=row["user_id"],
        slug_url=row["slug_url"],
        fmt=row["fmt"],
        status=row["status"],
        chapter_count=row["chapter_count"],
        error=row["error"],
        finished_at=row["finished_at"],
    )
