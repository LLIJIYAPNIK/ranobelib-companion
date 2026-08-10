"""Access to the ``library_entries`` table (see migrations/0002_library_entries.sql).

Deliberately stores only ``slug_url`` and reading progress - not the title's name/cover.
Those are SDK response data, already cached in the SDK's own ``cache_dir``; duplicating
them here would be exactly the "own cache on top of the SDK cache" CLAUDE.md rules out.
Callers needing a title's display info re-fetch it through ``app/services/client.py``
(cheap - it's a local cache hit after the first request).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class LibraryEntry:
    id: int
    user_id: int
    slug_url: str
    added_at: str
    last_read_volume: str | None
    last_read_number: str | None
    last_read_at: str | None


def add_entry(conn: sqlite3.Connection, user_id: int, slug_url: str) -> LibraryEntry:
    """Idempotent - adding a title that's already in the library just returns the
    existing row instead of raising, since a repeat click of "add" isn't an error."""
    conn.execute(
        "INSERT OR IGNORE INTO library_entries (user_id, slug_url, added_at) "
        "VALUES (?, ?, ?)",
        (user_id, slug_url, datetime.now(UTC).isoformat()),
    )
    conn.commit()
    entry = get_entry(conn, user_id, slug_url)
    assert entry is not None  # just inserted (or already existed)
    return entry


def remove_entry(conn: sqlite3.Connection, user_id: int, slug_url: str) -> None:
    """Not an error if the title wasn't in the library to begin with."""
    conn.execute(
        "DELETE FROM library_entries WHERE user_id = ? AND slug_url = ?",
        (user_id, slug_url),
    )
    conn.commit()


def get_entry(conn: sqlite3.Connection, user_id: int, slug_url: str) -> LibraryEntry | None:
    row = conn.execute(
        "SELECT * FROM library_entries WHERE user_id = ? AND slug_url = ?",
        (user_id, slug_url),
    ).fetchone()
    return _row_to_entry(row) if row is not None else None


def list_entries(conn: sqlite3.Connection, user_id: int) -> list[LibraryEntry]:
    """Most recently read first, falling back to most recently added for titles that
    haven't been opened yet."""
    rows = conn.execute(
        "SELECT * FROM library_entries WHERE user_id = ? "
        "ORDER BY COALESCE(last_read_at, added_at) DESC",
        (user_id,),
    ).fetchall()
    return [_row_to_entry(row) for row in rows]


def record_progress(
    conn: sqlite3.Connection, user_id: int, slug_url: str, volume: str, number: str
) -> None:
    """Only updates an existing row - no-op if `slug_url` isn't in this user's library.
    In practice the chapter-read route (PR 35) calls `add_entry()` right before this, so
    the row always exists by the time we get here; this stays a plain UPDATE rather than
    an upsert so other callers without that guarantee can't silently create entries."""
    conn.execute(
        "UPDATE library_entries "
        "SET last_read_volume = ?, last_read_number = ?, last_read_at = ? "
        "WHERE user_id = ? AND slug_url = ?",
        (volume, number, datetime.now(UTC).isoformat(), user_id, slug_url),
    )
    conn.commit()


def _row_to_entry(row: sqlite3.Row) -> LibraryEntry:
    return LibraryEntry(
        id=row["id"],
        user_id=row["user_id"],
        slug_url=row["slug_url"],
        added_at=row["added_at"],
        last_read_volume=row["last_read_volume"],
        last_read_number=row["last_read_number"],
        last_read_at=row["last_read_at"],
    )
