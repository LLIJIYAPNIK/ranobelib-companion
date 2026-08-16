import base64
import io

import pytest
from fastapi import UploadFile

from app.comment_attachment import (
    CommentAttachmentError,
    save_comment_attachment,
)
from app.config import get_settings
from app.gif_video import is_ffmpeg_available

_JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
_MP4_BYTES = b"\x00\x00\x00\x18ftyp" + b"\x00" * 32
_WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
# The smallest possible valid GIF - see tests/test_gif_video.py for the same fixture.
_TINY_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")

_requires_ffmpeg = pytest.mark.skipif(
    not is_ffmpeg_available(), reason="ffmpeg not installed in this environment"
)


@pytest.fixture(autouse=True)
def isolated_attachment_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COMMENT_ATTACHMENT_DIR", str(tmp_path / "attachments"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _upload(contents: bytes, filename: str = "upload") -> UploadFile:
    return UploadFile(file=io.BytesIO(contents), filename=filename)


async def test_saves_a_jpeg_as_an_image() -> None:
    path, kind = await save_comment_attachment(_upload(_JPEG_BYTES, "photo.jpg"))

    assert kind == "image"
    assert path.endswith(".jpg")
    assert (get_settings().comment_attachment_dir / path).read_bytes() == _JPEG_BYTES


async def test_saves_a_png_as_an_image() -> None:
    path, kind = await save_comment_attachment(_upload(_PNG_BYTES, "photo.png"))

    assert kind == "image"
    assert path.endswith(".png")


async def test_saves_a_webp_as_an_image() -> None:
    path, kind = await save_comment_attachment(_upload(_WEBP_BYTES, "photo.webp"))

    assert kind == "image"
    assert path.endswith(".webp")


async def test_saves_an_mp4_as_a_video() -> None:
    path, kind = await save_comment_attachment(_upload(_MP4_BYTES, "clip.mp4"))

    assert kind == "video"
    assert path.endswith(".mp4")
    assert (get_settings().comment_attachment_dir / path).read_bytes() == _MP4_BYTES


async def test_saves_a_webm_as_a_video() -> None:
    path, kind = await save_comment_attachment(_upload(_WEBM_BYTES, "clip.webm"))

    assert kind == "video"
    assert path.endswith(".webm")


async def test_rejects_an_oversized_image() -> None:
    oversized = _JPEG_BYTES + b"\x00" * (9 * 1024 * 1024)
    with pytest.raises(CommentAttachmentError, match="слишком большой"):
        await save_comment_attachment(_upload(oversized, "photo.jpg"))


async def test_rejects_an_unrecognized_file() -> None:
    with pytest.raises(CommentAttachmentError, match="Поддерживаются только"):
        await save_comment_attachment(_upload(b"not a real file", "mystery.bin"))


async def test_generated_filenames_dont_collide_between_uploads() -> None:
    path1, _ = await save_comment_attachment(_upload(_JPEG_BYTES, "a.jpg"))
    path2, _ = await save_comment_attachment(_upload(_JPEG_BYTES, "b.jpg"))

    assert path1 != path2


@_requires_ffmpeg
async def test_a_gif_is_converted_to_a_looping_video_not_saved_as_is() -> None:
    path, kind = await save_comment_attachment(_upload(_TINY_GIF, "cat.gif"))

    assert kind == "gif"
    assert path.endswith(".mp4")
    stored = get_settings().comment_attachment_dir / path
    assert stored.exists()
    assert stored.stat().st_size > 0
    # Confirms it's really an mp4, not the raw GIF bytes just renamed.
    assert stored.read_bytes()[:6] not in (b"GIF87a", b"GIF89a")
