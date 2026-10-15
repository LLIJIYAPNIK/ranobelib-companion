"""«Комментарии» (PR 350): the admin's list of every comment and one comment's context.

Read-only (PR 326's transaction with the admin timeout). Hiding, showing and deleting
for good live in app/db/comments.py next to the site's own edit/delete, and the route
records each in the action log.

Statuses, in the order they win: «удалён» (is_deleted - by the author, or wiped by
«Удалить навсегда» because replies hang under it), «скрыт» (hidden_at), «опубликован».
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from psycopg import AsyncConnection

from app.db.admin_data import _read_only

PAGE_SIZE = 25

PUBLISHED = "published"
HIDDEN = "hidden"
DELETED = "deleted"
STATUSES = (PUBLISHED, HIDDEN, DELETED)

ROOT = "root"
REPLY = "reply"

# Sort keys the list accepts -> the SQL they order by (a whitelist).
SORTS = {
    "id": "c.id",
    "created_at": "c.created_at",
    "reactions": "reactions",
}
DEFAULT_SORT = "created_at"

_STATUS_SQL = (
    "CASE WHEN c.is_deleted = 1 THEN 'deleted' "
    "WHEN c.hidden_at IS NOT NULL THEN 'hidden' ELSE 'published' END"
)
_STATUS_WHERE = {
    PUBLISHED: "c.is_deleted = 0 AND c.hidden_at IS NULL",
    HIDDEN: "c.is_deleted = 0 AND c.hidden_at IS NOT NULL",
    DELETED: "c.is_deleted = 1",
}
_FROM = (
    "FROM comments c JOIN users u ON u.id = c.user_id "
    "LEFT JOIN ("
    "  SELECT comment_id, COUNT(*) FILTER (WHERE value = 1) AS likes,"
    "  COUNT(*) FILTER (WHERE value = -1) AS dislikes"
    "  FROM comment_reactions GROUP BY comment_id"
    ") r ON r.comment_id = c.id "
)
_COLUMNS = (
    "c.id, c.user_id, c.body, c.attachment_kind, c.slug_url, c.volume, c.number, "
    "c.branch_id, c.paragraph_index, c.parent_comment_id, c.created_at, c.updated_at, "
    "c.hidden_at, u.nickname, u.email, COALESCE(r.likes, 0) AS likes, "
    f"COALESCE(r.dislikes, 0) AS dislikes, {_STATUS_SQL} AS status"
)


@dataclass(frozen=True)
class CommentRow:
    id: int
    user_id: int
    author: str  # nickname, or the email without one
    body: str
    attachment_kind: str | None
    slug_url: str
    volume: str
    number: str
    branch_id: str
    paragraph_index: int
    parent_comment_id: int | None
    created_at: str
    updated_at: str | None
    hidden_at: str | None
    likes: int
    dislikes: int
    status: str


@dataclass(frozen=True)
class CommentPage:
    rows: list[CommentRow]
    total: int
    page: int
    pages: int


def _row(row: dict) -> CommentRow:
    return CommentRow(
        id=row["id"],
        user_id=row["user_id"],
        author=row["nickname"] or row["email"],
        body=row["body"],
        attachment_kind=row["attachment_kind"],
        slug_url=row["slug_url"],
        volume=row["volume"],
        number=row["number"],
        branch_id=row["branch_id"],
        paragraph_index=row["paragraph_index"],
        parent_comment_id=row["parent_comment_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        hidden_at=row["hidden_at"],
        likes=row["likes"],
        dislikes=row["dislikes"],
        status=row["status"],
    )


def _where(query: str, status: str | None, kind: str | None) -> tuple[str, dict[str, object]]:
    conditions = []
    params: dict[str, object] = {}
    query = query.strip()
    if query:
        # position() rather than LIKE: nothing in the search text acts as a wildcard.
        search = (
            "position(%(q)s in lower(c.body)) > 0 OR position(%(q)s in lower(u.email)) > 0 "
            "OR position(%(q)s in lower(coalesce(u.nickname, ''))) > 0 "
            "OR position(%(q)s in lower(c.slug_url)) > 0"
        )
        params["q"] = query.lower()
        if query.isdigit() and len(query) <= 9:
            search += " OR c.id = %(qid)s"
            params["qid"] = int(query)
        conditions.append(f"({search})")
    if status in _STATUS_WHERE:
        conditions.append(_STATUS_WHERE[status])
    if kind == ROOT:
        conditions.append("c.parent_comment_id IS NULL")
    elif kind == REPLY:
        conditions.append("c.parent_comment_id IS NOT NULL")
    return (f"WHERE {' AND '.join(conditions)} " if conditions else ""), params


async def list_comments(
    conn: AsyncConnection,
    *,
    query: str = "",
    status: str | None = None,
    kind: str | None = None,
    sort: str = DEFAULT_SORT,
    descending: bool = True,
    page: int = 1,
) -> CommentPage:
    """One page of comments. The search matches the text, the author's email or
    nickname, the title's slug_url (case-insensitive substring) or, for digits, the
    comment's id."""
    where, params = _where(query, status, kind)
    order = SORTS.get(sort, SORTS[DEFAULT_SORT])
    direction = "DESC" if descending else "ASC"
    async with _read_only(conn):
        cursor = await conn.execute(f"SELECT COUNT(*) AS n {_FROM}{where}", params)
        total = (await cursor.fetchone())["n"]
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(max(page, 1), pages)
        cursor = await conn.execute(
            f"SELECT {_COLUMNS}, COALESCE(r.likes, 0) + COALESCE(r.dislikes, 0) AS reactions "
            f"{_FROM}{where}ORDER BY {order} {direction} NULLS LAST, c.id DESC "
            "LIMIT %(limit)s OFFSET %(offset)s",
            {**params, "limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE},
        )
        rows = await cursor.fetchall()
    return CommentPage(rows=[_row(row) for row in rows], total=total, page=page, pages=pages)


@dataclass(frozen=True)
class AuthorHistory:
    """The drawer's «Автор»: since when, how many comments, how many hidden."""

    user_id: int
    created_at: str
    comments: int
    hidden: int


@dataclass(frozen=True)
class CommentDetail:
    comment: CommentRow
    parent: CommentRow | None  # what it answers, if it's a reply
    replies: int
    author: AuthorHistory


async def comment_detail(conn: AsyncConnection, comment_id: int) -> CommentDetail | None:
    async with _read_only(conn):
        cursor = await conn.execute(f"SELECT {_COLUMNS} {_FROM}WHERE c.id = %s", (comment_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        comment = _row(row)
        parent = None
        if comment.parent_comment_id is not None:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} {_FROM}WHERE c.id = %s", (comment.parent_comment_id,)
            )
            parent_row = await cursor.fetchone()
            parent = _row(parent_row) if parent_row else None
        cursor = await conn.execute(
            "SELECT u.created_at, "
            "(SELECT COUNT(*) FROM comments WHERE parent_comment_id = %(id)s) AS replies, "
            "(SELECT COUNT(*) FROM comments WHERE user_id = u.id) AS comments, "
            "(SELECT COUNT(*) FROM comments WHERE user_id = u.id "
            "  AND hidden_at IS NOT NULL) AS hidden "
            "FROM users u WHERE u.id = %(user)s",
            {"id": comment.id, "user": comment.user_id},
        )
        extra = await cursor.fetchone()
    return CommentDetail(
        comment=comment,
        parent=parent,
        replies=extra["replies"],
        author=AuthorHistory(
            user_id=comment.user_id,
            created_at=extra["created_at"],
            comments=extra["comments"],
            hidden=extra["hidden"],
        ),
    )
