"""Access to the ``notifications`` table (migrations/0014_notifications.sql) - PR 167 added
the generation side (notify_comment_reaction() below); PR 168 adds the read side the
sidebar bell/panel needs; PR 169 adds paged access for the "Все уведомления" page; PR 170
adds mark-read/delete (mark_notification_read()/delete_notification() below) - the only
other place ``is_read`` was ever mutated before this is the dedupe path inside
notify_comment_reaction() itself.

``kind`` is a plain string tag (not an enum/CHECK constraint) so a later notification kind
doesn't need a migration of its own to add - the same "no schema ceremony ahead of actual
need" reasoning CLAUDE.md already applies to the migration runner itself. ``comment_id`` is
nullable for the same reason: a future kind might not be about a comment at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from app.auth.avatar import initials_for, url_for

KIND_COMMENT_REACTION = "comment_reaction"
# PR 199: friend requests/acceptances (app/db/friendships.py) - both inserted only from the
# code path that actually created the underlying row (a new pending request, or a request
# just accepted), never on send_request()'s idempotent "already existed" path - unlike
# KIND_COMMENT_REACTION above, neither needs its own unread-dedupe merge, since the
# friendships table itself already prevents the same event from happening twice.
KIND_FRIEND_REQUEST = "friend_request"
KIND_FRIEND_ACCEPT = "friend_accept"

# PR 168: how much of the comment's own body shows in the notification card - a hint of
# context ("отреагировали на ваш комментарий «Согласен, но...»"), not the full text (which
# can run to MAX_COMMENT_LENGTH/app/db/comments.py's 2000 characters).
COMMENT_EXCERPT_LENGTH = 140


async def notify_comment_reaction(
    conn: AsyncConnection, comment_id: int, actor_user_id: int
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
    cursor = await conn.execute("SELECT user_id FROM comments WHERE id = %s", (comment_id,))
    comment = await cursor.fetchone()
    if comment is None:
        return
    recipient_id = comment["user_id"]
    if recipient_id == actor_user_id:
        return

    created_at = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "SELECT id FROM notifications "
        "WHERE user_id = %s AND kind = %s AND comment_id = %s AND actor_user_id = %s "
        "AND is_read = 0",
        (recipient_id, KIND_COMMENT_REACTION, comment_id, actor_user_id),
    )
    existing = await cursor.fetchone()
    if existing is not None:
        await conn.execute(
            "UPDATE notifications SET created_at = %s WHERE id = %s",
            (created_at, existing["id"]),
        )
    else:
        await conn.execute(
            "INSERT INTO notifications "
            "(user_id, kind, comment_id, actor_user_id, is_read, created_at) "
            "VALUES (%s, %s, %s, %s, 0, %s)",
            (recipient_id, KIND_COMMENT_REACTION, comment_id, actor_user_id, created_at),
        )


async def notify_friend_request(
    conn: AsyncConnection, recipient_id: int, actor_user_id: int
) -> None:
    """Tells `recipient_id` that `actor_user_id` just sent them a friend request - call
    only when `friendships.send_request()` actually created a new pending row, never on
    its idempotent "a relationship already existed" path (see KIND_FRIEND_REQUEST above)."""
    await _insert_notification(conn, recipient_id, KIND_FRIEND_REQUEST, actor_user_id)


async def notify_friend_accept(
    conn: AsyncConnection, recipient_id: int, actor_user_id: int
) -> None:
    """Tells `recipient_id` (the original requester) that `actor_user_id` accepted their
    friend request."""
    await _insert_notification(conn, recipient_id, KIND_FRIEND_ACCEPT, actor_user_id)


async def _insert_notification(
    conn: AsyncConnection, recipient_id: int, kind: str, actor_user_id: int
) -> None:
    await conn.execute(
        "INSERT INTO notifications (user_id, kind, comment_id, actor_user_id, is_read, created_at) "
        "VALUES (%s, %s, NULL, %s, 0, %s)",
        (recipient_id, kind, actor_user_id, datetime.now(UTC).isoformat()),
    )


async def mark_notification_read(
    conn: AsyncConnection, notification_id: int, user_id: int
) -> bool:
    """True if a row was actually updated. Scoping the UPDATE by `user_id` as well as
    `id` means an id that exists but belongs to another user updates nothing and reports
    back the same as an id that doesn't exist at all - the caller turns both into a 404,
    not a 403, same reasoning as app/db/downloads.py's delete_entry(). A no-op (still
    returns True) if the row was already read - the caller doesn't need to special-case
    "the visitor double-clicked" or "two tabs raced" as an error."""
    cursor = await conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = %s AND user_id = %s",
        (notification_id, user_id),
    )
    return cursor.rowcount > 0


async def delete_notification(conn: AsyncConnection, notification_id: int, user_id: int) -> bool:
    """Same ownership-scoped shape as mark_notification_read() above - a real DELETE, not
    a soft "hidden" flag, per the roadmap's own wording."""
    cursor = await conn.execute(
        "DELETE FROM notifications WHERE id = %s AND user_id = %s",
        (notification_id, user_id),
    )
    return cursor.rowcount > 0


@dataclass(frozen=True)
class Notification:
    id: int
    kind: str
    is_read: bool
    created_at: str
    actor_name: str
    # PR 199: who to link to for a kind that isn't about a comment (friend_request/
    # friend_accept) - _comment_url() below already covers the comment_id case.
    actor_user_id: int
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


async def count_unread_notifications(conn: AsyncConnection, user_id: int) -> int:
    """What the sidebar bell's badge shows - polled the same way downloads-status.js
    already polls its own badge (app/api/downloads_section.py's /downloads/status)."""
    cursor = await conn.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE user_id = %s AND is_read = 0",
        (user_id,),
    )
    row = await cursor.fetchone()
    return row["n"]


async def list_recent_notifications(
    conn: AsyncConnection, user_id: int, limit: int
) -> list[Notification]:
    """The bell panel's own list, newest first - fetched once when it's opened, not
    polled (only the unread count above needs to be live everywhere).

    Unread only: the panel is a quick "what's new" popover, not a history - once
    something's been read (PR 170) it belongs on the full `/notifications` page
    (list_notifications_page() below), not lingering here mixed in with what's still
    unread (PR 179)."""
    return await _list_notifications(conn, user_id, offset=0, limit=limit, unread_only=True)


async def list_notifications_page(
    conn: AsyncConnection, user_id: int, page: int, page_size: int
) -> tuple[list[Notification], bool]:
    """PR 169's "Все уведомления" page, one page at a time (`page` is 1-indexed, same
    convention as app/api/library.py's own catalog pagination). Fetches one extra row
    past `page_size` to answer "is there a next page" without a second COUNT(*) query -
    the catalog's own has_next_page instead comes straight from the SDK's response, but
    there's no such response to read it off here, so this table computes its own.

    Full history, read and unread both - unlike the bell panel above (PR 179), this page
    is where a read notification keeps living, distinguished only by the
    `.notifications-panel__item--unread` CSS modifier."""
    rows = await _list_notifications(
        conn, user_id, offset=(page - 1) * page_size, limit=page_size + 1
    )
    has_next_page = len(rows) > page_size
    return rows[:page_size], has_next_page


async def _list_notifications(
    conn: AsyncConnection, user_id: int, offset: int, limit: int, unread_only: bool = False
) -> list[Notification]:
    """LEFT JOIN comments (not JOIN) so a notification survives its comment being deleted
    later (PR 172) - comment_id/comment_excerpt/comment_url just come back None instead of
    the whole row vanishing or the query failing.

    Orders by `id DESC` as a tiebreaker after `created_at DESC` - two notifications
    created microseconds apart (e.g. in a tight loop, as tests do) can land on the same
    ISO timestamp, and `created_at` alone would then leave their relative order to
    Postgres's own unspecified tie-breaking instead of "most recently inserted first"."""
    unread_clause = "AND notifications.is_read = 0 " if unread_only else ""
    cursor = await conn.execute(
        "SELECT notifications.id, notifications.kind, notifications.is_read, "
        "notifications.created_at, notifications.actor_user_id, "
        "actor.nickname AS actor_nickname, actor.email AS actor_email, "
        "actor.avatar_path AS actor_avatar_path, "
        "comments.id AS comment_id, comments.body AS comment_body, "
        "comments.slug_url AS comment_slug_url, comments.volume AS comment_volume, "
        "comments.number AS comment_number, comments.branch_id AS comment_branch_id "
        "FROM notifications "
        "JOIN users AS actor ON actor.id = notifications.actor_user_id "
        "LEFT JOIN comments ON comments.id = notifications.comment_id "
        "WHERE notifications.user_id = %s " + unread_clause + "ORDER BY "
        "notifications.created_at DESC, notifications.id DESC LIMIT %s OFFSET %s",
        (user_id, limit, offset),
    )
    rows = await cursor.fetchall()
    return [_row_to_notification(row) for row in rows]


def _row_to_notification(row: dict[str, Any]) -> Notification:
    return Notification(
        id=row["id"],
        kind=row["kind"],
        is_read=bool(row["is_read"]),
        created_at=row["created_at"],
        actor_name=row["actor_nickname"] or row["actor_email"],
        actor_user_id=row["actor_user_id"],
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


def _comment_url(row: dict[str, Any]) -> str | None:
    if row["comment_id"] is None:
        return None
    slug, volume, number = row["comment_slug_url"], row["comment_volume"], row["comment_number"]
    url = f"/titles/{slug}/chapters/{volume}/{number}"
    if row["comment_branch_id"]:
        url += f"?branch_id={row['comment_branch_id']}"
    return url
