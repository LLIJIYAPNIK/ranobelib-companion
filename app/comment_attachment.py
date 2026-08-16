"""Unified comment attachment upload (PR 151, generalizing PR 150's GIF-only path) - one
button in the composer for image/video/GIF alike (per explicit direction: not a separate
button per file type). ``save_comment_attachment()`` sniffs the actual bytes to classify
the upload and decides what to do with it:

- A GIF still goes through ``app/gif_video.py``'s ``convert_gif_to_video()`` (a silent
  looping mp4, ``attachment_kind`` "gif") - PR 150's decision that a GIF should play like a
  video, not sit still like a picture, holds regardless of which button picked it.
- Any other recognized image (JPG/PNG/WEBP) or video (MP4/WEBM) is stored as-is
  (``attachment_kind`` "image"/"video"), the same validate-then-write pattern
  ``app/auth/avatar.py``'s ``save_avatar()`` already uses, just with its own size limits (a
  video runs heavier than a profile photo) and a generated filename rather than one keyed
  to a user id, since a comment doesn't have an id until after ``create_comment()`` runs.

One ``attachment_path``/``attachment_kind`` pair per comment (migration 0011/0012's
columns, already generic enough for this - PR 150 didn't need a new migration). Real
multi-attachment support would need its own table and is a bigger feature, not something
to back into here without an explicit ask.
"""

from __future__ import annotations

import secrets

from fastapi import UploadFile

from app.config import get_settings
from app.gif_video import GifConversionError, convert_gif_to_video
from app.image_sniff import looks_like_jpeg, looks_like_png, looks_like_webp

# A photo runs smaller than a video - same "generous but bounded" reasoning as
# app/auth/avatar.py's _MAX_AVATAR_SIZE_BYTES and app/gif_video.py's MAX_GIF_SIZE_BYTES,
# just its own limit per kind rather than inheriting either of theirs as-is.
MAX_IMAGE_SIZE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_SIZE_BYTES = 50 * 1024 * 1024

_GIF_MAGIC = (b"GIF87a", b"GIF89a")


class CommentAttachmentError(Exception):
    """Raised for anything that isn't a recognized image/video/GIF, is over its own
    kind's size limit, or (GIF only) that ffmpeg rejects/times out on. The message is
    safe to show the visitor as-is (same convention as AvatarUploadError)."""


def _looks_like_mp4(contents: bytes) -> bool:
    return contents[4:8] == b"ftyp"


def _looks_like_webm(contents: bytes) -> bool:
    return contents[:4] == b"\x1a\x45\xdf\xa3"


async def save_comment_attachment(upload: UploadFile) -> tuple[str, str]:
    """Returns (attachment_path, attachment_kind) once the file's been classified,
    validated, and (for a GIF) converted or (for anything else recognized) written to
    disk under Settings.comment_attachment_dir. Raises CommentAttachmentError for
    anything unrecognized or rejected."""
    contents = await upload.read(MAX_VIDEO_SIZE_BYTES + 1)

    if contents[:6] in _GIF_MAGIC:
        dest_filename = f"{secrets.token_hex(16)}.mp4"
        try:
            await convert_gif_to_video(
                contents, get_settings().comment_attachment_dir / dest_filename
            )
        except GifConversionError as exc:
            raise CommentAttachmentError(str(exc)) from exc
        return dest_filename, "gif"

    if looks_like_jpeg(contents):
        return _save_raw(contents, ".jpg", "image")
    if looks_like_png(contents):
        return _save_raw(contents, ".png", "image")
    if looks_like_webp(contents):
        return _save_raw(contents, ".webp", "image")
    if _looks_like_mp4(contents):
        return _save_raw(contents, ".mp4", "video")
    if _looks_like_webm(contents):
        return _save_raw(contents, ".webm", "video")

    raise CommentAttachmentError(
        "Поддерживаются только изображения (JPG, PNG, WEBP, GIF) и видео (MP4, WEBM)"
    )


def _save_raw(contents: bytes, extension: str, kind: str) -> tuple[str, str]:
    limit = MAX_IMAGE_SIZE_BYTES if kind == "image" else MAX_VIDEO_SIZE_BYTES
    if len(contents) > limit:
        raise CommentAttachmentError(
            f"Файл слишком большой (максимум {limit // (1024 * 1024)} МБ)"
        )
    attachment_dir = get_settings().comment_attachment_dir
    attachment_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_hex(16)}{extension}"
    (attachment_dir / filename).write_bytes(contents)
    return filename, kind
