"""Access to the ``friendships`` table (migrations/0018_friendships.sql) - PR 199's friend
requests/relationships.

A single row models the relationship between two users regardless of who acts on it next:
``status`` starts ``'pending'`` (a request `requester_id` sent to `addressee_id`), flips to
``'accepted'`` in place once the addressee accepts, and the row is a real ``DELETE`` (never
a soft flag) for a decline, a cancelled outgoing request, or removing an existing friend -
all three are the same "this relationship no longer exists" operation, just reached from
different UI states (see `remove_relationship()` below).

At most one row can ever exist between any two users, in either direction - enforced by the
migration's `idx_friendships_pair` unique index on `(LEAST(a, b), GREATEST(a, b))`, the same
"pre-check + DB constraint as race-safe backstop" pattern already used for
``users.email``/``nickname`` uniqueness (see `send_request()` below and its docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import AsyncConnection

from app.auth.avatar import initials_for, url_for


@dataclass(frozen=True)
class Friendship:
    id: int
    requester_id: int
    addressee_id: int
    status: str  # "pending" | "accepted"
    created_at: str
    responded_at: str | None


@dataclass(frozen=True)
class FriendUser:
    """Just enough of the other party's `User` row (app/db/users.py) to render a friend-
    list/request row - same trimmed-down shape notifications.py's `Notification.actor_*`
    fields use for the same reason (a full `User` object isn't needed here)."""

    id: int
    display_name: str
    avatar_url: str | None
    avatar_initials: str


@dataclass(frozen=True)
class FriendEntry:
    friendship_id: int
    user: FriendUser
    since: str  # responded_at - always set for an accepted friendship


@dataclass(frozen=True)
class FriendRequestEntry:
    friendship_id: int
    user: FriendUser
    created_at: str


async def get_relationship(
    conn: AsyncConnection, user_a: int, user_b: int
) -> Friendship | None:
    """The relationship between these two users regardless of who requested it, or None if
    there isn't one yet."""
    cursor = await conn.execute(
        "SELECT id, requester_id, addressee_id, status, created_at, responded_at "
        "FROM friendships "
        "WHERE (requester_id = %s AND addressee_id = %s) "
        "OR (requester_id = %s AND addressee_id = %s)",
        (user_a, user_b, user_b, user_a),
    )
    row = await cursor.fetchone()
    return _row_to_friendship(row) if row is not None else None


async def send_request(
    conn: AsyncConnection, requester_id: int, addressee_id: int
) -> tuple[Friendship, bool]:
    """Idempotent - sending a request where a relationship already exists in either
    direction (pending either way, or already accepted) just returns that existing row
    instead of raising, the same "repeat click isn't an error" shape as
    `library.add_entry()`. The second element of the returned tuple is True only when this
    call actually inserted a new row - callers (see app/api/friendships.py) use it to decide
    whether to fire `notifications.notify_friend_request()`, so a repeat click never sends a
    duplicate notification.

    The existence check above isn't itself race-safe against two concurrent requests
    landing at once (e.g. A -> B and B -> A within the same instant) - `idx_friendships_pair`
    (migrations/0018_friendships.sql) is the actual source of truth, and a `UniqueViolation`
    from it here just means the other side won the race a moment ago, so this re-reads and
    returns whatever they created (with `created=False`, same as any other pre-existing
    relationship) rather than raising."""
    existing = await get_relationship(conn, requester_id, addressee_id)
    if existing is not None:
        return existing, False

    created_at = datetime.now(UTC).isoformat()
    try:
        cursor = await conn.execute(
            "INSERT INTO friendships (requester_id, addressee_id, status, created_at) "
            "VALUES (%s, %s, 'pending', %s) "
            "RETURNING id, requester_id, addressee_id, status, created_at, responded_at",
            (requester_id, addressee_id, created_at),
        )
    except psycopg.errors.UniqueViolation:
        existing = await get_relationship(conn, requester_id, addressee_id)
        assert existing is not None  # the constraint that just fired guarantees this
        return existing, False
    row = await cursor.fetchone()
    assert row is not None  # just inserted
    return _row_to_friendship(row), True


async def accept_request(conn: AsyncConnection, user_id: int, requester_id: int) -> bool:
    """`user_id` (the addressee) accepts a pending request from `requester_id`. True if a
    row was actually updated - False if there's no such pending request (already handled,
    never existed, or `user_id` is actually the requester, not the addressee - the WHERE
    clause scopes both, same ownership-scoping reasoning as
    `notifications.mark_notification_read()`)."""
    cursor = await conn.execute(
        "UPDATE friendships SET status = 'accepted', responded_at = %s "
        "WHERE requester_id = %s AND addressee_id = %s AND status = 'pending'",
        (datetime.now(UTC).isoformat(), requester_id, user_id),
    )
    return cursor.rowcount > 0


async def remove_relationship(conn: AsyncConnection, user_id: int, other_user_id: int) -> bool:
    """A real DELETE, not a soft flag, per the roadmap's own wording - covers declining an
    incoming request, cancelling an outgoing one, and unfriending an accepted one alike,
    since all three mean the same thing: this relationship no longer exists. Either party
    may call this regardless of who originally sent the request. True if a row was actually
    removed."""
    cursor = await conn.execute(
        "DELETE FROM friendships "
        "WHERE (requester_id = %s AND addressee_id = %s) "
        "OR (requester_id = %s AND addressee_id = %s)",
        (user_id, other_user_id, other_user_id, user_id),
    )
    return cursor.rowcount > 0


async def list_friends(conn: AsyncConnection, user_id: int) -> list[FriendEntry]:
    """Accepted friendships only, most recently accepted first."""
    cursor = await conn.execute(
        "SELECT friendships.id AS friendship_id, friendships.responded_at, "
        "other.id AS other_id, other.nickname AS other_nickname, "
        "other.email AS other_email, other.avatar_path AS other_avatar_path "
        "FROM friendships "
        "JOIN users AS other "
        "ON other.id = CASE WHEN friendships.requester_id = %s "
        "THEN friendships.addressee_id ELSE friendships.requester_id END "
        "WHERE friendships.status = 'accepted' "
        "AND (friendships.requester_id = %s OR friendships.addressee_id = %s) "
        "ORDER BY friendships.responded_at DESC",
        (user_id, user_id, user_id),
    )
    rows = await cursor.fetchall()
    return [
        FriendEntry(
            friendship_id=row["friendship_id"],
            user=_row_to_friend_user(row),
            since=row["responded_at"],
        )
        for row in rows
    ]


async def list_incoming_requests(conn: AsyncConnection, user_id: int) -> list[FriendRequestEntry]:
    """Pending requests `user_id` has received (they're the addressee), oldest first - the
    same "oldest unactioned item first" ordering a request queue usually reads best in."""
    return await _list_requests(conn, addressee_id=user_id)


async def list_outgoing_requests(conn: AsyncConnection, user_id: int) -> list[FriendRequestEntry]:
    """Pending requests `user_id` has sent (they're the requester), waiting on someone
    else's response."""
    return await _list_requests(conn, requester_id=user_id)


async def _list_requests(
    conn: AsyncConnection, *, addressee_id: int | None = None, requester_id: int | None = None
) -> list[FriendRequestEntry]:
    assert (addressee_id is None) != (requester_id is None)  # exactly one side is fixed
    if addressee_id is not None:
        own_column, other_column, own_value = "addressee_id", "requester_id", addressee_id
    else:
        own_column, other_column, own_value = "requester_id", "addressee_id", requester_id
    cursor = await conn.execute(
        f"SELECT friendships.id AS friendship_id, friendships.created_at, "
        f"other.id AS other_id, other.nickname AS other_nickname, "
        f"other.email AS other_email, other.avatar_path AS other_avatar_path "
        f"FROM friendships "
        f"JOIN users AS other ON other.id = friendships.{other_column} "
        f"WHERE friendships.{own_column} = %s AND friendships.status = 'pending' "
        f"ORDER BY friendships.created_at ASC",
        (own_value,),
    )
    rows = await cursor.fetchall()
    return [
        FriendRequestEntry(
            friendship_id=row["friendship_id"],
            user=_row_to_friend_user(row),
            created_at=row["created_at"],
        )
        for row in rows
    ]


def _row_to_friendship(row: dict[str, Any]) -> Friendship:
    return Friendship(
        id=row["id"],
        requester_id=row["requester_id"],
        addressee_id=row["addressee_id"],
        status=row["status"],
        created_at=row["created_at"],
        responded_at=row["responded_at"],
    )


def _row_to_friend_user(row: dict[str, Any]) -> FriendUser:
    return FriendUser(
        id=row["other_id"],
        display_name=row["other_nickname"] or row["other_email"],
        avatar_url=url_for(row["other_avatar_path"]),
        avatar_initials=initials_for(row["other_nickname"], row["other_email"]),
    )
