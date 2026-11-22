"""Orchestrates EPUB upload/import, metadata edits, and consistent deletion.

Transaction & cleanup contract:
- Upload streams the file to a temporary directory while enforcing ``max_size_bytes`` and
  computing SHA-256. The temp directory is always removed in ``finally`` (even on errors).
- EPUB structure/content validation happens before anything is written to storage or DB.
- Storage objects (original EPUB, optional cover, chapter assets) are written next; if any
  storage write fails, every key already written during this import is cleaned up.
- DB records (title, active file, chapters, assets) are inserted in a single Postgres
  transaction; if the DB write fails, the transaction rolls back AND all written storage
  objects are cleaned up so no orphaned files or DB rows remain.
- Deletion removes the DB records inside a transaction first (cascading files/chapters/
  assets), then deletes all collected storage keys best-effort. If storage deletion fails
  for some keys, the DB deletion still stands (the user never sees a broken book), every
  failed key is logged at ERROR level for operator reconciliation, and the caller receives
  ``DeleteUserTitleResult(deleted=True, failed_storage_keys=[...])``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from psycopg import AsyncConnection

from app.config import get_settings
from app.db.user_titles import (
    UserTitle,
    create_user_title_bundle,
    delete_user_title_and_collect_keys,
    update_user_title_metadata,
)
from app.user_titles.epub import (
    MAX_AUTHOR_LENGTH,
    MAX_AUTHORS_COUNT,
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    EpubValidationError,
    parse_epub,
)
from app.user_titles.storage import FileStorage, StorageError, get_storage

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024
_FILENAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f/\\]+")
_WHITESPACE_RE = re.compile(r"\s+")


class UserTitleError(Exception):
    """Base user-facing error for user-title operations."""

    status_code: int = 400


class EpubTooLargeError(UserTitleError):
    status_code = 413


class InvalidEpubError(UserTitleError):
    status_code = 400


class UserTitleStorageError(UserTitleError):
    status_code = 500


@dataclass(frozen=True)
class DeleteUserTitleResult:
    deleted: bool
    failed_storage_keys: list[str]


def sanitize_original_filename(raw_name: str | None) -> str:
    """Strips directory components and control chars from an uploaded filename."""
    base = Path(raw_name or "").name
    cleaned = _FILENAME_CONTROL_RE.sub("_", base).strip().strip(".")
    if not cleaned:
        return "book.epub"
    if not cleaned.lower().endswith(".epub"):
        cleaned = f"{cleaned}.epub"
    return cleaned[:200]


def format_size_limit(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        mb = size_bytes / (1024 * 1024)
        return f"{mb:.0f} МБ" if mb.is_integer() else f"{mb:.1f} МБ"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} КБ"
    return f"{size_bytes} Б"


def parse_authors_input(raw: str | None) -> list[str]:
    """Splits a comma- or newline-separated authors string into deduplicated clean names."""
    if not raw:
        return []
    parts = re.split(r"[\n,;]+", raw)
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = _WHITESPACE_RE.sub(" ", part).strip()[:MAX_AUTHOR_LENGTH].rstrip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)
            if len(result) >= MAX_AUTHORS_COUNT:
                break
    return result


def normalize_metadata_input(
    *, title: str, authors_raw: str | list[str] | None, description: str | None
) -> tuple[str, list[str], str | None]:
    clean_title = _WHITESPACE_RE.sub(" ", title or "").strip()
    if not clean_title:
        raise UserTitleError("Название книги не может быть пустым")
    if len(clean_title) > MAX_TITLE_LENGTH:
        raise UserTitleError(f"Название слишком длинное (максимум {MAX_TITLE_LENGTH} символов)")

    if isinstance(authors_raw, list):
        authors = parse_authors_input("\n".join(str(a) for a in authors_raw))
    else:
        authors = parse_authors_input(authors_raw)

    clean_desc = (description or "").strip()
    if len(clean_desc) > MAX_DESCRIPTION_LENGTH:
        raise UserTitleError(
            f"Описание слишком длинное (максимум {MAX_DESCRIPTION_LENGTH} символов)"
        )
    return clean_title, authors, clean_desc or None


async def import_epub(
    conn: AsyncConnection[Any],
    *,
    user_id: int,
    upload: UploadFile,
    storage: FileStorage | None = None,
    max_size_bytes: int | None = None,
) -> UserTitle:
    """Streams, validates, parses, stores, and registers a user's EPUB book."""
    settings = get_settings()
    limit = max_size_bytes if max_size_bytes is not None else settings.user_epub_max_size_bytes
    store = storage if storage is not None else get_storage()

    original_filename = sanitize_original_filename(upload.filename)
    title_id = uuid.uuid4()
    file_id = uuid.uuid4()

    with tempfile.TemporaryDirectory(prefix="epub-import-") as tmp_dir:
        tmp_path = Path(tmp_dir) / "upload.epub"
        hasher = hashlib.sha256()
        total_bytes = 0

        try:
            with tmp_path.open("wb") as dst:
                while True:
                    chunk = await upload.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > limit:
                        raise EpubTooLargeError(
                            f"Файл слишком большой (максимум {format_size_limit(limit)})"
                        )
                    hasher.update(chunk)
                    dst.write(chunk)
        except EpubTooLargeError:
            raise
        except OSError as exc:
            logger.exception("Failed to read uploaded EPUB into temporary file")
            raise UserTitleStorageError(
                "Не удалось принять файл. Попробуйте загрузить его ещё раз."
            ) from exc

        if total_bytes == 0:
            raise InvalidEpubError("Файл пуст. Выберите корректный файл EPUB.")

        sha256_hex = hasher.hexdigest()

        try:
            parsed = parse_epub(tmp_path, title_id=title_id, original_filename=original_filename)
        except EpubValidationError as exc:
            raise InvalidEpubError(str(exc)) from exc

        written_keys: list[str] = []
        epub_key = f"epubs/{file_id}.epub"
        cover_key: str | None = None
        cover_media_type: str | None = None
        asset_rows: list[tuple[uuid.UUID, str, str, int, str]] = []

        try:
            store.put_file(epub_key, tmp_path)
            written_keys.append(epub_key)

            if parsed.cover is not None:
                cover_key = f"covers/{title_id}{parsed.cover.extension}"
                cover_media_type = parsed.cover.media_type
                store.put_bytes(cover_key, parsed.cover.data)
                written_keys.append(cover_key)

            for asset in parsed.assets:
                asset_key = f"assets/{title_id}/{asset.asset_id}{asset.extension}"
                store.put_bytes(asset_key, asset.data)
                written_keys.append(asset_key)
                asset_rows.append(
                    (
                        asset.asset_id,
                        asset_key,
                        asset.media_type,
                        len(asset.data),
                        asset.source_href,
                    )
                )
        except StorageError as exc:
            logger.exception("Storage error while importing EPUB for user_id=%s", user_id)
            failed = store.delete_many(written_keys)
            if failed:
                logger.error("Failed to roll back storage keys after storage error: %s", failed)
            raise UserTitleStorageError(
                "Не удалось сохранить файлы книги в хранилище. Попробуйте позже."
            ) from exc

        chapter_rows = [
            (uuid.uuid4(), ch.position, ch.title, ch.content, ch.source_href)
            for ch in parsed.chapters
        ]

        try:
            return await create_user_title_bundle(
                conn,
                title_id=title_id,
                user_id=user_id,
                title=parsed.title,
                description=parsed.description,
                authors=parsed.authors,
                cover_storage_key=cover_key,
                cover_media_type=cover_media_type,
                file_id=file_id,
                file_storage_key=epub_key,
                original_filename=original_filename,
                file_media_type="application/epub+zip",
                size_bytes=total_bytes,
                sha256=sha256_hex,
                chapters=chapter_rows,
                assets=asset_rows,
            )
        except Exception as exc:
            logger.exception("Database error while importing EPUB for user_id=%s", user_id)
            failed = store.delete_many(written_keys)
            if failed:
                logger.error("Failed to roll back storage keys after DB error: %s", failed)
            raise UserTitleStorageError(
                "Не удалось сохранить книгу в базе данных. Попробуйте позже."
            ) from exc


async def edit_user_title(
    conn: AsyncConnection[Any],
    *,
    user_id: int,
    title_id: uuid.UUID,
    title: str,
    authors_raw: str | list[str] | None,
    description: str | None,
) -> UserTitle | None:
    clean_title, authors, clean_desc = normalize_metadata_input(
        title=title, authors_raw=authors_raw, description=description
    )
    return await update_user_title_metadata(
        conn,
        user_id=user_id,
        title_id=title_id,
        title=clean_title,
        authors=authors,
        description=clean_desc,
    )


async def delete_user_title(
    conn: AsyncConnection[Any],
    *,
    user_id: int,
    title_id: uuid.UUID,
    storage: FileStorage | None = None,
) -> DeleteUserTitleResult:
    """Deletes a user's title and all its files.

    DB records are deleted transactionally first; then every collected storage key is
    deleted best-effort. Partial storage failures are logged and returned in
    ``failed_storage_keys`` while keeping the book deleted from the database.
    """
    keys = await delete_user_title_and_collect_keys(conn, user_id, title_id)
    if keys is None:
        return DeleteUserTitleResult(deleted=False, failed_storage_keys=[])

    store = storage if storage is not None else get_storage()
    failed = store.delete_many(keys)
    if failed:
        logger.error(
            "Partial storage cleanup failure after deleting user_title=%s: failed_keys=%s",
            title_id,
            failed,
        )
    return DeleteUserTitleResult(deleted=True, failed_storage_keys=failed)
