from app.image_sniff import looks_like_jpeg, looks_like_png, looks_like_webp

_JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


def test_looks_like_jpeg() -> None:
    assert looks_like_jpeg(_JPEG_BYTES) is True
    assert looks_like_jpeg(_PNG_BYTES) is False


def test_looks_like_png() -> None:
    assert looks_like_png(_PNG_BYTES) is True
    assert looks_like_png(_JPEG_BYTES) is False


def test_looks_like_webp() -> None:
    assert looks_like_webp(_WEBP_BYTES) is True
    assert looks_like_webp(_PNG_BYTES) is False


def test_none_of_them_match_unrelated_bytes() -> None:
    garbage = b"not an image at all"
    assert looks_like_jpeg(garbage) is False
    assert looks_like_png(garbage) is False
    assert looks_like_webp(garbage) is False
