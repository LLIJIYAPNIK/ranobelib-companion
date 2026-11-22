"""End-to-end Postgres + ASGI tests for private user-uploaded EPUB titles (issue 432)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.user_titles import get_active_title_file, get_user_title
from app.user_titles.storage import LocalFileStorage, StorageError
from tests.auth_helpers import register
from tests.db_reset import reset_app_database
from tests.epub_helpers import TINY_JPEG, TINY_PNG, ChapterSpec, build_epub_bytes


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("USER_TITLES_DIR", str(tmp_path / "user_titles"))
    monkeypatch.setenv("USER_EPUB_MAX_SIZE_BYTES", str(2 * 1024 * 1024))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _make_client_for_app() -> TestClient:
    from app.main import app

    return TestClient(app)


def _sample_epub() -> bytes:
    return build_epub_bytes(
        title="Книга Алисы",
        authors=["Алиса Писатель", "Боб Соавтор"],
        description="Приватная книга для чтения в ридере.",
        chapters=[
            ChapterSpec(
                "ch1.xhtml",
                "Первая глава",
                '<p>Текст первой главы.</p><p><img src="images/pic.png" alt="Картинка"></p>'
                '<script>alert("xss")</script>',
            ),
            ChapterSpec("ch2.xhtml", "Вторая глава", "<p>Финальный текст книги.</p>"),
        ],
        cover=("images/cover.jpg", "image/jpeg", TINY_JPEG),
        extra_files={"images/pic.png": ("image/png", TINY_PNG)},
    )


def _storage_files() -> list[Path]:
    root = get_settings().user_titles_dir
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


# --- Authentication & Isolation -------------------------------------------------------


def test_anonymous_visitor_is_redirected_from_my_titles(client: TestClient) -> None:
    fake_id = uuid.uuid4()
    for method, path in (
        ("GET", "/my-titles"),
        ("GET", f"/my-titles/{fake_id}"),
        ("GET", f"/my-titles/{fake_id}/chapters/1"),
        ("GET", f"/my-titles/{fake_id}/cover"),
        ("GET", f"/my-titles/{fake_id}/assets/{uuid.uuid4()}"),
        ("GET", f"/my-titles/{fake_id}/download"),
        ("POST", f"/my-titles/{fake_id}/edit"),
        ("POST", f"/my-titles/{fake_id}/delete"),
        ("DELETE", f"/my-titles/{fake_id}"),
    ):
        resp = client.request(method, path, follow_redirects=False)
        assert resp.status_code == 303, (method, path)
        assert resp.headers["location"] == "/login"


async def test_full_upload_read_edit_download_delete_lifecycle(client: TestClient) -> None:
    register(client, "alice@example.com")
    epub_data = _sample_epub()

    upload_resp = client.post(
        "/my-titles/upload",
        files={"file": ("alice_book.epub", epub_data, "application/epub+zip")},
        headers={"accept": "application/json"},
    )
    assert upload_resp.status_code == 201
    payload = upload_resp.json()
    title_id = uuid.UUID(payload["id"])
    assert payload["title"] == "Книга Алисы"
    assert payload["authors"] == ["Алиса Писатель", "Боб Соавтор"]
    assert payload["chapter_count"] == 2
    assert payload["has_cover"] is True
    assert "storage_key" not in payload

    # Verify DB & storage consistency (no storage key leaked anywhere in responses).
    async with connection() as conn:
        db_title = await get_user_title(conn, 1, title_id)
        db_file = await get_active_title_file(conn, 1, title_id)
    assert db_title is not None
    assert db_file is not None
    assert db_file.import_status == "ready"
    assert db_file.size_bytes == len(epub_data)
    assert len(_storage_files()) == 3  # epub + cover + chapter asset

    # Visible on /my-titles and /library, but never leaks storage_key.
    my_page = client.get("/my-titles")
    assert my_page.status_code == 200
    assert "Книга Алисы" in my_page.text
    assert db_file.storage_key not in my_page.text
    assert str(get_settings().user_titles_dir) not in my_page.text

    lib_page = client.get("/library")
    assert lib_page.status_code == 200
    assert 'data-role="library-user-titles"' in lib_page.text
    assert "Книга Алисы" in lib_page.text

    # Title page & TOC.
    detail = client.get(f"/my-titles/{title_id}")
    assert detail.status_code == 200
    assert "Первая глава" in detail.text
    assert "Вторая глава" in detail.text
    assert db_file.storage_key not in detail.text

    # Reader renders sanitized chapter content with safe asset URL and no XSS.
    ch1 = client.get(f"/my-titles/{title_id}/chapters/1")
    assert ch1.status_code == 200
    assert "Текст первой главы." in ch1.text
    assert "<script>alert" not in ch1.text
    assert f'/my-titles/{title_id}/chapters/2' in ch1.text
    assert f"/my-titles/{title_id}/assets/" in ch1.text
    # Reader does not attach ranobelib social/progress scripts for private books.
    assert "static/js/paragraph-menu.js" not in ch1.text
    assert "static/js/reading-progress-tick.js" not in ch1.text

    # Cover & chapter asset & EPUB download work for owner.
    cover_resp = client.get(f"/my-titles/{title_id}/cover")
    assert cover_resp.status_code == 200
    assert cover_resp.content == TINY_JPEG
    assert cover_resp.headers["x-content-type-options"] == "nosniff"
    assert "private" in cover_resp.headers["cache-control"]

    dl_resp = client.get(f"/my-titles/{title_id}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.content == epub_data
    assert "attachment" in dl_resp.headers["content-disposition"]
    assert db_file.storage_key not in dl_resp.headers["content-disposition"]

    # Edit metadata (title, authors, description).
    edit_resp = client.post(
        f"/my-titles/{title_id}/edit",
        data={
            "title": "Новое название",
            "authors": "Исправленный Автор",
            "description": "Новое описание",
        },
        follow_redirects=False,
    )
    assert edit_resp.status_code == 303
    updated_detail = client.get(f"/my-titles/{title_id}")
    assert "Новое название" in updated_detail.text
    assert "Исправленный Автор" in updated_detail.text
    assert "Новое описание" in updated_detail.text

    # Delete removes DB records and all storage files cleanly.
    del_resp = client.post(f"/my-titles/{title_id}/delete", follow_redirects=False)
    assert del_resp.status_code == 303
    assert del_resp.headers["location"] == "/my-titles?deleted=1"
    assert client.get(f"/my-titles/{title_id}").status_code == 404
    assert _storage_files() == []


def test_other_user_cannot_access_read_edit_download_or_delete_foreign_title(
    client: TestClient,
) -> None:
    register(client, "alice@example.com")
    upload_resp = client.post(
        "/my-titles/upload",
        files={"file": ("secret.epub", _sample_epub(), "application/epub+zip")},
        headers={"accept": "application/json"},
    )
    assert upload_resp.status_code == 201
    title_id = upload_resp.json()["id"]

    # Extract asset URL from Alice's chapter HTML so Bob tries to hit it directly too.
    ch1_html = client.get(f"/my-titles/{title_id}/chapters/1").text
    marker = f"/my-titles/{title_id}/assets/"
    start = ch1_html.index(marker)
    asset_url = ch1_html[start : ch1_html.index('"', start)]

    # Second independent session for Bob.
    with _make_client_for_app() as bob:
        register(bob, "bob@example.com")

        assert bob.get("/my-titles").status_code == 200
        assert "Книга Алисы" not in bob.get("/my-titles").text
        assert bob.get(f"/my-titles/{title_id}").status_code == 404
        assert bob.get(f"/my-titles/{title_id}/chapters/1").status_code == 404
        assert bob.get(f"/my-titles/{title_id}/cover").status_code == 404
        assert bob.get(asset_url).status_code == 404
        assert bob.get(f"/my-titles/{title_id}/download").status_code == 404
        assert (
            bob.post(
                f"/my-titles/{title_id}/edit",
                data={"title": "Взлом", "authors": "", "description": ""},
            ).status_code
            == 404
        )
        assert bob.patch(f"/my-titles/{title_id}", json={"title": "Взлом"}).status_code == 404
        assert bob.post(f"/my-titles/{title_id}/delete").status_code == 404
        assert bob.delete(f"/my-titles/{title_id}").status_code == 404

    # Alice's book is completely untouched.
    assert client.get(f"/my-titles/{title_id}").status_code == 200
    assert "Книга Алисы" in client.get(f"/my-titles/{title_id}").text


def test_invalid_oversized_and_storage_failure_leave_no_orphans(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    register(client, "alice@example.com")

    # 1. Invalid EPUB
    bad_resp = client.post(
        "/my-titles/upload",
        files={"file": ("fake.epub", b"not an epub", "application/epub+zip")},
    )
    assert bad_resp.status_code == 400
    assert 'data-role="epub-upload-error"' in bad_resp.text
    assert _storage_files() == []

    # 2. Oversized EPUB
    monkeypatch.setenv("USER_EPUB_MAX_SIZE_BYTES", "128")
    get_settings.cache_clear()
    big_resp = client.post(
        "/my-titles/upload",
        files={"file": ("big.epub", _sample_epub(), "application/epub+zip")},
        headers={"accept": "application/json"},
    )
    assert big_resp.status_code == 413
    assert "слишком большой" in big_resp.json()["detail"].lower()
    assert _storage_files() == []

    # 3. Storage failure
    monkeypatch.setenv("USER_EPUB_MAX_SIZE_BYTES", str(2 * 1024 * 1024))
    get_settings.cache_clear()

    def boom_put_bytes(self: LocalFileStorage, key: str, data: bytes) -> None:
        raise StorageError("disk failure")

    with patch.object(LocalFileStorage, "put_bytes", boom_put_bytes):
        err_resp = client.post(
            "/my-titles/upload",
            files={"file": ("book.epub", _sample_epub(), "application/epub+zip")},
            headers={"accept": "application/json"},
        )
    assert err_resp.status_code == 500
    assert _storage_files() == []
    assert client.get("/my-titles", headers={"accept": "application/json"}).json() == {"items": []}


async def test_schema_enforces_single_active_epub_and_unique_chapter_position(
    client: TestClient,
) -> None:
    register(client, "alice@example.com")
    resp = client.post(
        "/my-titles/upload",
        files={"file": ("book.epub", _sample_epub(), "application/epub+zip")},
        headers={"accept": "application/json"},
    )
    assert resp.status_code == 201
    title_id = uuid.UUID(resp.json()["id"])

    async with connection() as conn:
        # Second active file for the same title violates partial unique index.
        with pytest.raises(psycopg.errors.UniqueViolation):
            await conn.execute(
                "INSERT INTO user_title_files "
                "(id, title_id, storage_key, original_filename, media_type, size_bytes, "
                "sha256, import_status, is_active, uploaded_at) "
                "VALUES (%s, %s, %s, 'dup.epub', 'application/epub+zip', 10, 'abc', 'ready', "
                "TRUE, '2026-11-22T00:00:00+00:00')",
                (uuid.uuid4(), title_id, f"epubs/{uuid.uuid4()}.epub"),
            )

        # Duplicate chapter position violates UNIQUE(title_id, position).
        with pytest.raises(psycopg.errors.UniqueViolation):
            await conn.execute(
                "INSERT INTO user_title_chapters "
                "(id, title_id, position, title, content, source_href) "
                "VALUES (%s, %s, 1, 'Dup', '<p>x</p>', 'dup.xhtml')",
                (uuid.uuid4(), title_id),
            )
