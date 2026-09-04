"""Access to the ``reactions`` table (migrations/0010_reactions.sql) - PR 132's emoji
reactions on a paragraph.

Keyed the same way PR 131's context menu already identifies a paragraph:
slug_url/volume/number/branch_id (denormalized, mirroring ``activity_events`` and
``library_entries`` - see CLAUDE.md's "Архитектура", there is no local chapters table to
carry a foreign key to; the SDK is the source of truth for chapter content itself) plus
the paragraph's own index among ``.reader-content``'s children. ``branch_id`` is stored as
``''`` rather than NULL for "no branch selected" - a plain UNIQUE constraint treats NULLs
as distinct from each other (true of both SQLite and Postgres), which would silently
defeat "one reaction per user per paragraph" for chapters with no branch_id.

One row per user per paragraph, holding whichever single emoji they last picked - the
roadmap is explicit that reacting is a toggle, not a like counter than accumulates.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from psycopg import AsyncConnection

# Must stay in sync with the picker in app/static/js/paragraph-menu.js - both lists exist
# independently (no shared JSON between Python and JS in this codebase), so a change here
# needs the same change made there.
ALLOWED_EMOJI = ("👍", "❤️", "😂", "😮", "😢", "😡", "🔥", "👏", "🤔", "💯")


async def toggle_reaction(
    conn: AsyncConnection,
    user_id: int,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
    emoji: str,
) -> str | None:
    """Sets `emoji` as this user's one reaction to the paragraph, replacing whatever they
    had there before - or, if `emoji` is already their active reaction, removes it
    instead (clicking the same emoji again is "undo", not a second vote). Returns the
    resulting state: the emoji now active, or None if the reaction was removed."""
    cursor = await conn.execute(
        "SELECT emoji FROM reactions "
        "WHERE user_id = %s AND slug_url = %s AND volume = %s AND number = %s "
        "AND branch_id = %s AND paragraph_index = %s",
        (user_id, slug_url, volume, number, branch_id, paragraph_index),
    )
    existing = await cursor.fetchone()

    if existing is not None and existing["emoji"] == emoji:
        await conn.execute(
            "DELETE FROM reactions "
            "WHERE user_id = %s AND slug_url = %s AND volume = %s AND number = %s "
            "AND branch_id = %s AND paragraph_index = %s",
            (user_id, slug_url, volume, number, branch_id, paragraph_index),
        )
        return None

    await conn.execute(
        "INSERT INTO reactions "
        "(user_id, slug_url, volume, number, branch_id, paragraph_index, emoji, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT(user_id, slug_url, volume, number, branch_id, paragraph_index) "
        "DO UPDATE SET emoji = excluded.emoji, created_at = excluded.created_at",
        (
            user_id,
            slug_url,
            volume,
            number,
            branch_id,
            paragraph_index,
            emoji,
            datetime.now(UTC).isoformat(),
        ),
    )
    return emoji


async def count_reactions(
    conn: AsyncConnection, slug_url: str, volume: str, number: str, branch_id: str
) -> dict[int, dict[str, int]]:
    """Aggregated counts per paragraph for the whole chapter in one query - the reaction
    strip under any given paragraph shouldn't need a request of its own, since a chapter
    page can have dozens of them."""
    cursor = await conn.execute(
        "SELECT paragraph_index, emoji, COUNT(*) AS n FROM reactions "
        "WHERE slug_url = %s AND volume = %s AND number = %s AND branch_id = %s "
        "GROUP BY paragraph_index, emoji",
        (slug_url, volume, number, branch_id),
    )
    rows = await cursor.fetchall()
    counts: dict[int, dict[str, int]] = defaultdict(dict)
    for row in rows:
        counts[row["paragraph_index"]][row["emoji"]] = row["n"]
    return dict(counts)


async def count_reactions_for_paragraph(
    conn: AsyncConnection,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
) -> dict[str, int]:
    """Same as `count_reactions`, narrowed to one paragraph - what the toggle endpoint
    returns so the client can refresh just the paragraph it touched."""
    cursor = await conn.execute(
        "SELECT emoji, COUNT(*) AS n FROM reactions "
        "WHERE slug_url = %s AND volume = %s AND number = %s AND branch_id = %s "
        "AND paragraph_index = %s "
        "GROUP BY emoji",
        (slug_url, volume, number, branch_id, paragraph_index),
    )
    rows = await cursor.fetchall()
    return {row["emoji"]: row["n"] for row in rows}


async def user_reactions(
    conn: AsyncConnection,
    user_id: int,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
) -> dict[int, str]:
    """Which single emoji (if any) this user has picked for each paragraph in the
    chapter - lets the picker highlight the visitor's own current pick."""
    cursor = await conn.execute(
        "SELECT paragraph_index, emoji FROM reactions "
        "WHERE user_id = %s AND slug_url = %s AND volume = %s AND number = %s AND branch_id = %s",
        (user_id, slug_url, volume, number, branch_id),
    )
    rows = await cursor.fetchall()
    return {row["paragraph_index"]: row["emoji"] for row in rows}
