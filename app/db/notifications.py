"""Access to the ``notifications`` table (migrations/0014_notifications.sql) - PR 167,
the data model and generation side of the notifications feature. No listing/read/delete
API yet - that's PR 168/169/170, which read this same table once there's a UI to show it
in.

``kind`` is a plain string tag (not an enum/CHECK constraint) so a later notification kind
doesn't need a migration of its own to add - the same "no schema ceremony ahead of actual
need" reasoning CLAUDE.md already applies to the migration runner itself. ``comment_id`` is
nullable for the same reason: a future kind might not be about a comment at all.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

KIND_COMMENT_REACTION = "comment_reaction"


def notify_comment_reaction(
    conn: sqlite3.Connection, comment_id: int, actor_user_id: int
) -> None:
    """Records that `actor_user_id` reacted to `comment_id`, for that comment's author to
    see - a no-op if the comment doesn't exist, or if the author is reacting to their own
    comment (nothing to tell them they don't already know).

    Repeatedly toggling a reaction on the same comment (like -> dislike -> like, or
    mashing the same button) must not pile up a fresh row per click: if the author already
    has an *unread* notification for this exact (comment, actor) pair, its timestamp is
    refreshed in place instead of inserting a second one. A notification the author has
    already read gets a new row on the next reaction, same as any other unread-until-seen
    notification feed - that one's already been seen, this is a new event."""
    comment = conn.execute(
        "SELECT user_id FROM comments WHERE id = ?", (comment_id,)
    ).fetchone()
    if comment is None:
        return
    recipient_id = comment["user_id"]
    if recipient_id == actor_user_id:
        return

    created_at = datetime.now(UTC).isoformat()
    existing = conn.execute(
        "SELECT id FROM notifications "
        "WHERE user_id = ? AND kind = ? AND comment_id = ? AND actor_user_id = ? "
        "AND is_read = 0",
        (recipient_id, KIND_COMMENT_REACTION, comment_id, actor_user_id),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE notifications SET created_at = ? WHERE id = ?",
            (created_at, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO notifications "
            "(user_id, kind, comment_id, actor_user_id, is_read, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (recipient_id, KIND_COMMENT_REACTION, comment_id, actor_user_id, created_at),
        )
    conn.commit()
