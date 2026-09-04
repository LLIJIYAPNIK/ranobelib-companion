"""Access to the ``users`` table (see migrations/0001_users.sql,
0009_users_privacy_flags.sql for the ``show_*`` columns,
0015_users_notification_flags.sql for ``notifications_enabled``/``do_not_disturb``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection


@dataclass(frozen=True)
class User:
    id: int
    email: str
    password_hash: str
    created_at: str
    nickname: str | None = None
    bio: str | None = None
    avatar_path: str | None = None
    # PR 124: what a *different*, non-owner visitor sees on this user's public /profile
    # page (PR 122) - the owner's own view of their profile always ignores these. Default
    # True (show everything) so PR 122's existing behavior doesn't change for anyone who
    # has never opened the privacy settings.
    show_currently_reading: bool = True
    show_favorite: bool = True
    show_library: bool = True
    # PR 171: "Показывать уведомления" (hides the sidebar bell entirely) and
    # "Не беспокоить" (hides it temporarily) - see update_notification_settings() below
    # for what each one actually does (both gate visibility identically; neither pauses
    # notify_comment_reaction() itself writing new rows). Default True/False so nobody who
    # has never opened this settings section sees any change in behavior.
    notifications_enabled: bool = True
    do_not_disturb: bool = False


async def create_user(
    conn: AsyncConnection, email: str, password_hash: str, nickname: str | None = None
) -> User:
    """Raises ``psycopg.errors.UniqueViolation`` if `email` is already registered, or if
    `nickname` (case-insensitively, see migrations/0017_users_nickname_unique.sql) is
    already taken by another account - callers (see app/api/auth.py) are expected to check
    `get_user_by_email`/`get_user_by_nickname` first and turn that into a form error, but
    the UNIQUE constraints are the actual source of truth against a race between two
    concurrent registrations. A `nickname` of ``None`` (not set) never collides with
    another ``None`` - the index is a partial one, ``WHERE nickname IS NOT NULL``."""
    email = _normalize_email(email)
    created_at = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "INSERT INTO users (email, password_hash, created_at, nickname) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (email, password_hash, created_at, nickname),
    )
    row = await cursor.fetchone()
    assert row is not None  # just inserted
    return User(
        id=row["id"],
        email=email,
        password_hash=password_hash,
        created_at=created_at,
        nickname=nickname,
    )


async def update_user_account(
    conn: AsyncConnection,
    user_id: int,
    *,
    email: str,
    nickname: str | None,
    bio: str | None,
) -> User:
    """Raises ``psycopg.errors.UniqueViolation`` if `email` or `nickname` collides with a
    *different* account - same defense-in-depth as `create_user`: callers (see
    app/api/settings.py) check `get_user_by_email`/`get_user_by_nickname` first and turn
    that into a form error, this is the race-safe backstop."""
    await conn.execute(
        "UPDATE users SET email = %s, nickname = %s, bio = %s WHERE id = %s",
        (_normalize_email(email), nickname, bio, user_id),
    )
    user = await get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


async def update_user_avatar(conn: AsyncConnection, user_id: int, avatar_path: str) -> User:
    """Called after a validated image has already been written to disk (see
    `app/auth/avatar.py`'s `save_avatar`) - this just records the resulting filename."""
    await conn.execute("UPDATE users SET avatar_path = %s WHERE id = %s", (avatar_path, user_id))
    user = await get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


async def update_user_password(conn: AsyncConnection, user_id: int, password_hash: str) -> User:
    """No uniqueness concerns here unlike `update_user_account` - `password_hash` isn't a
    unique column, so there's nothing to check before writing it. Callers (see
    app/api/settings.py) are expected to have already verified the visitor's *current*
    password with `verify_password` before calling this."""
    await conn.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id)
    )
    user = await get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


async def update_privacy_settings(
    conn: AsyncConnection,
    user_id: int,
    *,
    show_currently_reading: bool,
    show_favorite: bool,
    show_library: bool,
) -> User:
    """The three "Приватность" toggles (PR 124) - independent of each other, so all three
    are always written together as a plain replace, not a partial update."""
    await conn.execute(
        "UPDATE users SET show_currently_reading = %s, show_favorite = %s, show_library = %s "
        "WHERE id = %s",
        # cast bool -> int: the column is INTEGER (0/1), not a native Postgres BOOLEAN -
        # see CLAUDE.md's PR 191 entry on why that stayed as-is - and unlike SQLite,
        # Postgres won't implicitly coerce a bound `True`/`False` into one.
        (int(show_currently_reading), int(show_favorite), int(show_library), user_id),
    )
    user = await get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


async def update_notification_settings(
    conn: AsyncConnection,
    user_id: int,
    *,
    notifications_enabled: bool,
    do_not_disturb: bool,
) -> User:
    """The "Уведомления" settings section's two toggles (PR 171) - same "always write
    both together, not a partial update" shape as update_privacy_settings() above.

    Both flags only ever gate whether base.html renders the sidebar bell/panel -
    notify_comment_reaction() (app/db/notifications.py) writes a row regardless of either
    one. A reaction to someone's comment is a fact that already happened; pausing
    generation while a visitor has either flag set would mean it's gone for good once
    they turn the flag back off (there's no "catch up on what happened while muted" here),
    which is worse than just not showing it live. "Показывать уведомления" and
    "Не беспокоить" end up controlling the exact same thing today (there's no snooze
    timer/schedule yet) - the distinction is only in the settings copy (a standing
    preference vs. a temporary one), not in what either does."""
    await conn.execute(
        "UPDATE users SET notifications_enabled = %s, do_not_disturb = %s WHERE id = %s",
        (int(notifications_enabled), int(do_not_disturb), user_id),
    )
    user = await get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


_USER_COLUMNS = (
    "id, email, password_hash, created_at, nickname, bio, avatar_path, "
    "show_currently_reading, show_favorite, show_library, "
    "notifications_enabled, do_not_disturb"
)


async def get_user_by_email(conn: AsyncConnection, email: str) -> User | None:
    cursor = await conn.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE email = %s",
        (_normalize_email(email),),
    )
    row = await cursor.fetchone()
    return _row_to_user(row) if row is not None else None


async def get_user_by_nickname(conn: AsyncConnection, nickname: str) -> User | None:
    """Case-insensitive, matching the partial unique index (0017) this backs - two
    accounts can't hold "Nick" and "nick" any more than they could hold the exact same
    string."""
    cursor = await conn.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE LOWER(nickname) = LOWER(%s)",
        (nickname,),
    )
    row = await cursor.fetchone()
    return _row_to_user(row) if row is not None else None


async def get_user_by_id(conn: AsyncConnection, user_id: int) -> User | None:
    cursor = await conn.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s",
        (user_id,),
    )
    row = await cursor.fetchone()
    return _row_to_user(row) if row is not None else None


def _row_to_user(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
        nickname=row["nickname"],
        bio=row["bio"],
        avatar_path=row["avatar_path"],
        show_currently_reading=bool(row["show_currently_reading"]),
        show_favorite=bool(row["show_favorite"]),
        show_library=bool(row["show_library"]),
        notifications_enabled=bool(row["notifications_enabled"]),
        do_not_disturb=bool(row["do_not_disturb"]),
    )


def _normalize_email(email: str) -> str:
    return email.strip().lower()
