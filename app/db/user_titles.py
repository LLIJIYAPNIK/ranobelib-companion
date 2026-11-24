"""Database access for user-uploaded EPUB titles (issues 432 and 434).

Visibility model:
- ``private`` (default): accessible only to the owner (``t.user_id = user_id``).
- ``published``: visible in ``/community-titles`` and readable by other users and guests;
  mutations (edit, unpublish, delete) remain strictly owner-only.
- ``hidden``: hidden by a moderator in ``/admin``; inaccessible to non-owners (404), and
  cannot be re-published by the owner.

Per-reader reading progress lives in ``user_title_reading_progress`` keyed by
``(user_id, title_id)`` so readers of a published book never overwrite each other's or the
owner's reading position.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLISHED = "published"
VISIBILITY_HIDDEN = "hidden"

COMMUNITY_SORTS = ("newest", "title", "author")
COMMUNITY_DEFAULT_SORT = "newest"


@dataclass(frozen=True)
class UserTitle:
    id: uuid.UUID
    user_id: int
    title: str
    description: str | None
    authors: list[str]
    cover_storage_key: str | None
    cover_media_type: str | None
    last_read_position: int | None
    last_read_at: str | None
    created_at: str
    updated_at: str
    chapter_count: int = 0
    visibility: str = VISIBILITY_PRIVATE
    published_at: str | None = None
    hidden_reason: str | None = None
    hidden_at: str | None = None
    allow_download: bool = False
    owner_name: str | None = None
    owner_avatar_path: str | None = None


@dataclass(frozen=True)
class UserTitleFile:
    id: uuid.UUID
    title_id: uuid.UUID
    storage_key: str
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    import_status: str
    import_error: str | None
    is_active: bool
    uploaded_at: str


@dataclass(frozen=True)
class UserTitleChapterSummary:
    id: uuid.UUID
    title_id: uuid.UUID
    position: int
    title: str
    source_href: str


@dataclass(frozen=True)
class UserTitleChapter:
    id: uuid.UUID
    title_id: uuid.UUID
    position: int
    title: str
    content: str
    source_href: str


@dataclass(frozen=True)
class UserTitleAsset:
    id: uuid.UUID
    title_id: uuid.UUID
    storage_key: str
    media_type: str
    size_bytes: int
    source_href: str
    created_at: str


_PUBLIC_OWNER_EXPR = "COALESCE(NULLIF(BTRIM(u.nickname), ''), 'Пользователь #' || u.id::text)"


async def create_user_title_bundle(
    conn: AsyncConnection[Any],
    *,
    title_id: uuid.UUID,
    user_id: int,
    title: str,
    description: str | None,
    authors: list[str],
    cover_storage_key: str | None,
    cover_media_type: str | None,
    file_id: uuid.UUID,
    file_storage_key: str,
    original_filename: str,
    file_media_type: str,
    size_bytes: int,
    sha256: str,
    chapters: list[tuple[uuid.UUID, int, str, str, str]],
    assets: list[tuple[uuid.UUID, str, str, int, str]],
) -> UserTitle:
    """Atomically inserts a private user title, its active EPUB file, chapters and assets.

    Runs inside a single Postgres transaction so any failure rolls back completely without
    leaving orphaned rows. Transitions ``import_status`` from ``pending`` to ``ready``
    before committing.
    """
    now = datetime.now(UTC).isoformat()
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO user_titles "
            "(id, user_id, title, description, authors, cover_storage_key, cover_media_type, "
            "visibility, allow_download, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'private', FALSE, %s, %s)",
            (
                title_id,
                user_id,
                title,
                description,
                Jsonb(authors),
                cover_storage_key,
                cover_media_type,
                now,
                now,
            ),
        )
        await conn.execute(
            "INSERT INTO user_title_files "
            "(id, title_id, storage_key, original_filename, media_type, size_bytes, sha256, "
            "import_status, is_active, uploaded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', TRUE, %s)",
            (
                file_id,
                title_id,
                file_storage_key,
                original_filename,
                file_media_type,
                size_bytes,
                sha256,
                now,
            ),
        )
        if chapters:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO user_title_chapters "
                    "(id, title_id, position, title, content, source_href) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [
                        (chap_id, title_id, pos, chap_title, content, source_href)
                        for chap_id, pos, chap_title, content, source_href in chapters
                    ],
                )
        if assets:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO user_title_assets "
                    "(id, title_id, storage_key, media_type, size_bytes, source_href, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        (asset_id, title_id, storage_key, media_type, asset_size, source_href, now)
                        for asset_id, storage_key, media_type, asset_size, source_href in assets
                    ],
                )
        await conn.execute(
            "UPDATE user_title_files SET import_status = 'ready' WHERE id = %s",
            (file_id,),
        )
    return UserTitle(
        id=title_id,
        user_id=user_id,
        title=title,
        description=description,
        authors=list(authors),
        cover_storage_key=cover_storage_key,
        cover_media_type=cover_media_type,
        last_read_position=None,
        last_read_at=None,
        created_at=now,
        updated_at=now,
        chapter_count=len(chapters),
        visibility=VISIBILITY_PRIVATE,
        published_at=None,
        hidden_reason=None,
        hidden_at=None,
        allow_download=False,
    )


# --- Owner-scoped queries (/my-titles) ----------------------------------------------------


async def list_user_titles(conn: AsyncConnection[Any], user_id: int) -> list[UserTitle]:
    """Lists ``user_id``'s own uploaded titles, most recently updated first."""
    cursor = await conn.execute(
        f"SELECT t.*, {_PUBLIC_OWNER_EXPR} AS owner_name, u.avatar_path AS owner_avatar_path, "
        "p.last_read_position AS reader_last_read_position, "
        "p.last_read_at AS reader_last_read_at, "
        "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) AS chapter_count "
        "FROM user_titles t "
        "JOIN users u ON u.id = t.user_id "
        "LEFT JOIN user_title_reading_progress p ON p.title_id = t.id AND p.user_id = %s "
        "WHERE t.user_id = %s "
        "ORDER BY t.updated_at DESC",
        (user_id, user_id),
    )
    rows = await cursor.fetchall()
    return [_row_to_title(row) for row in rows]


async def get_user_title(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID
) -> UserTitle | None:
    cursor = await conn.execute(
        f"SELECT t.*, {_PUBLIC_OWNER_EXPR} AS owner_name, u.avatar_path AS owner_avatar_path, "
        "p.last_read_position AS reader_last_read_position, "
        "p.last_read_at AS reader_last_read_at, "
        "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) AS chapter_count "
        "FROM user_titles t "
        "JOIN users u ON u.id = t.user_id "
        "LEFT JOIN user_title_reading_progress p ON p.title_id = t.id AND p.user_id = %s "
        "WHERE t.id = %s AND t.user_id = %s",
        (user_id, title_id, user_id),
    )
    row = await cursor.fetchone()
    return _row_to_title(row) if row is not None else None


async def get_active_title_file(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID
) -> UserTitleFile | None:
    cursor = await conn.execute(
        "SELECT f.* FROM user_title_files f "
        "JOIN user_titles t ON t.id = f.title_id "
        "WHERE f.title_id = %s AND t.user_id = %s AND f.is_active = TRUE",
        (title_id, user_id),
    )
    row = await cursor.fetchone()
    return _row_to_file(row) if row is not None else None


async def list_chapter_summaries(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID
) -> list[UserTitleChapterSummary]:
    cursor = await conn.execute(
        "SELECT c.id, c.title_id, c.position, c.title, c.source_href "
        "FROM user_title_chapters c "
        "JOIN user_titles t ON t.id = c.title_id "
        "WHERE c.title_id = %s AND t.user_id = %s "
        "ORDER BY c.position ASC",
        (title_id, user_id),
    )
    rows = await cursor.fetchall()
    return [
        UserTitleChapterSummary(
            id=_as_uuid(row["id"]),
            title_id=_as_uuid(row["title_id"]),
            position=row["position"],
            title=row["title"],
            source_href=row["source_href"],
        )
        for row in rows
    ]


async def get_chapter(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID, position: int
) -> UserTitleChapter | None:
    cursor = await conn.execute(
        "SELECT c.* FROM user_title_chapters c "
        "JOIN user_titles t ON t.id = c.title_id "
        "WHERE c.title_id = %s AND t.user_id = %s AND c.position = %s",
        (title_id, user_id, position),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return UserTitleChapter(
        id=_as_uuid(row["id"]),
        title_id=_as_uuid(row["title_id"]),
        position=row["position"],
        title=row["title"],
        content=row["content"],
        source_href=row["source_href"],
    )


async def get_title_asset(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID, asset_id: uuid.UUID
) -> UserTitleAsset | None:
    cursor = await conn.execute(
        "SELECT a.* FROM user_title_assets a "
        "JOIN user_titles t ON t.id = a.title_id "
        "WHERE a.id = %s AND a.title_id = %s AND t.user_id = %s",
        (asset_id, title_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return UserTitleAsset(
        id=_as_uuid(row["id"]),
        title_id=_as_uuid(row["title_id"]),
        storage_key=row["storage_key"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        source_href=row["source_href"],
        created_at=row["created_at"],
    )


async def update_user_title_metadata(
    conn: AsyncConnection[Any],
    *,
    user_id: int,
    title_id: uuid.UUID,
    title: str,
    authors: list[str],
    description: str | None,
) -> UserTitle | None:
    now = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "UPDATE user_titles "
        "SET title = %s, authors = %s, description = %s, updated_at = %s "
        "WHERE id = %s AND user_id = %s "
        "RETURNING id",
        (title, Jsonb(authors), description, now, title_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return await get_user_title(conn, user_id, title_id)


async def set_user_title_published(
    conn: AsyncConnection[Any],
    user_id: int,
    title_id: uuid.UUID,
    *,
    allow_download: bool = False,
) -> UserTitle | None:
    """Publishes an owner's non-hidden title (private -> published or updates allow_download)."""
    now = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "UPDATE user_titles "
        "SET visibility = 'published', "
        "    published_at = COALESCE(published_at, %s), "
        "    allow_download = %s, "
        "    updated_at = %s "
        "WHERE id = %s AND user_id = %s AND visibility <> 'hidden' "
        "RETURNING id",
        (now, allow_download, now, title_id, user_id),
    )
    if await cursor.fetchone() is None:
        return None
    return await get_user_title(conn, user_id, title_id)


async def set_user_title_private(
    conn: AsyncConnection[Any],
    user_id: int,
    title_id: uuid.UUID,
) -> UserTitle | None:
    """Unpublishes an owner's published title back to private. Never changes 'hidden'."""
    now = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "UPDATE user_titles "
        "SET visibility = 'private', updated_at = %s "
        "WHERE id = %s AND user_id = %s AND visibility = 'published' "
        "RETURNING id",
        (now, title_id, user_id),
    )
    if await cursor.fetchone() is None:
        return None
    return await get_user_title(conn, user_id, title_id)


async def set_user_title_allow_download(
    conn: AsyncConnection[Any],
    user_id: int,
    title_id: uuid.UUID,
    *,
    allow_download: bool,
) -> UserTitle | None:
    """Updates whether other readers may download the source EPUB of a published title."""
    now = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "UPDATE user_titles "
        "SET allow_download = %s, updated_at = %s "
        "WHERE id = %s AND user_id = %s "
        "RETURNING id",
        (allow_download, now, title_id, user_id),
    )
    if await cursor.fetchone() is None:
        return None
    return await get_user_title(conn, user_id, title_id)


# --- Per-reader reading progress ----------------------------------------------------------


async def record_user_title_read(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID, position: int
) -> None:
    """Records ``user_id``'s personal reading position for ``title_id``.

    Stored per ``(user_id, title_id)`` in ``user_title_reading_progress`` so reading a
    published book never affects the owner's or other readers' progress.
    """
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT INTO user_title_reading_progress "
        "(user_id, title_id, last_read_position, last_read_at) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (user_id, title_id) "
        "DO UPDATE SET last_read_position = EXCLUDED.last_read_position, "
        "              last_read_at = EXCLUDED.last_read_at",
        (user_id, title_id, position, now),
    )


async def get_reading_progress(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID
) -> tuple[int, str] | None:
    cursor = await conn.execute(
        "SELECT last_read_position, last_read_at FROM user_title_reading_progress "
        "WHERE user_id = %s AND title_id = %s",
        (user_id, title_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return int(row["last_read_position"]), str(row["last_read_at"])


# --- Public / Community queries (/community-titles, /profile) -----------------------------


async def list_published_user_titles(
    conn: AsyncConnection[Any],
    *,
    query: str = "",
    sort: str = COMMUNITY_DEFAULT_SORT,
    page: int = 1,
    page_size: int = 24,
    viewer_user_id: int | None = None,
) -> tuple[list[UserTitle], int]:
    """Lists publicly visible (``visibility = 'published'``) user titles with search & sort."""
    sort_key = sort if sort in COMMUNITY_SORTS else COMMUNITY_DEFAULT_SORT
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    conditions = ["t.visibility = 'published'"]
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    clean_query = query.strip()
    if clean_query:
        conditions.append(
            "(position(%(q)s in lower(t.title)) > 0 "
            "OR position(%(q)s in lower(t.authors::text)) > 0 "
            "OR position(%(q)s in lower(COALESCE(t.description, ''))) > 0 "
            "OR position(%(q)s in lower(COALESCE(u.nickname, ''))) > 0)"
        )
        params["q"] = clean_query.lower()

    where_sql = " AND ".join(conditions)
    order_sql = {
        "newest": "t.published_at DESC NULLS LAST, t.created_at DESC, t.id DESC",
        "title": "lower(t.title) ASC, t.published_at DESC NULLS LAST",
        "author": "lower(COALESCE(t.authors->>0, '')) ASC, lower(t.title) ASC",
    }[sort_key]

    count_cur = await conn.execute(
        f"SELECT COUNT(*) AS n FROM user_titles t JOIN users u ON u.id = t.user_id "
        f"WHERE {where_sql}",
        params,
    )
    total = int((await count_cur.fetchone())["n"])

    if viewer_user_id is not None:
        params["viewer_id"] = viewer_user_id
        progress_select = (
            "p.last_read_position AS reader_last_read_position, "
            "p.last_read_at AS reader_last_read_at, "
        )
        progress_join = (
            "LEFT JOIN user_title_reading_progress p "
            "ON p.title_id = t.id AND p.user_id = %(viewer_id)s "
        )
    else:
        progress_select = (
            "NULL::integer AS reader_last_read_position, "
            "NULL::text AS reader_last_read_at, "
        )
        progress_join = ""

    cursor = await conn.execute(
        f"SELECT t.*, {_PUBLIC_OWNER_EXPR} AS owner_name, u.avatar_path AS owner_avatar_path, "
        f"{progress_select}"
        "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) AS chapter_count "
        "FROM user_titles t "
        "JOIN users u ON u.id = t.user_id "
        f"{progress_join}"
        f"WHERE {where_sql} "
        f"ORDER BY {order_sql} "
        "LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_title(row) for row in rows], total


async def list_published_titles_by_user(
    conn: AsyncConnection[Any], owner_user_id: int, *, limit: int = 12
) -> list[UserTitle]:
    """Published books owned by ``owner_user_id`` (shown on their profile)."""
    cursor = await conn.execute(
        f"SELECT t.*, {_PUBLIC_OWNER_EXPR} AS owner_name, u.avatar_path AS owner_avatar_path, "
        "NULL::integer AS reader_last_read_position, "
        "NULL::text AS reader_last_read_at, "
        "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) AS chapter_count "
        "FROM user_titles t "
        "JOIN users u ON u.id = t.user_id "
        "WHERE t.user_id = %s AND t.visibility = 'published' "
        "ORDER BY t.published_at DESC NULLS LAST, t.created_at DESC "
        "LIMIT %s",
        (owner_user_id, limit),
    )
    rows = await cursor.fetchall()
    return [_row_to_title(row) for row in rows]


async def get_published_user_title(
    conn: AsyncConnection[Any],
    title_id: uuid.UUID,
    *,
    viewer_user_id: int | None = None,
) -> UserTitle | None:
    """Returns a published title by UUID, with ``viewer_user_id``'s personal progress."""
    if viewer_user_id is not None:
        cursor = await conn.execute(
            f"SELECT t.*, {_PUBLIC_OWNER_EXPR} AS owner_name, u.avatar_path AS owner_avatar_path, "
            "p.last_read_position AS reader_last_read_position, "
            "p.last_read_at AS reader_last_read_at, "
            "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) "
            "AS chapter_count "
            "FROM user_titles t "
            "JOIN users u ON u.id = t.user_id "
            "LEFT JOIN user_title_reading_progress p "
            "ON p.title_id = t.id AND p.user_id = %s "
            "WHERE t.id = %s AND t.visibility = 'published'",
            (viewer_user_id, title_id),
        )
    else:
        cursor = await conn.execute(
            f"SELECT t.*, {_PUBLIC_OWNER_EXPR} AS owner_name, u.avatar_path AS owner_avatar_path, "
            "NULL::integer AS reader_last_read_position, "
            "NULL::text AS reader_last_read_at, "
            "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) "
            "AS chapter_count "
            "FROM user_titles t "
            "JOIN users u ON u.id = t.user_id "
            "WHERE t.id = %s AND t.visibility = 'published'",
            (title_id,),
        )
    row = await cursor.fetchone()
    return _row_to_title(row) if row is not None else None


async def list_published_chapter_summaries(
    conn: AsyncConnection[Any], title_id: uuid.UUID
) -> list[UserTitleChapterSummary]:
    cursor = await conn.execute(
        "SELECT c.id, c.title_id, c.position, c.title, c.source_href "
        "FROM user_title_chapters c "
        "JOIN user_titles t ON t.id = c.title_id "
        "WHERE c.title_id = %s AND t.visibility = 'published' "
        "ORDER BY c.position ASC",
        (title_id,),
    )
    rows = await cursor.fetchall()
    return [
        UserTitleChapterSummary(
            id=_as_uuid(row["id"]),
            title_id=_as_uuid(row["title_id"]),
            position=row["position"],
            title=row["title"],
            source_href=row["source_href"],
        )
        for row in rows
    ]


async def get_published_chapter(
    conn: AsyncConnection[Any], title_id: uuid.UUID, position: int
) -> UserTitleChapter | None:
    cursor = await conn.execute(
        "SELECT c.* FROM user_title_chapters c "
        "JOIN user_titles t ON t.id = c.title_id "
        "WHERE c.title_id = %s AND t.visibility = 'published' AND c.position = %s",
        (title_id, position),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return UserTitleChapter(
        id=_as_uuid(row["id"]),
        title_id=_as_uuid(row["title_id"]),
        position=row["position"],
        title=row["title"],
        content=row["content"],
        source_href=row["source_href"],
    )


async def get_published_title_asset(
    conn: AsyncConnection[Any], title_id: uuid.UUID, asset_id: uuid.UUID
) -> UserTitleAsset | None:
    cursor = await conn.execute(
        "SELECT a.* FROM user_title_assets a "
        "JOIN user_titles t ON t.id = a.title_id "
        "WHERE a.id = %s AND a.title_id = %s AND t.visibility = 'published'",
        (asset_id, title_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return UserTitleAsset(
        id=_as_uuid(row["id"]),
        title_id=_as_uuid(row["title_id"]),
        storage_key=row["storage_key"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        source_href=row["source_href"],
        created_at=row["created_at"],
    )


async def get_published_active_title_file(
    conn: AsyncConnection[Any], title_id: uuid.UUID
) -> UserTitleFile | None:
    cursor = await conn.execute(
        "SELECT f.* FROM user_title_files f "
        "JOIN user_titles t ON t.id = f.title_id "
        "WHERE f.title_id = %s AND t.visibility = 'published' AND f.is_active = TRUE",
        (title_id,),
    )
    row = await cursor.fetchone()
    return _row_to_file(row) if row is not None else None


# --- Moderator / Admin helpers ------------------------------------------------------------


async def get_user_title_for_admin(
    conn: AsyncConnection[Any], title_id: uuid.UUID
) -> UserTitle | None:
    """Admin-only lookup regardless of visibility or owner."""
    cursor = await conn.execute(
        "SELECT t.*, COALESCE(u.nickname, u.email) AS owner_name, "
        "u.avatar_path AS owner_avatar_path, "
        "NULL::integer AS reader_last_read_position, "
        "NULL::text AS reader_last_read_at, "
        "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) AS chapter_count "
        "FROM user_titles t "
        "JOIN users u ON u.id = t.user_id "
        "WHERE t.id = %s",
        (title_id,),
    )
    row = await cursor.fetchone()
    return _row_to_title(row) if row is not None else None


async def hide_user_title_by_moderator(
    conn: AsyncConnection[Any], title_id: uuid.UUID, *, reason: str | None = None
) -> tuple[UserTitle, str, str | None] | None:
    """Marks a title as ``hidden`` by a moderator and returns ``(updated, prev_visibility,
    prev_hidden_reason)``. Returns ``None`` if the title does not exist."""
    clean_reason = (reason or "").strip() or None
    now = datetime.now(UTC).isoformat()
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT visibility, hidden_reason FROM user_titles WHERE id = %s FOR UPDATE",
            (title_id,),
        )
        existing = await cur.fetchone()
        if existing is None:
            return None
        prev_visibility = str(existing["visibility"])
        prev_reason = existing["hidden_reason"]
        await conn.execute(
            "UPDATE user_titles "
            "SET visibility = 'hidden', hidden_at = %s, hidden_reason = %s, updated_at = %s "
            "WHERE id = %s",
            (now, clean_reason, now, title_id),
        )
    updated = await get_user_title_for_admin(conn, title_id)
    assert updated is not None
    return updated, prev_visibility, prev_reason


async def restore_user_title_by_moderator(
    conn: AsyncConnection[Any],
    title_id: uuid.UUID,
    *,
    target_visibility: str = VISIBILITY_PUBLISHED,
) -> UserTitle | None:
    """Reverts ``hide_user_title_by_moderator()`` (e.g. moderation Undo)."""
    if target_visibility not in (VISIBILITY_PRIVATE, VISIBILITY_PUBLISHED):
        target_visibility = VISIBILITY_PRIVATE
    now = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "UPDATE user_titles "
        "SET visibility = %s, hidden_at = NULL, hidden_reason = NULL, updated_at = %s "
        "WHERE id = %s AND visibility = 'hidden' "
        "RETURNING id",
        (target_visibility, now, title_id),
    )
    if await cursor.fetchone() is None:
        return None
    return await get_user_title_for_admin(conn, title_id)


# --- Deletion -----------------------------------------------------------------------------


async def delete_user_title_and_collect_keys(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID
) -> list[str] | None:
    """Deletes ``title_id`` (if owned by ``user_id``) in a single transaction and returns all
    associated storage keys (cover, EPUB files, chapter assets) to remove from storage.

    Returns ``None`` if the title does not exist or does not belong to ``user_id``.
    """
    async with conn.transaction():
        cursor = await conn.execute(
            "SELECT cover_storage_key FROM user_titles WHERE id = %s AND user_id = %s FOR UPDATE",
            (title_id, user_id),
        )
        title_row = await cursor.fetchone()
        if title_row is None:
            return None

        keys: list[str] = []
        if title_row["cover_storage_key"]:
            keys.append(title_row["cover_storage_key"])

        files_cur = await conn.execute(
            "SELECT storage_key FROM user_title_files WHERE title_id = %s",
            (title_id,),
        )
        for row in await files_cur.fetchall():
            if row["storage_key"]:
                keys.append(row["storage_key"])

        assets_cur = await conn.execute(
            "SELECT storage_key FROM user_title_assets WHERE title_id = %s",
            (title_id,),
        )
        for row in await assets_cur.fetchall():
            if row["storage_key"]:
                keys.append(row["storage_key"])

        await conn.execute(
            "DELETE FROM user_titles WHERE id = %s AND user_id = %s",
            (title_id, user_id),
        )
        return keys


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _row_to_title(row: dict[str, Any]) -> UserTitle:
    raw_authors = row["authors"]
    authors = [str(a) for a in raw_authors] if isinstance(raw_authors, list) else []
    if "reader_last_read_position" in row:
        last_read_position = row["reader_last_read_position"]
        last_read_at = row["reader_last_read_at"]
    else:
        last_read_position = row.get("last_read_position")
        last_read_at = row.get("last_read_at")
    return UserTitle(
        id=_as_uuid(row["id"]),
        user_id=row["user_id"],
        title=row["title"],
        description=row["description"],
        authors=authors,
        cover_storage_key=row["cover_storage_key"],
        cover_media_type=row["cover_media_type"],
        last_read_position=last_read_position,
        last_read_at=last_read_at,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        chapter_count=int(row.get("chapter_count", 0) or 0),
        visibility=str(row.get("visibility") or VISIBILITY_PRIVATE),
        published_at=row.get("published_at"),
        hidden_reason=row.get("hidden_reason"),
        hidden_at=row.get("hidden_at"),
        allow_download=bool(row.get("allow_download", False)),
        owner_name=row.get("owner_name"),
        owner_avatar_path=row.get("owner_avatar_path"),
    )


def _row_to_file(row: dict[str, Any]) -> UserTitleFile:
    return UserTitleFile(
        id=_as_uuid(row["id"]),
        title_id=_as_uuid(row["title_id"]),
        storage_key=row["storage_key"],
        original_filename=row["original_filename"],
        media_type=row["media_type"],
        size_bytes=int(row["size_bytes"]),
        sha256=row["sha256"],
        import_status=row["import_status"],
        import_error=row["import_error"],
        is_active=bool(row["is_active"]),
        uploaded_at=row["uploaded_at"],
    )
