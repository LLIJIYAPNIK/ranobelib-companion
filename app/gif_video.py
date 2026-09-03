"""GIF comment attachments (PR 150) - a visitor attaches a .gif to a comment; the server
transcodes it into a small silent looping video (h264 mp4, no audio track) instead of
storing the GIF itself, so it renders client-side as
``<video autoplay loop muted playsinline>`` rather than ``<img>`` - the same trick
Discord/Twitter use, since an equivalent video is dramatically smaller than the GIF it was
made from.

Needs the ``ffmpeg`` binary on ``PATH``. Its absence is a "feature not available right
now" UI state, the same precedent already set for WeasyPrint/PDF export
(``app/services/exports.py``, ``EXPORTERS``) - not a crash. ``is_ffmpeg_available()`` gates
whether the composer even shows the GIF button (``read_chapter()`` in
``app/api/chapters.py``); ``convert_gif_to_video()`` still checks again and raises a clear
``GifConversionError`` if it's ever called anyway (a stale page, a direct API call).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from starlette.concurrency import run_in_threadpool

# Generous for a GIF, small enough that reading it fully into memory (rather than
# streaming to disk in chunks) isn't a concern - same reasoning as
# app/auth/avatar.py's _MAX_AVATAR_SIZE_BYTES, just a larger cap since GIFs run bigger.
MAX_GIF_SIZE_BYTES = 15 * 1024 * 1024

# Bounds how long a single conversion can run - a pathological GIF (huge frame count/
# dimensions) shouldn't be able to tie up a request indefinitely.
_CONVERSION_TIMEOUT_SECONDS = 30

_GIF_MAGIC = (b"GIF87a", b"GIF89a")

# Caps the longer side at 720px (plenty for a comment attachment) while preserving aspect
# ratio, then forces both dimensions even - libx264's yuv420p output requires it, and the
# first scale pass alone only guarantees one dimension is even when force_original_aspect_
# ratio=decrease computes the other from a non-square source. max(...,2) guards a source
# with a 1px side (trunc(1/2)*2 = 0, an invalid dimension) - degenerate for a real GIF, but
# not for a deliberately pathological upload.
_SCALE_FILTER = (
    "scale='min(720\\,iw)':'min(720\\,ih)':force_original_aspect_ratio=decrease,"
    "scale=max(trunc(iw/2)*2\\,2):max(trunc(ih/2)*2\\,2)"
)


class GifConversionError(Exception):
    """Raised for anything that isn't a real GIF, is over MAX_GIF_SIZE_BYTES, or that
    ffmpeg itself rejects/times out on. The message is safe to show the visitor as-is
    (same convention as AvatarUploadError)."""


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _looks_like_gif(contents: bytes) -> bool:
    return contents[:6] in _GIF_MAGIC


async def convert_gif_to_video(contents: bytes, dest_path: Path) -> None:
    """Writes an h264 mp4 to `dest_path`, converted from `contents` (the raw bytes of an
    uploaded .gif). Raises GifConversionError instead of leaving a partial/corrupt file at
    `dest_path` on any failure."""
    if len(contents) > MAX_GIF_SIZE_BYTES:
        raise GifConversionError("Файл слишком большой (максимум 15 МБ)")
    if not _looks_like_gif(contents):
        raise GifConversionError("Файл повреждён или не является GIF")
    if not is_ffmpeg_available():
        raise GifConversionError("Загрузка GIF сейчас недоступна")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / "input.gif"
        src_path.write_bytes(contents)
        # A blocking subprocess.run() in a worker thread, not asyncio.create_subprocess_exec
        # - the latter needs ProactorEventLoop on Windows, which conflicts with psycopg's
        # async mode (needs SelectorEventLoop, see app/main.py's event loop policy).
        await run_in_threadpool(_run_ffmpeg, src_path, dest_path)


def _run_ffmpeg(src_path: Path, dest_path: Path) -> None:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src_path),
                "-movflags",
                "faststart",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                _SCALE_FILTER,
                "-an",
                str(dest_path),
            ],
            capture_output=True,
            timeout=_CONVERSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        dest_path.unlink(missing_ok=True)
        raise GifConversionError("Обработка GIF заняла слишком много времени") from None
    if result.returncode != 0:
        dest_path.unlink(missing_ok=True)
        raise GifConversionError("Не удалось обработать GIF") from None
