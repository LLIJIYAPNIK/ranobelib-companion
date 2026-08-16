"""Access to the ``comments`` table (migrations/0011_comments.sql) - PR 133's threaded
comments on a paragraph.

Keyed the same way PR 131/132 already identify a paragraph: slug_url/volume/number/
branch_id (denormalized, same reasoning as app/db/reactions.py - there's no local
chapters table to hold a real foreign key to) plus the paragraph's own index among
``.reader-content``'s children. ``parent_comment_id`` is a nullable self-reference,
present from the start rather than bolted on later - the roadmap is explicit that nested
replies are part of the initial feature, not a follow-up.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.auth.avatar import initials_for, url_for

MAX_COMMENT_LENGTH = 2000


@dataclass(frozen=True)
class Comment:
    id: int
    user_id: int
    author: str
    body: str
    created_at: str
    parent_comment_id: int | None
    # PR 147: the same picture-or-initials pairing avatar_url(user)/avatar_initials(user)
    # give Jinja templates, precomputed here since comments render client-side from JSON
    # rather than through those Jinja globals - avatar_initials is always present so the
    # client never needs its own copy of initials_for()'s segment-splitting logic.
    avatar_url: str | None
    avatar_initials: str
    # PR 150: the converted (GIF -> silent looping mp4, app/gif_video.py) attachment a
    # comment can carry - attachment_kind is "gif" for now; a bare filename under
    # Settings.comment_attachment_dir, not a URL (attachment_url below is what callers
    # actually render, same url_for()-style split as avatar_path/avatar_url).
    attachment_path: str | None = None
    attachment_kind: str | None = None
    replies: list[Comment] = field(default_factory=list)

    @property
    def attachment_url(self) -> str | None:
        if not self.attachment_path:
            return None
        return f"/comment-attachments/{self.attachment_path}"


def create_comment(
    conn: sqlite3.Connection,
    user_id: int,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
    body: str,
    parent_comment_id: int | None = None,
    attachment_path: str | None = None,
    attachment_kind: str | None = None,
) -> Comment:
    """Raises ValueError for an oversized body, or a `parent_comment_id` that isn't
    actually a comment on this same paragraph (a stale or tampered form field) - the
    caller (app/api/chapters.py) turns that into a 400, the same defensive shape as
    reactions' emoji-not-in-ALLOWED_EMOJI check.

    An empty body is only rejected when there's no `attachment_path` either (PR 150) - a
    comment can be just a GIF with no caption text, but never fully empty."""
    body = body.strip()
    if not body and not attachment_path:
        raise ValueError("Комментарий не может быть пустым")
    if len(body) > MAX_COMMENT_LENGTH:
        raise ValueError(f"Комментарий длиннее {MAX_COMMENT_LENGTH} символов")

    if parent_comment_id is not None:
        parent = conn.execute(
            "SELECT 1 FROM comments WHERE id = ? AND slug_url = ? AND volume = ? "
            "AND number = ? AND branch_id = ? AND paragraph_index = ?",
            (parent_comment_id, slug_url, volume, number, branch_id, paragraph_index),
        ).fetchone()
        if parent is None:
            raise ValueError("Комментарий, на который вы отвечаете, не найден")

    created_at = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        "INSERT INTO comments "
        "(user_id, slug_url, volume, number, branch_id, paragraph_index, "
        "parent_comment_id, body, created_at, attachment_path, attachment_kind) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            slug_url,
            volume,
            number,
            branch_id,
            paragraph_index,
            parent_comment_id,
            body,
            created_at,
            attachment_path,
            attachment_kind,
        ),
    )
    conn.commit()
    author_row = conn.execute(
        "SELECT nickname, email, avatar_path FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    author = (author_row["nickname"] or author_row["email"]) if author_row else "?"
    return Comment(
        id=cursor.lastrowid,
        user_id=user_id,
        author=author,
        body=body,
        created_at=created_at,
        parent_comment_id=parent_comment_id,
        attachment_path=attachment_path,
        attachment_kind=attachment_kind,
        avatar_url=url_for(author_row["avatar_path"]) if author_row else None,
        avatar_initials=initials_for(author_row["nickname"], author_row["email"])
        if author_row
        else "?",
    )


def list_comments_for_paragraph(
    conn: sqlite3.Connection,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
) -> list[Comment]:
    """Root comments with their replies already nested inside (reddit-style), not a flat
    list - the client renders exactly this shape with indentation by depth, no tree of
    its own to build."""
    rows = conn.execute(
        "SELECT comments.id, comments.user_id, comments.body, comments.created_at, "
        "comments.parent_comment_id, comments.attachment_path, comments.attachment_kind, "
        "users.nickname, users.email, users.avatar_path "
        "FROM comments JOIN users ON users.id = comments.user_id "
        "WHERE comments.slug_url = ? AND comments.volume = ? AND comments.number = ? "
        "AND comments.branch_id = ? AND comments.paragraph_index = ? "
        "ORDER BY comments.created_at ASC",
        (slug_url, volume, number, branch_id, paragraph_index),
    ).fetchall()

    nodes: dict[int, Comment] = {
        row["id"]: Comment(
            id=row["id"],
            user_id=row["user_id"],
            author=row["nickname"] or row["email"],
            body=row["body"],
            created_at=row["created_at"],
            parent_comment_id=row["parent_comment_id"],
            avatar_url=url_for(row["avatar_path"]),
            avatar_initials=initials_for(row["nickname"], row["email"]),
            attachment_path=row["attachment_path"],
            attachment_kind=row["attachment_kind"],
        )
        for row in rows
    }
    roots: list[Comment] = []
    for row in rows:
        node = nodes[row["id"]]
        parent_id = row["parent_comment_id"]
        # A parent outside this same paragraph can't happen through create_comment()'s own
        # validation above, but falling back to a root comment rather than raising keeps
        # this read path robust regardless (e.g. against a row from before that check
        # existed).
        if parent_id is not None and parent_id in nodes:
            nodes[parent_id].replies.append(node)
        else:
            roots.append(node)
    return roots


def count_comments_for_paragraph(
    conn: sqlite3.Connection,
    slug_url: str,
    volume: str,
    number: str,
    branch_id: str,
    paragraph_index: int,
) -> int:
    """Every comment under this paragraph, replies included - what "N комментариев" counts."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM comments "
        "WHERE slug_url = ? AND volume = ? AND number = ? AND branch_id = ? "
        "AND paragraph_index = ?",
        (slug_url, volume, number, branch_id, paragraph_index),
    ).fetchone()
    return row["n"]


def count_comments(
    conn: sqlite3.Connection, slug_url: str, volume: str, number: str, branch_id: str
) -> dict[int, int]:
    """Aggregated comment counts per paragraph for the whole chapter in one query - same
    "one bulk fetch on load, not one per paragraph" reasoning as
    app/db/reactions.py's count_reactions()."""
    rows = conn.execute(
        "SELECT paragraph_index, COUNT(*) AS n FROM comments "
        "WHERE slug_url = ? AND volume = ? AND number = ? AND branch_id = ? "
        "GROUP BY paragraph_index",
        (slug_url, volume, number, branch_id),
    ).fetchall()
    return {row["paragraph_index"]: row["n"] for row in rows}


def count_comments_by_user(conn: sqlite3.Connection, user_id: int) -> int:
    """Every comment this user has ever posted, across every title/chapter/paragraph -
    PR 135's profile page counter, not scoped to any one paragraph the way the two
    functions above are."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM comments WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["n"]
