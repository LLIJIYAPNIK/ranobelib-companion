"""Access to the ``users`` table (see migrations/0001_users.sql,
0009_users_privacy_flags.sql for the ``show_*`` columns,
0015_users_notification_flags.sql for ``notifications_enabled``/``do_not_disturb``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


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


def create_user(
    conn: sqlite3.Connection, email: str, password_hash: str, nickname: str | None = None
) -> User:
    """Raises ``sqlite3.IntegrityError`` if `email` is already registered - callers
    (see app/api/auth.py) are expected to check `get_user_by_email` first and turn that
    into a form error, but the UNIQUE constraint is the actual source of truth against
    a races between two concurrent registrations. `nickname` reuses the same column
    `update_user_account` (PR 90) writes to - same non-unique reasoning applies here,
    nothing looks a user up by it (PR 105 in CLAUDE.md's roadmap)."""
    email = _normalize_email(email)
    created_at = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        "INSERT INTO users (email, password_hash, created_at, nickname) VALUES (?, ?, ?, ?)",
        (email, password_hash, created_at, nickname),
    )
    conn.commit()
    return User(
        id=cursor.lastrowid,
        email=email,
        password_hash=password_hash,
        created_at=created_at,
        nickname=nickname,
    )


def update_user_account(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    email: str,
    nickname: str | None,
    bio: str | None,
) -> User:
    """Raises ``sqlite3.IntegrityError`` if `email` collides with a *different* account -
    same defense-in-depth as `create_user`: callers (see app/api/settings.py) check
    `get_user_by_email` first and turn that into a form error, this is the race-safe
    backstop. Unlike email, `nickname` isn't unique (see PR 90 in CLAUDE.md's roadmap -
    nothing in this app looks a user up by nickname, so uniqueness would be an
    artificial restriction)."""
    conn.execute(
        "UPDATE users SET email = ?, nickname = ?, bio = ? WHERE id = ?",
        (_normalize_email(email), nickname, bio, user_id),
    )
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


def update_user_avatar(conn: sqlite3.Connection, user_id: int, avatar_path: str) -> User:
    """Called after a validated image has already been written to disk (see
    `app/auth/avatar.py`'s `save_avatar`) - this just records the resulting filename."""
    conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_path, user_id))
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


def update_user_password(conn: sqlite3.Connection, user_id: int, password_hash: str) -> User:
    """No uniqueness concerns here unlike `update_user_account` - `password_hash` isn't a
    unique column, so there's nothing to check before writing it. Callers (see
    app/api/settings.py) are expected to have already verified the visitor's *current*
    password with `verify_password` before calling this."""
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


def update_privacy_settings(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    show_currently_reading: bool,
    show_favorite: bool,
    show_library: bool,
) -> User:
    """The three "Приватность" toggles (PR 124) - independent of each other, so all three
    are always written together as a plain replace, not a partial update."""
    conn.execute(
        "UPDATE users SET show_currently_reading = ?, show_favorite = ?, show_library = ? "
        "WHERE id = ?",
        (show_currently_reading, show_favorite, show_library, user_id),
    )
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


def update_notification_settings(
    conn: sqlite3.Connection,
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
    conn.execute(
        "UPDATE users SET notifications_enabled = ?, do_not_disturb = ? WHERE id = ?",
        (notifications_enabled, do_not_disturb, user_id),
    )
    conn.commit()
    user = get_user_by_id(conn, user_id)
    assert user is not None  # just updated
    return user


_USER_COLUMNS = (
    "id, email, password_hash, created_at, nickname, bio, avatar_path, "
    "show_currently_reading, show_favorite, show_library, "
    "notifications_enabled, do_not_disturb"
)


def get_user_by_email(conn: sqlite3.Connection, email: str) -> User | None:
    row = conn.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE email = ?",
        (_normalize_email(email),),
    ).fetchone()
    return _row_to_user(row) if row is not None else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return _row_to_user(row) if row is not None else None


def _row_to_user(row: sqlite3.Row) -> User:
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
