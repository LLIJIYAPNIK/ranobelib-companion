"""Private file/object storage abstraction for user-uploaded EPUBs, covers and assets.

Keys are internal opaque paths (e.g. ``epubs/<uuid>.epub``, ``covers/<uuid>.jpg``,
``assets/<title-uuid>/<asset-uuid>.png``) that never leave the server: routes identify
resources by DB UUIDs and resolve them through this storage layer only after verifying
authentication and ownership.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)

_VALID_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class StorageError(Exception):
    """Raised when reading, writing or deleting a stored object fails."""


class StorageNotFoundError(StorageError):
    """Raised when a requested storage key does not exist."""


class FileStorage(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...

    def put_file(self, key: str, source_path: Path) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def resolve_path(self, key: str) -> Path: ...

    def delete(self, key: str) -> None: ...

    def delete_many(self, keys: Iterable[str]) -> list[str]: ...


def validate_storage_key(key: str) -> str:
    """Rejects empty, absolute, backslash, or path-traversal keys before touching disk."""
    if not key or "\\" in key or key.startswith("/") or "//" in key:
        raise StorageError(f"Invalid storage key: {key!r}")
    if not _VALID_KEY_RE.match(key):
        raise StorageError(f"Invalid storage key: {key!r}")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise StorageError(f"Invalid storage key: {key!r}")
    return key


class LocalFileStorage:
    """Filesystem-backed storage rooted at ``Settings.user_titles_dir``.

    Writes go through a temp file in the destination directory followed by ``os.replace``
    so a crashed or interrupted write never leaves a half-written file at ``key``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _target(self, key: str) -> Path:
        validated = validate_storage_key(key)
        root_resolved = self._root.resolve()
        target = (root_resolved / validated).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise StorageError(f"Storage key escapes root: {key!r}") from exc
        return target

    def put_bytes(self, key: str, data: bytes) -> None:
        target = self._target(key)
        tmp_path: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_tmp = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
            tmp_path = Path(raw_tmp)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
            os.replace(tmp_path, target)
            tmp_path = None
        except StorageError:
            raise
        except OSError as exc:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise StorageError(f"Failed to write storage object {key!r}") from exc

    def put_file(self, key: str, source_path: Path) -> None:
        target = self._target(key)
        tmp_path: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_tmp = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
            tmp_path = Path(raw_tmp)
            try:
                with os.fdopen(fd, "wb") as dst, source_path.open("rb") as src:
                    shutil.copyfileobj(src, dst, length=64 * 1024)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
            os.replace(tmp_path, target)
            tmp_path = None
        except StorageError:
            raise
        except OSError as exc:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise StorageError(f"Failed to store file {key!r}") from exc

    def get_bytes(self, key: str) -> bytes:
        target = self._target(key)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"Storage object not found: {key!r}") from exc
        except OSError as exc:
            raise StorageError(f"Failed to read storage object {key!r}") from exc

    def resolve_path(self, key: str) -> Path:
        target = self._target(key)
        if not target.is_file():
            raise StorageNotFoundError(f"Storage object not found: {key!r}")
        return target

    def delete(self, key: str) -> None:
        target = self._target(key)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"Failed to delete storage object {key!r}") from exc
        # Best-effort cleanup of now-empty per-title subdirectories inside root.
        root_resolved = self._root.resolve()
        parent = target.parent
        while parent != root_resolved and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def delete_many(self, keys: Iterable[str]) -> list[str]:
        """Deletes every key in ``keys`` best-effort; returns keys that failed to delete."""
        failed: list[str] = []
        for key in keys:
            if not key:
                continue
            try:
                self.delete(key)
            except StorageError:
                logger.exception("Failed to delete storage key %r", key)
                failed.append(key)
        return failed


def get_storage() -> FileStorage:
    return LocalFileStorage(get_settings().user_titles_dir)
