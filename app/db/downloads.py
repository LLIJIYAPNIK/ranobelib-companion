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


def list_download_history(
    conn: sqlite3.Connection, user_id: int, limit: int = 20
) -> list[DownloadHistoryEntry]:
    """Most recently finished first."""
    rows = conn.execute(
        "SELECT * FROM download_history WHERE user_id = ? "
        "ORDER BY finished_at DESC, id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        DownloadHistoryEntry(
            id=row["id"],
            user_id=row["user_id"],
            slug_url=row["slug_url"],
            fmt=row["fmt"],
            status=row["status"],
            chapter_count=row["chapter_count"],
            error=row["error"],
            finished_at=row["finished_at"],
        )
        for row in rows
    ]
