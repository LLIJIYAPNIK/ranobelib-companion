import base64

import pytest

from app.gif_video import GifConversionError, convert_gif_to_video, is_ffmpeg_available

# The smallest possible valid GIF (1x1, transparent) - a well-known 34-byte fixture, not
# generated via ffmpeg itself so this test suite doesn't depend on the thing it's testing
# just to build its own input.
_TINY_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")

requires_ffmpeg = pytest.mark.skipif(
    not is_ffmpeg_available(), reason="ffmpeg not installed in this environment"
)


async def test_rejects_a_file_that_isnt_a_gif(tmp_path) -> None:
    with pytest.raises(GifConversionError, match="не является GIF"):
        await convert_gif_to_video(b"not a gif at all", tmp_path / "out.mp4")


async def test_rejects_an_oversized_file(tmp_path) -> None:
    oversized = b"GIF89a" + b"\x00" * (16 * 1024 * 1024)
    with pytest.raises(GifConversionError, match="слишком большой"):
        await convert_gif_to_video(oversized, tmp_path / "out.mp4")


@requires_ffmpeg
async def test_converts_a_real_gif_to_an_mp4(tmp_path) -> None:
    dest = tmp_path / "out.mp4"
    await convert_gif_to_video(_TINY_GIF, dest)

    assert dest.exists()
    assert dest.stat().st_size > 0


@requires_ffmpeg
async def test_converts_a_1x1_gif_without_producing_a_zero_dimension(tmp_path) -> None:
    """Regression test: the scale filter's second pass (forcing even dimensions) used to
    floor-divide a 1px side down to 0, which ffmpeg rejects outright - max(...,2) in
    app/gif_video.py's _SCALE_FILTER guards against it."""
    dest = tmp_path / "out.mp4"
    await convert_gif_to_video(_TINY_GIF, dest)

    assert dest.stat().st_size > 0
