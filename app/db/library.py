"""Access to the ``library_entries`` table (see migrations/0002_library_entries.sql,
0008_library_entries_favorite.sql for ``is_favorite``).

Deliberately stores only ``slug_url`` and reading progress - not the title's name/cover.
Those are SDK response data, already cached in the SDK's own ``cache_dir``; duplicating
them here would be exactly the "own cache on top of the SDK cache" CLAUDE.md rules out.
Callers needing a title's display info re-fetch it through ``app/services/client.py``
(cheap - it's a local cache hit after the first request).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection


@dataclass(frozen=True)
class LibraryEntry:
    id: int
    user_id: int
    slug_url: str
    added_at: str
    last_read_volume: str | None
    last_read_number: str | None
    last_read_at: str | None
    is_favorite: bool


async def add_entry(conn: AsyncConnection, user_id: int, slug_url: str) -> LibraryEntry:
    """Idempotent - adding a title that's already in the library just returns the
    existing row instead of raising, since a repeat click of "add" isn't an error."""
    await conn.execute(
        "INSERT INTO library_entries (user_id, slug_url, added_at) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (user_id, slug_url, datetime.now(UTC).isoformat()),
    )
    entry = await get_entry(conn, user_id, slug_url)
    assert entry is not None  # just inserted (or already existed)
    return entry


async def remove_entry(conn: AsyncConnection, user_id: int, slug_url: str) -> None:
    """Not an error if the title wasn't in the library to begin with."""
    await conn.execute(
        "DELETE FROM library_entries WHERE user_id = %s AND slug_url = %s",
        (user_id, slug_url),
    )


async def get_entry(conn: AsyncConnection, user_id: int, slug_url: str) -> LibraryEntry | None:
    cursor = await conn.execute(
        "SELECT * FROM library_entries WHERE user_id = %s AND slug_url = %s",
        (user_id, slug_url),
    )
    row = await cursor.fetchone()
    return _row_to_entry(row) if row is not None else None


async def list_entries(conn: AsyncConnection, user_id: int) -> list[LibraryEntry]:
    """Most recently read first, falling back to most recently added for titles that
    haven't been opened yet."""
    cursor = await conn.execute(
        "SELECT * FROM library_entries WHERE user_id = %s "
        "ORDER BY COALESCE(last_read_at, added_at) DESC",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_entry(row) for row in rows]


async def set_favorite(conn: AsyncConnection, user_id: int, slug_url: str) -> None:
    """Marks `slug_url` as `user_id`'s one favorite title, clearing any previous favorite
    first - exactly one favorite per user is simplest as a plain boolean flag reset on
    every new pick, rather than a separate table just to hold a single value (see PR 123
    in CLAUDE.md's roadmap). No-op (both UPDATEs affect 0 rows) if `slug_url` isn't
    actually in this user's library."""
    await conn.execute("UPDATE library_entries SET is_favorite = 0 WHERE user_id = %s", (user_id,))
    await conn.execute(
        "UPDATE library_entries SET is_favorite = 1 WHERE user_id = %s AND slug_url = %s",
        (user_id, slug_url),
    )


async def unset_favorite(conn: AsyncConnection, user_id: int, slug_url: str) -> None:
    """Not an error if `slug_url` wasn't the favorite (or wasn't in the library) to begin
    with - same "no-op instead of raising" shape as `remove_entry`."""
    await conn.execute(
        "UPDATE library_entries SET is_favorite = 0 WHERE user_id = %s AND slug_url = %s",
        (user_id, slug_url),
    )


async def get_favorite_entry(conn: AsyncConnection, user_id: int) -> LibraryEntry | None:
    cursor = await conn.execute(
        "SELECT * FROM library_entries WHERE user_id = %s AND is_favorite = 1", (user_id,)
    )
    row = await cursor.fetchone()
    return _row_to_entry(row) if row is not None else None


async def record_progress(
    conn: AsyncConnection, user_id: int, slug_url: str, volume: str, number: str
) -> None:
    """Only updates an existing row - no-op if `slug_url` isn't in this user's library.
    In practice the chapter-read route (PR 35) calls `add_entry()` right before this, so
    the row always exists by the time we get here; this stays a plain UPDATE rather than
    an upsert so other callers without that guarantee can't silently create entries."""
    await conn.execute(
        "UPDATE library_entries "
        "SET last_read_volume = %s, last_read_number = %s, last_read_at = %s "
        "WHERE user_id = %s AND slug_url = %s",
        (volume, number, datetime.now(UTC).isoformat(), user_id, slug_url),
    )


def _row_to_entry(row: dict[str, Any]) -> LibraryEntry:
    return LibraryEntry(
        id=row["id"],
        user_id=row["user_id"],
        slug_url=row["slug_url"],
        added_at=row["added_at"],
        last_read_volume=row["last_read_volume"],
        last_read_number=row["last_read_number"],
        last_read_at=row["last_read_at"],
        is_favorite=bool(row["is_favorite"]),
    )
