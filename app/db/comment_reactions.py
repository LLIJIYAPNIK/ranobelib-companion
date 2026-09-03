"""Access to the ``comment_reactions`` table (migrations/0013_comment_reactions.sql) -
PR 155's like/dislike on a comment.

A separate feature and a separate table from app/db/reactions.py's own PR 132 reactions:
those react to a paragraph of the chapter itself (a palette of 10 emoji, denormalized
against slug_url/volume/number/branch_id/paragraph_index since there's no local table for
a chapter to hold a real foreign key to); these react to one specific comment, which - unlike
a chapter paragraph - *is* a row in this app's own database, so ``comment_id`` can be a real
foreign key instead of denormalizing the comment's own paragraph key onto every reaction row.

One row per user per comment, holding whichever single value (+1 like / -1 dislike) they
last picked - same toggle semantics as the paragraph reactions above: reacting again with
the same value removes it, the opposite value replaces it, never a second row.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from psycopg import Connection

LIKE = 1
DISLIKE = -1


def toggle_comment_reaction(
    conn: Connection, user_id: int, comment_id: int, value: int
) -> int | None:
    """Sets `value` as this user's one reaction to the comment, replacing whatever they
    had there before - or, if `value` is already their active reaction, removes it instead
    (clicking the same button again is "undo", not a second vote). Returns the resulting
    state: the value now active, or None if the reaction was removed.

    Raises ValueError if `comment_id` doesn't name an actual comment - there's no foreign
    key enforcement failure to rely on here either way (an explicit check keeps the error
    the same shape it'd need if the FK were ever dropped), the same defensive shape
    create_comment() already uses for a stale/tampered `parent_comment_id`."""
    comment = conn.execute("SELECT 1 FROM comments WHERE id = %s", (comment_id,)).fetchone()
    if comment is None:
        raise ValueError("Комментарий не найден")

    existing = conn.execute(
        "SELECT value FROM comment_reactions WHERE comment_id = %s AND user_id = %s",
        (comment_id, user_id),
    ).fetchone()

    if existing is not None and existing["value"] == value:
        conn.execute(
            "DELETE FROM comment_reactions WHERE comment_id = %s AND user_id = %s",
            (comment_id, user_id),
        )
        return None

    conn.execute(
        "INSERT INTO comment_reactions (comment_id, user_id, value, created_at) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT(comment_id, user_id) "
        "DO UPDATE SET value = excluded.value, created_at = excluded.created_at",
        (comment_id, user_id, value, datetime.now(UTC).isoformat()),
    )
    return value


def count_reactions_for_comment(conn: Connection, comment_id: int) -> dict[str, int]:
    """Like/dislike counts for one comment - what the toggle endpoint returns so the
    client can refresh just the comment it touched, without refetching the whole thread."""
    rows = conn.execute(
        "SELECT value, COUNT(*) AS n FROM comment_reactions "
        "WHERE comment_id = %s GROUP BY value",
        (comment_id,),
    ).fetchall()
    counts = {"like": 0, "dislike": 0}
    for row in rows:
        counts["like" if row["value"] == LIKE else "dislike"] = row["n"]
    return counts


def count_reactions_for_paragraph(
    conn: Connection,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
) -> dict[int, dict[str, int]]:
    """Like/dislike counts for every comment (replies included) under one paragraph, one
    query for the whole thread - same "one bulk fetch, not one per comment" reasoning as
    app/db/comments.py's own count_comments()/list_comments_for_paragraph()."""
    rows = conn.execute(
        "SELECT comment_reactions.comment_id, comment_reactions.value, COUNT(*) AS n "
        "FROM comment_reactions "
        "JOIN comments ON comments.id = comment_reactions.comment_id "
        "WHERE comments.slug_url = %s AND comments.volume = %s AND comments.number = %s "
        "AND comments.branch_id = %s AND comments.paragraph_index = %s "
        "GROUP BY comment_reactions.comment_id, comment_reactions.value",
        (slug_url, volume, number, branch_id, paragraph_index),
    ).fetchall()
    counts: dict[int, dict[str, int]] = defaultdict(lambda: {"like": 0, "dislike": 0})
    for row in rows:
        counts[row["comment_id"]]["like" if row["value"] == LIKE else "dislike"] = row["n"]
    return dict(counts)


def user_reactions_for_paragraph(
    conn: Connection,
    user_id: int,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
) -> dict[int, int]:
    """Which value (if any) this user has picked for each comment under one paragraph -
    lets the client highlight the visitor's own current like/dislike, same purpose as
    app/db/reactions.py's own user_reactions() for paragraph reactions."""
    rows = conn.execute(
        "SELECT comment_reactions.comment_id, comment_reactions.value "
        "FROM comment_reactions "
        "JOIN comments ON comments.id = comment_reactions.comment_id "
        "WHERE comment_reactions.user_id = %s AND comments.slug_url = %s "
        "AND comments.volume = %s AND comments.number = %s AND comments.branch_id = %s "
        "AND comments.paragraph_index = %s",
        (user_id, slug_url, volume, number, branch_id, paragraph_index),
    ).fetchall()
    return {row["comment_id"]: row["value"] for row in rows}
