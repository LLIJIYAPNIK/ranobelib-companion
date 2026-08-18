"""Access to the ``notifications`` table (migrations/0014_notifications.sql) - PR 167 added
the generation side (notify_comment_reaction() below); PR 168 adds the read side the
sidebar bell/panel needs; PR 169 adds paged access for the "Все уведомления" page. Mark-
read/delete is still PR 170's - nothing here mutates ``is_read`` except the dedupe path
inside notify_comment_reaction() itself.

``kind`` is a plain string tag (not an enum/CHECK constraint) so a later notification kind
doesn't need a migration of its own to add - the same "no schema ceremony ahead of actual
need" reasoning CLAUDE.md already applies to the migration runner itself. ``comment_id`` is
nullable for the same reason: a future kind might not be about a comment at all.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from app.auth.avatar import initials_for, url_for

KIND_COMMENT_REACTION = "comment_reaction"

# PR 168: how much of the comment's own body shows in the notification card - a hint of
# context ("отреагировали на ваш комментарий «Согласен, но...»"), not the full text (which
# can run to MAX_COMMENT_LENGTH/app/db/comments.py's 2000 characters).
COMMENT_EXCERPT_LENGTH = 140


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


@dataclass(frozen=True)
class Notification:
    id: int
    kind: str
    is_read: bool
    created_at: str
    actor_name: str
    # PR 147/comments.py's same picture-or-initials pairing - the bell panel renders one
    # or the other exactly like every other avatar in this codebase.
    actor_avatar_url: str | None
    actor_avatar_initials: str
    # Only ever set for kind == KIND_COMMENT_REACTION today - None for a comment that's
    # since been deleted (LEFT JOIN in list_recent_notifications() below) as well as for
    # any future kind that isn't about a comment at all.
    comment_id: int | None
    comment_excerpt: str | None
    comment_url: str | None


def count_unread_notifications(conn: sqlite3.Connection, user_id: int) -> int:
    """What the sidebar bell's badge shows - polled the same way downloads-status.js
    already polls its own badge (app/api/downloads_section.py's /downloads/status)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND is_read = 0",
        (user_id,),
    ).fetchone()
    return row["n"]


def list_recent_notifications(
    conn: sqlite3.Connection, user_id: int, limit: int
) -> list[Notification]:
    """The bell panel's own list, newest first - fetched once when it's opened, not
    polled (only the unread count above needs to be live everywhere)."""
    return _list_notifications(conn, user_id, offset=0, limit=limit)


def list_notifications_page(
    conn: sqlite3.Connection, user_id: int, page: int, page_size: int
) -> tuple[list[Notification], bool]:
    """PR 169's "Все уведомления" page, one page at a time (`page` is 1-indexed, same
    convention as app/api/library.py's own catalog pagination). Fetches one extra row
    past `page_size` to answer "is there a next page" without a second COUNT(*) query -
    the catalog's own has_next_page instead comes straight from the SDK's response, but
    there's no such response to read it off here, so this table computes its own."""
    rows = _list_notifications(conn, user_id, offset=(page - 1) * page_size, limit=page_size + 1)
    has_next_page = len(rows) > page_size
    return rows[:page_size], has_next_page


def _list_notifications(
    conn: sqlite3.Connection, user_id: int, offset: int, limit: int
) -> list[Notification]:
    """LEFT JOIN comments (not JOIN) so a notification survives its comment being deleted
    later (PR 172) - comment_id/comment_excerpt/comment_url just come back None instead of
    the whole row vanishing or the query failing.

    Orders by `id DESC` as a tiebreaker after `created_at DESC` - two notifications
    created microseconds apart (e.g. in a tight loop, as tests do) can land on the same
    ISO timestamp, and `created_at` alone would then leave their relative order to
    SQLite's own unspecified tie-breaking instead of "most recently inserted first"."""
    rows = conn.execute(
        "SELECT notifications.id, notifications.kind, notifications.is_read, "
        "notifications.created_at, "
        "actor.nickname AS actor_nickname, actor.email AS actor_email, "
        "actor.avatar_path AS actor_avatar_path, "
        "comments.id AS comment_id, comments.body AS comment_body, "
        "comments.slug_url AS comment_slug_url, comments.volume AS comment_volume, "
        "comments.number AS comment_number, comments.branch_id AS comment_branch_id "
        "FROM notifications "
        "JOIN users AS actor ON actor.id = notifications.actor_user_id "
        "LEFT JOIN comments ON comments.id = notifications.comment_id "
        "WHERE notifications.user_id = ? "
        "ORDER BY notifications.created_at DESC, notifications.id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()
    return [_row_to_notification(row) for row in rows]


def _row_to_notification(row: sqlite3.Row) -> Notification:
    return Notification(
        id=row["id"],
        kind=row["kind"],
        is_read=bool(row["is_read"]),
        created_at=row["created_at"],
        actor_name=row["actor_nickname"] or row["actor_email"],
        actor_avatar_url=url_for(row["actor_avatar_path"]),
        actor_avatar_initials=initials_for(row["actor_nickname"], row["actor_email"]),
        comment_id=row["comment_id"],
        comment_excerpt=_excerpt(row["comment_body"]),
        comment_url=_comment_url(row),
    )


def _excerpt(body: str | None) -> str | None:
    if body is None:
        return None
    if len(body) <= COMMENT_EXCERPT_LENGTH:
        return body
    return body[:COMMENT_EXCERPT_LENGTH].rstrip() + "…"


def _comment_url(row: sqlite3.Row) -> str | None:
    if row["comment_id"] is None:
        return None
    slug, volume, number = row["comment_slug_url"], row["comment_volume"], row["comment_number"]
    url = f"/titles/{slug}/chapters/{volume}/{number}"
    if row["comment_branch_id"]:
        url += f"?branch_id={row['comment_branch_id']}"
    return url
