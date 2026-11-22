"""Database access for private user-uploaded titles (issue 432).

Every read, update, and delete is strictly scoped by ``user_id`` so one user can never
view, read, modify, download, or delete another user's book even when knowing its UUID.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


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
    """Atomically inserts a user title, its active EPUB file metadata, chapters and assets.

    Runs inside a single Postgres transaction so any failure rolls back completely without
    leaving orphaned rows. Transitions ``import_status`` from ``pending`` to ``ready``
    before committing.
    """
    now = datetime.now(UTC).isoformat()
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO user_titles "
            "(id, user_id, title, description, authors, cover_storage_key, cover_media_type, "
            "created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
    )


async def list_user_titles(conn: AsyncConnection[Any], user_id: int) -> list[UserTitle]:
    """Lists ``user_id``'s own uploaded titles, most recently updated first."""
    cursor = await conn.execute(
        "SELECT t.*, "
        "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) AS chapter_count "
        "FROM user_titles t "
        "WHERE t.user_id = %s "
        "ORDER BY t.updated_at DESC",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_title(row) for row in rows]


async def get_user_title(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID
) -> UserTitle | None:
    cursor = await conn.execute(
        "SELECT t.*, "
        "(SELECT COUNT(*) FROM user_title_chapters c WHERE c.title_id = t.id) AS chapter_count "
        "FROM user_titles t "
        "WHERE t.id = %s AND t.user_id = %s",
        (title_id, user_id),
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


async def record_user_title_read(
    conn: AsyncConnection[Any], user_id: int, title_id: uuid.UUID, position: int
) -> None:
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "UPDATE user_titles "
        "SET last_read_position = %s, last_read_at = %s, updated_at = %s "
        "WHERE id = %s AND user_id = %s",
        (position, now, now, title_id, user_id),
    )


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
    return UserTitle(
        id=_as_uuid(row["id"]),
        user_id=row["user_id"],
        title=row["title"],
        description=row["description"],
        authors=authors,
        cover_storage_key=row["cover_storage_key"],
        cover_media_type=row["cover_media_type"],
        last_read_position=row["last_read_position"],
        last_read_at=row["last_read_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        chapter_count=int(row.get("chapter_count", 0) or 0),
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
