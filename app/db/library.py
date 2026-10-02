"""Access to the ``library_entries`` table (see migrations/0002_library_entries.sql,
0008_library_entries_favorite.sql for ``is_favorite``, 0024_library_entries_read_paragraph.sql
for the paragraph-level reading position).

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
    default_translation_index: int | None
    # Paragraph-level position inside the last_read_* chapter (wave 35): the last revealed
    # paragraph and the chapter's paragraph count when it was saved - the total travels
    # with it because an edit on ranobelib.me can change the count since.
    last_read_paragraph: int | None
    last_read_paragraph_total: int | None


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


async def set_default_translation_index(
    conn: AsyncConnection, user_id: int, slug_url: str, translation_index: int | None
) -> None:
    """PR 205: persists a per-title "перевод по умолчанию" so start_download() (and later,
    reading a chapter) doesn't have to ask again every time a title turns out to have
    ambiguous chapters. `None` clears it back to "спрашивать каждый раз". Only meaningful
    for a title already in the library (nowhere else to store this per-user choice) - a
    no-op, same shape as set_favorite()/record_progress(), if `slug_url` isn't."""
    await conn.execute(
        "UPDATE library_entries SET default_translation_index = %s "
        "WHERE user_id = %s AND slug_url = %s",
        (translation_index, user_id, slug_url),
    )


async def get_currently_reading_entries(
    conn: AsyncConnection, user_ids: list[int]
) -> dict[int, LibraryEntry]:
    """The entry each of these users is "currently reading", keyed by user_id - same rule
    app/api/profile.py's own `currently_reading` uses for a single user (the most recently
    touched entry, COALESCE(last_read_at, added_at) DESC, only shown if that particular
    entry has actually been opened at least once): a title that's merely the most recently
    *added* one, never read, still means this user has nothing "currently reading", even if
    an older entry further down does have a read position.

    One query for the whole `user_ids` list (PR 200's friend-activity column needs this for
    every friend at once), not one per user - the inner DISTINCT ON picks each user's single
    most-recently-touched row first, and only then is it filtered down to rows that were
    actually read, so an unread top entry correctly hides that user entirely rather than
    falling through to an older, already-read one. A user_id absent from the returned dict
    has nothing to show."""
    if not user_ids:
        return {}
    cursor = await conn.execute(
        "SELECT * FROM ("
        "  SELECT DISTINCT ON (user_id) * FROM library_entries "
        "  WHERE user_id = ANY(%s) "
        "  ORDER BY user_id, COALESCE(last_read_at, added_at) DESC"
        ") AS most_recently_touched "
        "WHERE last_read_at IS NOT NULL",
        (user_ids,),
    )
    rows = await cursor.fetchall()
    return {row["user_id"]: _row_to_entry(row) for row in rows}


async def get_favorite_entry(conn: AsyncConnection, user_id: int) -> LibraryEntry | None:
    cursor = await conn.execute(
        "SELECT * FROM library_entries WHERE user_id = %s AND is_favorite = 1", (user_id,)
    )
    row = await cursor.fetchone()
    return _row_to_entry(row) if row is not None else None


async def record_progress(
    conn: AsyncConnection,
    user_id: int,
    slug_url: str,
    volume: str,
    number: str,
    paragraph: int | None = None,
    paragraph_total: int | None = None,
) -> None:
    """Only updates an existing row - no-op if `slug_url` isn't in this user's library.
    In practice the chapter-read route (PR 35) calls `add_entry()` right before this, so
    the row always exists by the time we get here; this stays a plain UPDATE rather than
    an upsert so other callers without that guarantee can't silently create entries.

    `paragraph`/`paragraph_total` (wave 35) are the position inside that chapter. Without
    them the stored position survives only a reopen of the same chapter - it belongs to
    last_read_volume/last_read_number, so moving to another chapter clears it rather than
    leaving the previous chapter's paragraph attached to the new one."""
    if paragraph is not None:
        await conn.execute(
            "UPDATE library_entries "
            "SET last_read_volume = %s, last_read_number = %s, last_read_at = %s, "
            "last_read_paragraph = %s, last_read_paragraph_total = %s "
            "WHERE user_id = %s AND slug_url = %s",
            (
                volume,
                number,
                datetime.now(UTC).isoformat(),
                paragraph,
                paragraph_total,
                user_id,
                slug_url,
            ),
        )
        return
    # The CASEs read the row's old chapter - Postgres evaluates every SET expression
    # against the pre-update row, so the order of assignments doesn't matter.
    same_chapter = "(last_read_volume = %s AND last_read_number = %s)"
    await conn.execute(
        "UPDATE library_entries "
        "SET last_read_volume = %s, last_read_number = %s, last_read_at = %s, "
        f"last_read_paragraph = CASE WHEN {same_chapter} THEN last_read_paragraph END, "
        f"last_read_paragraph_total = CASE WHEN {same_chapter} "
        "THEN last_read_paragraph_total END "
        "WHERE user_id = %s AND slug_url = %s",
        (
            volume,
            number,
            datetime.now(UTC).isoformat(),
            volume,
            number,
            volume,
            number,
            user_id,
            slug_url,
        ),
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
        default_translation_index=row["default_translation_index"],
        last_read_paragraph=row["last_read_paragraph"],
        last_read_paragraph_total=row["last_read_paragraph_total"],
    )
