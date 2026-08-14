"""Sidebar avatar: an uploaded image if the user has one (PR 96), otherwise initials
derived from their nickname, or their email if they haven't set one (PR 90 added the
nickname field - before that, email was the only identity string available)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import UploadFile

from app.db.users import User

_SEGMENT_SPLIT = re.compile(r"[\s._\-+]+")


def avatar_initials(user: User) -> str:
    """Up to two uppercase letters, e.g. "Alice Wong" -> "AW", "bob@x.com" -> "BO"."""
    source = user.nickname or user.email.split("@", 1)[0]
    segments = [segment for segment in _SEGMENT_SPLIT.split(source) if segment]
    if not segments:
        return "?"
    if len(segments) == 1:
        return segments[0][:2].upper()
    return (segments[0][0] + segments[1][0]).upper()


# PR 96: uploaded avatar images. Not in app/config.py's Settings yet (fixed local
# directory for now) - AVATAR_DIR becomes env-configurable once the persistent-volume
# story for it is worked out the same way it already is for cache_dir/db_path.
_AVATARS_DIR = Path(".ranobelib_avatars")

_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# Generous for a profile photo, small enough that reading it fully into memory (rather
# than streaming to disk in chunks) isn't a concern.
_MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024


class AvatarUploadError(Exception):
    """Raised for a rejected upload - the message is safe to show the visitor as-is."""


def _looks_like(contents: bytes, extension: str) -> bool:
    """Sniffs the actual bytes rather than trusting the browser-supplied content-type
    header alone - a renamed/relabeled file (e.g. an .exe served as "image/png") would
    otherwise sail through the content-type check above."""
    if extension == ".jpg":
        return contents.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return contents.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".webp":
        return contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
    return False


async def save_avatar(upload: UploadFile, user_id: int) -> str:
    """Writes `upload`'s bytes to disk and returns the filename to store as
    `User.avatar_path` (e.g. "12.png"). Raises `AvatarUploadError` for anything that
    isn't one of the allowed image types, over the size limit, or whose content doesn't
    actually match the claimed type."""
    extension = _EXTENSION_BY_CONTENT_TYPE.get(upload.content_type or "")
    if extension is None:
        raise AvatarUploadError("Поддерживаются только изображения JPG, PNG и WEBP")

    contents = await upload.read(_MAX_AVATAR_SIZE_BYTES + 1)
    if len(contents) > _MAX_AVATAR_SIZE_BYTES:
        raise AvatarUploadError("Файл слишком большой (максимум 5 МБ)")
    if not _looks_like(contents, extension):
        raise AvatarUploadError("Файл повреждён или не является изображением этого типа")

    _AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{user_id}{extension}"
    (_AVATARS_DIR / filename).write_bytes(contents)
    return filename
