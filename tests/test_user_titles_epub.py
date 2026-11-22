"""Unit tests for EPUB parsing, validation, sanitization, storage, and config (issue 432)."""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import pytest

from app.config import get_settings
from app.user_titles.epub import EpubValidationError, parse_epub
from app.user_titles.storage import (
    LocalFileStorage,
    StorageError,
    StorageNotFoundError,
    validate_storage_key,
)
from tests.epub_helpers import TINY_GIF, TINY_JPEG, TINY_PNG, ChapterSpec, build_epub_bytes


def _write_epub(tmp_path: Path, data: bytes, name: str = "book.epub") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- Valid EPUB 2 & EPUB 3 parsing ----------------------------------------------------


def test_parse_valid_epub3_extracts_metadata_cover_chapters_and_assets(tmp_path: Path) -> None:
    title_id = uuid.uuid4()
    data = build_epub_bytes(
        title="Хроники Заката",
        authors=["Анна Смирнова", "Пётр Волков", "Анна Смирнова"],
        description="<p>История о <b>двух</b> мирах.</p>",
        chapters=[
            ChapterSpec(
                "text/ch1.xhtml",
                "Пролог",
                '<div><p>Привет, мир!</p><p><img src="../images/map.png" alt="Карта"></p>'
                '<p><a href="ch2.xhtml">К главе 2</a></p></div>',
            ),
            ChapterSpec(
                "text/ch2.xhtml",
                "Глава 1. Начало",
                '<p>Вторая глава с <a href="https://example.com/ref">внешней ссылкой</a>.</p>',
            ),
        ],
        cover=("images/cover.jpg", "image/jpeg", TINY_JPEG),
        cover_mode="epub3",
        toc_mode="epub3",
        extra_files={"images/map.png": ("image/png", TINY_PNG)},
    )
    parsed = parse_epub(_write_epub(tmp_path, data), title_id=title_id)

    assert parsed.title == "Хроники Заката"
    assert parsed.authors == ["Анна Смирнова", "Пётр Волков"]
    assert parsed.description == "История о двух мирах."
    assert parsed.cover is not None
    assert parsed.cover.media_type == "image/jpeg"
    assert parsed.cover.extension == ".jpg"
    assert len(parsed.chapters) == 2
    assert [c.position for c in parsed.chapters] == [1, 2]
    assert [c.title for c in parsed.chapters] == ["Пролог", "Глава 1. Начало"]
    assert len(parsed.assets) == 1
    asset_id = parsed.assets[0].asset_id
    assert f'/my-titles/{title_id}/assets/{asset_id}"' in parsed.chapters[0].content
    # Single outer <div> wrapper unwrapped so paragraphs are direct children for reader scripts.
    assert parsed.chapters[0].content.startswith("<p>Привет, мир!</p>")
    # Internal chapter link rewritten to reader route.
    assert f'href="/my-titles/{title_id}/chapters/2"' in parsed.chapters[0].content
    # External link kept with safe rel attributes.
    assert 'href="https://example.com/ref"' in parsed.chapters[1].content
    assert 'rel="nofollow noopener noreferrer ugc"' in parsed.chapters[1].content


def test_parse_valid_epub2_ncx_and_meta_cover(tmp_path: Path) -> None:
    title_id = uuid.uuid4()
    data = build_epub_bytes(
        title="Старый EPUB 2",
        authors=["Автор Один"],
        chapters=[
            ChapterSpec("ch1.xhtml", "Часть I", "<p>Первый текст.</p>"),
            ChapterSpec("ch2.xhtml", "Часть II", "<p>Второй текст.</p>"),
        ],
        cover=("cover.png", "image/png", TINY_PNG),
        cover_mode="epub2",
        toc_mode="ncx",
    )
    parsed = parse_epub(_write_epub(tmp_path, data), title_id=title_id)

    assert parsed.title == "Старый EPUB 2"
    assert parsed.cover is not None
    assert parsed.cover.media_type == "image/png"
    assert [c.title for c in parsed.chapters] == ["Часть I", "Часть II"]


def test_parse_falls_back_to_filename_and_heading_when_metadata_sparse(tmp_path: Path) -> None:
    data = build_epub_bytes(
        title="",
        authors=[],
        description=None,
        chapters=[
            ChapterSpec("ch1.xhtml", "", "<h2>Заголовок из h2</h2><p>Содержимое.</p>", in_toc=False)
        ],
        cover=("cover.gif", "image/gif", TINY_GIF),
        cover_mode="fallback",
        toc_mode="none",
    )
    parsed = parse_epub(
        _write_epub(tmp_path, data),
        title_id=uuid.uuid4(),
        original_filename="Моё произведение.epub",
    )

    assert parsed.title == "Моё произведение"
    assert parsed.authors == []
    assert parsed.description is None
    assert parsed.cover is not None
    assert parsed.cover.media_type == "image/gif"
    assert parsed.chapters[0].title == "Заголовок из h2"


# --- Validation & structural rejection ------------------------------------------------


def test_rejects_non_zip_file_with_epub_extension(tmp_path: Path) -> None:
    path = _write_epub(tmp_path, b"not a zip archive at all", "fake.epub")
    with pytest.raises(EpubValidationError, match="ZIP"):
        parse_epub(path, title_id=uuid.uuid4())


def test_rejects_missing_or_wrong_mimetype(tmp_path: Path) -> None:
    missing = _write_epub(tmp_path, build_epub_bytes(mimetype=None), "no_mime.epub")
    with pytest.raises(EpubValidationError, match="mimetype"):
        parse_epub(missing, title_id=uuid.uuid4())

    wrong = _write_epub(tmp_path, build_epub_bytes(mimetype=b"text/plain"), "bad_mime.epub")
    with pytest.raises(EpubValidationError, match="mimetype"):
        parse_epub(wrong, title_id=uuid.uuid4())


def test_rejects_missing_container_xml(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip")
    path = _write_epub(tmp_path, buf.getvalue())
    with pytest.raises(EpubValidationError, match="container.xml"):
        parse_epub(path, title_id=uuid.uuid4())


def test_rejects_path_traversal_inside_zip(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip")
        zf.writestr("../evil.txt", b"boom")
    path = _write_epub(tmp_path, buf.getvalue())
    with pytest.raises(EpubValidationError, match="пути"):
        parse_epub(path, title_id=uuid.uuid4())


def test_rejects_xml_entity_expansion_attack(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
            "<container><rootfiles><rootfile full-path='content.opf'/></rootfiles></container>",
        )
    path = _write_epub(tmp_path, buf.getvalue())
    with pytest.raises(EpubValidationError, match="XML"):
        parse_epub(path, title_id=uuid.uuid4())


def test_rejects_epub_with_no_readable_chapters(tmp_path: Path) -> None:
    data = build_epub_bytes(
        chapters=[
            ChapterSpec("empty.xhtml", "Пусто", "<script>alert(1)</script><style>p{}</style>")
        ]
    )
    with pytest.raises(EpubValidationError, match="главы"):
        parse_epub(_write_epub(tmp_path, data), title_id=uuid.uuid4())


# --- Active content / XSS / SVG / CSS / links / images sanitization -------------------


def test_sanitizes_active_content_css_svg_links_and_external_images(tmp_path: Path) -> None:
    title_id = uuid.uuid4()
    malicious_html = (
        "<style>body { display: none; }</style>"
        "<script>alert('xss')</script>"
        '<p class="spoof" style="position:fixed;inset:0" data-role="chapter" onclick="alert(1)">'
        'Безопасный текст <a href="javascript:alert(2)">плохая ссылка</a> '
        '<a href="data:text/html,<script>alert(3)</script>">data ссылка</a>'
        "</p>"
        '<iframe src="https://evil.example"></iframe>'
        '<object data="evil.swf"></object>'
        '<img src="https://tracker.evil/pixel.png" alt="tracker">'
        '<img src="data:image/svg+xml,<svg onload=alert(1)>">'
        '<img src="images/evil.svg" alt="svg">'
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(4)">'
        "<script>alert(5)</script><circle cx='10' cy='10' r='5'/>"
        "</svg>"
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<image xlink:href="images/illus.png" width="100" height="100"/>'
        "</svg>"
    )
    data = build_epub_bytes(
        chapters=[ChapterSpec("ch1.xhtml", "Тест безопасности", malicious_html)],
        extra_files={
            "images/evil.svg": ("image/svg+xml", b"<svg onload='alert(1)'></svg>"),
            "images/illus.png": ("image/png", TINY_PNG),
        },
    )
    parsed = parse_epub(_write_epub(tmp_path, data), title_id=title_id)
    content = parsed.chapters[0].content

    for forbidden in (
        "<script",
        "alert(",
        "<style",
        "position:fixed",
        "onclick",
        "onload",
        "javascript:",
        "data:",
        "<iframe",
        "<object",
        "<svg",
        "tracker.evil",
        "evil.svg",
        "data-role",
        'class="spoof"',
    ):
        assert forbidden not in content, f"Forbidden payload leaked: {forbidden}"

    assert "Безопасный текст" in content
    assert "плохая ссылка" in content  # link unwrapped, text preserved
    # Calibre-style <svg><image xlink:href="images/illus.png"/></svg> safely converted to <img>.
    assert len(parsed.assets) == 1
    assert f'/my-titles/{title_id}/assets/{parsed.assets[0].asset_id}"' in content


# --- Storage unit tests ---------------------------------------------------------------


def test_local_storage_put_read_resolve_delete_and_traversal_defense(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "store")
    storage.put_bytes("covers/abc.png", TINY_PNG)

    assert storage.get_bytes("covers/abc.png") == TINY_PNG
    assert storage.resolve_path("covers/abc.png").is_file()

    storage.delete("covers/abc.png")
    # Deleting again is idempotent.
    storage.delete("covers/abc.png")
    with pytest.raises(StorageNotFoundError):
        storage.get_bytes("covers/abc.png")

    for bad_key in ("", "/etc/passwd", "../escape.epub", "a/../b", "a\\b", "dir//file"):
        with pytest.raises(StorageError):
            validate_storage_key(bad_key)


def test_local_storage_delete_many_reports_partial_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(tmp_path / "store")
    storage.put_bytes("a/1.bin", b"1")
    storage.put_bytes("a/2.bin", b"2")

    real_delete = storage.delete

    def flaky_delete(key: str) -> None:
        if key == "a/2.bin":
            raise StorageError("disk busy")
        real_delete(key)

    monkeypatch.setattr(storage, "delete", flaky_delete)
    failed = storage.delete_many(["a/1.bin", "a/2.bin"])

    assert failed == ["a/2.bin"]
    assert not (tmp_path / "store/a/1.bin").exists()
    assert (tmp_path / "store/a/2.bin").exists()


# --- Config tests ---------------------------------------------------------------------


def test_user_epub_config_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USER_TITLES_DIR", raising=False)
    monkeypatch.delenv("USER_EPUB_MAX_SIZE_BYTES", raising=False)
    get_settings.cache_clear()
    defaults = get_settings()
    assert defaults.user_titles_dir == Path(".ranobelib_user_titles")
    assert defaults.user_epub_max_size_bytes == 50 * 1024 * 1024

    monkeypatch.setenv("USER_TITLES_DIR", "/var/lib/user-epubs")
    monkeypatch.setenv("USER_EPUB_MAX_SIZE_BYTES", "1048576")
    get_settings.cache_clear()
    custom = get_settings()
    assert custom.user_titles_dir == Path("/var/lib/user-epubs")
    assert custom.user_epub_max_size_bytes == 1048576

    monkeypatch.setenv("USER_EPUB_MAX_SIZE_BYTES", "not-a-number")
    get_settings.cache_clear()
    assert get_settings().user_epub_max_size_bytes == 50 * 1024 * 1024
    get_settings.cache_clear()
