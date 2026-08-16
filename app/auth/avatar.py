"""Sidebar avatar: an uploaded image if the user has one (PR 96), otherwise initials
derived from their nickname, or their email if they haven't set one (PR 90 added the
nickname field - before that, email was the only identity string available)."""

from __future__ import annotations

import re

from fastapi import UploadFile

from app.config import get_settings
from app.db.users import User

_SEGMENT_SPLIT = re.compile(r"[\s._\-+]+")


def initials_for(nickname: str | None, email: str) -> str:
    """Up to two uppercase letters, e.g. "Alice Wong" -> "AW", "bob@x.com" -> "BO". The
    primitive `avatar_initials()`/`avatar_url()` below build on - split out so PR 147's
    comment authors (rendered client-side from JSON, not a `User` row) can get the same
    picture-or-initials pairing without a full `User` object to pass in."""
    source = nickname or email.split("@", 1)[0]
    segments = [segment for segment in _SEGMENT_SPLIT.split(source) if segment]
    if not segments:
        return "?"
    if len(segments) == 1:
        return segments[0][:2].upper()
    return (segments[0][0] + segments[1][0]).upper()


def url_for(avatar_path: str | None) -> str | None:
    """URL of the uploaded avatar image (served from Settings.avatar_dir, mounted at
    /avatars in app/main.py), or None if there isn't one - callers fall back to
    `initials_for()`/`avatar_initials()` in that case."""
    if not avatar_path:
        return None
    return f"/avatars/{avatar_path}"


def avatar_initials(user: User) -> str:
    return initials_for(user.nickname, user.email)


def avatar_url(user: User) -> str | None:
    return url_for(user.avatar_path)


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
    """Writes `upload`'s bytes to disk (under `Settings.avatar_dir` - `AVATAR_DIR`,
    needing the same persistent-volume treatment in a container deployment as
    `cache_dir`/`db_path`) and returns the filename to store as `User.avatar_path` (e.g.
    "12.png"). Raises `AvatarUploadError` for anything that isn't one of the allowed
    image types, over the size limit, or whose content doesn't actually match the
    claimed type."""
    extension = _EXTENSION_BY_CONTENT_TYPE.get(upload.content_type or "")
    if extension is None:
        raise AvatarUploadError("Поддерживаются только изображения JPG, PNG и WEBP")

    contents = await upload.read(_MAX_AVATAR_SIZE_BYTES + 1)
    if len(contents) > _MAX_AVATAR_SIZE_BYTES:
        raise AvatarUploadError("Файл слишком большой (максимум 5 МБ)")
    if not _looks_like(contents, extension):
        raise AvatarUploadError("Файл повреждён или не является изображением этого типа")

    avatar_dir = get_settings().avatar_dir
    avatar_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{user_id}{extension}"
    # A previous avatar with a *different* extension (e.g. re-uploading a .jpg over an
    # existing .png) would otherwise linger on disk forever under its own filename,
    # unreferenced by anything once `avatar_path` is overwritten below.
    for stale in avatar_dir.glob(f"{user_id}.*"):
        if stale.name != filename:
            stale.unlink()
    (avatar_dir / filename).write_bytes(contents)
    return filename
