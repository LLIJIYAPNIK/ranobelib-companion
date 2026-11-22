"""Unit tests for import/delete transactional cleanup & route access contracts (no DB needed)."""

from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request, UploadFile

from app.db.user_titles import UserTitle
from app.user_titles.service import (
    EpubTooLargeError,
    InvalidEpubError,
    UserTitleStorageError,
    delete_user_title,
    import_epub,
)
from app.user_titles.storage import LocalFileStorage, StorageError
from tests.epub_helpers import TINY_JPEG, TINY_PNG, ChapterSpec, build_epub_bytes


def _upload(data: bytes, filename: str = "novel.epub") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _all_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


async def test_import_epub_writes_files_and_computes_sha256_and_size(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    epub_bytes = build_epub_bytes(
        title="Моя книга",
        authors=["Автор А"],
        chapters=[
            ChapterSpec("ch1.xhtml", "Глава 1", '<p>Привет!</p><p><img src="img.png"/></p>'),
        ],
        cover=("cover.jpg", "image/jpeg", TINY_JPEG),
        extra_files={"img.png": ("image/png", TINY_PNG)},
    )
    captured: dict[str, Any] = {}

    async def fake_create(conn: Any, **kwargs: Any) -> UserTitle:
        captured.update(kwargs)
        return UserTitle(
            id=kwargs["title_id"],
            user_id=kwargs["user_id"],
            title=kwargs["title"],
            description=kwargs["description"],
            authors=kwargs["authors"],
            cover_storage_key=kwargs["cover_storage_key"],
            cover_media_type=kwargs["cover_media_type"],
            last_read_position=None,
            last_read_at=None,
            created_at="2026-11-22T00:00:00+00:00",
            updated_at="2026-11-22T00:00:00+00:00",
            chapter_count=len(kwargs["chapters"]),
        )

    with patch("app.user_titles.service.create_user_title_bundle", side_effect=fake_create):
        result = await import_epub(
            conn=AsyncMock(),
            user_id=42,
            upload=_upload(epub_bytes, filename="../../secret/my_novel.epub"),
            storage=storage,
            max_size_bytes=1024 * 1024,
        )

    assert result.title == "Моя книга"
    assert captured["original_filename"] == "my_novel.epub"
    assert captured["size_bytes"] == len(epub_bytes)
    assert captured["sha256"] == hashlib.sha256(epub_bytes).hexdigest()
    # Stored: 1 EPUB + 1 cover + 1 chapter image asset = 3 files.
    assert len(_all_files(tmp_path / "storage")) == 3
    assert storage.get_bytes(captured["file_storage_key"]) == epub_bytes


async def test_import_rejects_oversized_file_without_leaving_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    epub_bytes = build_epub_bytes()

    with pytest.raises(EpubTooLargeError) as exc_info:
        await import_epub(
            conn=AsyncMock(),
            user_id=1,
            upload=_upload(epub_bytes),
            storage=storage,
            max_size_bytes=100,
        )

    assert exc_info.value.status_code == 413
    assert _all_files(tmp_path / "storage") == []


async def test_import_rejects_invalid_epub_without_leaving_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")

    with pytest.raises(InvalidEpubError) as exc_info:
        await import_epub(
            conn=AsyncMock(),
            user_id=1,
            upload=_upload(b"plain text disguised as epub"),
            storage=storage,
            max_size_bytes=1024 * 1024,
        )

    assert exc_info.value.status_code == 400
    assert _all_files(tmp_path / "storage") == []


async def test_import_rolls_back_storage_on_mid_upload_storage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    epub_bytes = build_epub_bytes(cover=("cover.jpg", "image/jpeg", TINY_JPEG))

    # EPUB put_file succeeds, cover put_bytes fails -> EPUB must be rolled back.
    def fail_put_bytes(key: str, data: bytes) -> None:
        raise StorageError("disk full")

    monkeypatch.setattr(storage, "put_bytes", fail_put_bytes)

    with pytest.raises(UserTitleStorageError) as exc_info:
        await import_epub(
            conn=AsyncMock(),
            user_id=1,
            upload=_upload(epub_bytes),
            storage=storage,
            max_size_bytes=1024 * 1024,
        )

    assert exc_info.value.status_code == 500
    assert _all_files(tmp_path / "storage") == []


async def test_import_rolls_back_storage_on_db_error(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    epub_bytes = build_epub_bytes(cover=("cover.jpg", "image/jpeg", TINY_JPEG))

    with (
        patch(
            "app.user_titles.service.create_user_title_bundle",
            side_effect=RuntimeError("postgres connection lost"),
        ),
        pytest.raises(UserTitleStorageError),
    ):
        await import_epub(
            conn=AsyncMock(),
            user_id=1,
            upload=_upload(epub_bytes),
            storage=storage,
            max_size_bytes=1024 * 1024,
        )

    assert _all_files(tmp_path / "storage") == []


async def test_delete_user_title_removes_all_files_and_handles_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    storage.put_bytes("epubs/f1.epub", b"epub")
    storage.put_bytes("covers/t1.jpg", TINY_JPEG)
    storage.put_bytes("assets/t1/a1.png", TINY_PNG)

    real_delete = storage.delete

    def flaky_delete(key: str) -> None:
        if key == "covers/t1.jpg":
            raise StorageError("permission denied")
        real_delete(key)

    monkeypatch.setattr(storage, "delete", flaky_delete)

    with patch(
        "app.user_titles.service.delete_user_title_and_collect_keys",
        return_value=["covers/t1.jpg", "epubs/f1.epub", "assets/t1/a1.png"],
    ):
        result = await delete_user_title(
            conn=AsyncMock(), user_id=1, title_id=uuid.uuid4(), storage=storage
        )

    assert result.deleted is True
    assert result.failed_storage_keys == ["covers/t1.jpg"]
    assert not (tmp_path / "storage/epubs/f1.epub").exists()
    assert not (tmp_path / "storage/assets/t1/a1.png").exists()
    assert (tmp_path / "storage/covers/t1.jpg").exists()


async def test_delete_unowned_title_does_not_touch_storage(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    storage.put_bytes("epubs/keep.epub", b"keep")

    with patch(
        "app.user_titles.service.delete_user_title_and_collect_keys",
        return_value=None,
    ):
        result = await delete_user_title(
            conn=AsyncMock(), user_id=2, title_id=uuid.uuid4(), storage=storage
        )

    assert result.deleted is False
    assert result.failed_storage_keys == []
    assert (tmp_path / "storage/epubs/keep.epub").exists()


# --- In-memory ASGI route tests (full HTTP + Jinja2 + security headers + ownership) ---


class _MemoryUserTitlesStore:
    """Minimal in-memory backing store for testing app/api/user_titles.py without Postgres."""

    def __init__(self) -> None:
        self.titles: dict[uuid.UUID, UserTitle] = {}
        self.files: dict[uuid.UUID, Any] = {}
        self.chapters: dict[uuid.UUID, list[Any]] = {}
        self.assets: dict[uuid.UUID, Any] = {}


async def test_http_routes_full_flow_and_owner_isolation_without_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from app.auth.dependencies import get_current_user, require_current_user
    from app.config import get_settings
    from app.db.connection import get_connection
    from app.db.user_titles import (
        UserTitleAsset,
        UserTitleChapter,
        UserTitleChapterSummary,
        UserTitleFile,
    )
    from app.db.users import User
    from app.main import app

    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("USER_TITLES_DIR", str(tmp_path / "ut_store"))
    monkeypatch.setenv("USER_EPUB_MAX_SIZE_BYTES", str(2 * 1024 * 1024))
    get_settings.cache_clear()

    alice = User(
        id=1,
        email="alice@example.com",
        password_hash="x",
        created_at="2026-11-22T00:00:00+00:00",
        nickname="Alice",
    )
    bob = User(
        id=2,
        email="bob@example.com",
        password_hash="x",
        created_at="2026-11-22T00:00:00+00:00",
        nickname="Bob",
    )
    active_user: list[User | None] = [alice]
    mem = _MemoryUserTitlesStore()

    async def fake_create(conn: Any, **kw: Any) -> UserTitle:
        ut = UserTitle(
            id=kw["title_id"],
            user_id=kw["user_id"],
            title=kw["title"],
            description=kw["description"],
            authors=list(kw["authors"]),
            cover_storage_key=kw["cover_storage_key"],
            cover_media_type=kw["cover_media_type"],
            last_read_position=None,
            last_read_at=None,
            created_at="2026-11-22T00:00:00+00:00",
            updated_at="2026-11-22T00:00:00+00:00",
            chapter_count=len(kw["chapters"]),
        )
        mem.titles[ut.id] = ut
        mem.files[ut.id] = UserTitleFile(
            id=kw["file_id"],
            title_id=ut.id,
            storage_key=kw["file_storage_key"],
            original_filename=kw["original_filename"],
            media_type=kw["file_media_type"],
            size_bytes=kw["size_bytes"],
            sha256=kw["sha256"],
            import_status="ready",
            import_error=None,
            is_active=True,
            uploaded_at="2026-11-22T00:00:00+00:00",
        )
        mem.chapters[ut.id] = [
            UserTitleChapter(
                id=cid,
                title_id=ut.id,
                position=pos,
                title=ctitle,
                content=content,
                source_href=href,
            )
            for cid, pos, ctitle, content, href in kw["chapters"]
        ]
        for aid, skey, mt, sz, shref in kw["assets"]:
            mem.assets[aid] = UserTitleAsset(
                id=aid,
                title_id=ut.id,
                storage_key=skey,
                media_type=mt,
                size_bytes=sz,
                source_href=shref,
                created_at="2026-11-22T00:00:00+00:00",
            )
        return ut

    async def fake_list(conn: Any, user_id: int) -> list[UserTitle]:
        return [t for t in mem.titles.values() if t.user_id == user_id]

    async def fake_get(conn: Any, user_id: int, title_id: uuid.UUID) -> UserTitle | None:
        t = mem.titles.get(title_id)
        return t if t and t.user_id == user_id else None

    async def fake_file(conn: Any, user_id: int, title_id: uuid.UUID) -> UserTitleFile | None:
        t = mem.titles.get(title_id)
        return mem.files.get(title_id) if t and t.user_id == user_id else None

    async def fake_summaries(
        conn: Any, user_id: int, title_id: uuid.UUID
    ) -> list[UserTitleChapterSummary]:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id:
            return []
        return [
            UserTitleChapterSummary(
                id=c.id,
                title_id=c.title_id,
                position=c.position,
                title=c.title,
                source_href=c.source_href,
            )
            for c in mem.chapters.get(title_id, [])
        ]

    async def fake_chapter(
        conn: Any, user_id: int, title_id: uuid.UUID, position: int
    ) -> UserTitleChapter | None:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id:
            return None
        return next((c for c in mem.chapters.get(title_id, []) if c.position == position), None)

    async def fake_asset(
        conn: Any, user_id: int, title_id: uuid.UUID, asset_id: uuid.UUID
    ) -> UserTitleAsset | None:
        t = mem.titles.get(title_id)
        a = mem.assets.get(asset_id)
        if not t or t.user_id != user_id or not a or a.title_id != title_id:
            return None
        return a

    async def fake_record_read(conn: Any, user_id: int, title_id: uuid.UUID, position: int) -> None:
        t = mem.titles.get(title_id)
        if t and t.user_id == user_id:
            mem.titles[title_id] = UserTitle(
                **{**t.__dict__, "last_read_position": position}
            )

    async def fake_update(
        conn: Any,
        *,
        user_id: int,
        title_id: uuid.UUID,
        title: str,
        authors: list[str],
        description: str | None,
    ) -> UserTitle | None:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id:
            return None
        updated = UserTitle(
            **{**t.__dict__, "title": title, "authors": authors, "description": description}
        )
        mem.titles[title_id] = updated
        return updated

    async def fake_delete_collect(
        conn: Any, user_id: int, title_id: uuid.UUID
    ) -> list[str] | None:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id:
            return None
        keys: list[str] = []
        if t.cover_storage_key:
            keys.append(t.cover_storage_key)
        f = mem.files.pop(title_id, None)
        if f:
            keys.append(f.storage_key)
        for aid in [k for k, v in mem.assets.items() if v.title_id == title_id]:
            keys.append(mem.assets.pop(aid).storage_key)
        mem.chapters.pop(title_id, None)
        mem.titles.pop(title_id, None)
        return keys

    async def fake_current_user(request: Request) -> User | None:
        request.state.current_user = active_user[0]
        request.state.ban_notice = None
        request.state.active_ban = None
        return active_user[0]

    def fake_require_user() -> User:
        from fastapi import HTTPException

        if active_user[0] is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        return active_user[0]

    async def fake_conn() -> Any:
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[require_current_user] = fake_require_user
    app.dependency_overrides[get_connection] = fake_conn

    epub_data = build_epub_bytes(
        title="Книга Алисы",
        authors=["Алиса"],
        description="Секретная книга",
        chapters=[
            ChapterSpec(
                "ch1.xhtml",
                "Глава 1",
                '<p>Привет из EPUB!</p><p><img src="img.png" alt="pic"/></p>'
                "<script>alert(1)</script>",
            ),
            ChapterSpec("ch2.xhtml", "Глава 2", "<p>Конец книги.</p>"),
        ],
        cover=("cover.jpg", "image/jpeg", TINY_JPEG),
        extra_files={"img.png": ("image/png", TINY_PNG)},
    )

    try:
        with (
            patch("app.user_titles.service.create_user_title_bundle", side_effect=fake_create),
            patch("app.user_titles.service.update_user_title_metadata", side_effect=fake_update),
            patch(
                "app.user_titles.service.delete_user_title_and_collect_keys",
                side_effect=fake_delete_collect,
            ),
            patch("app.api.user_titles.list_user_titles", side_effect=fake_list),
            patch("app.api.user_titles.get_user_title", side_effect=fake_get),
            patch("app.api.user_titles.get_active_title_file", side_effect=fake_file),
            patch("app.api.user_titles.list_chapter_summaries", side_effect=fake_summaries),
            patch("app.api.user_titles.get_chapter", side_effect=fake_chapter),
            patch("app.api.user_titles.get_title_asset", side_effect=fake_asset),
            patch("app.api.user_titles.record_user_title_read", side_effect=fake_record_read),
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Upload EPUB as Alice
                up = await client.post(
                    "/my-titles/upload",
                    files={"file": ("alice.epub", epub_data, "application/epub+zip")},
                    headers={"accept": "application/json"},
                )
                assert up.status_code == 201
                body = up.json()
                tid = uuid.UUID(body["id"])
                assert body["title"] == "Книга Алисы"
                assert "storage_key" not in body

                # HTML list page & detail page render without leaking storage key
                file_key = mem.files[tid].storage_key
                list_html = (await client.get("/my-titles")).text
                assert "Книга Алисы" in list_html
                assert file_key not in list_html

                detail_html = (await client.get(f"/my-titles/{tid}")).text
                assert "Секретная книга" in detail_html
                assert "Глава 1" in detail_html
                assert file_key not in detail_html

                # Reader renders chapter 1 safely inside existing chapter.html
                ch1 = await client.get(f"/my-titles/{tid}/chapters/1")
                assert ch1.status_code == 200
                assert "Привет из EPUB!" in ch1.text
                assert "<script>alert(1)</script>" not in ch1.text
                assert f"/my-titles/{tid}/chapters/2" in ch1.text
                assert "static/js/reader-hud.js" in ch1.text
                assert "static/js/paragraph-menu.js" not in ch1.text

                # Cover, asset, and download work for Alice
                aid = next(iter(mem.assets.keys()))
                assert (await client.get(f"/my-titles/{tid}/cover")).content == TINY_JPEG
                assert (await client.get(f"/my-titles/{tid}/assets/{aid}")).content == TINY_PNG
                dl = await client.get(f"/my-titles/{tid}/download")
                assert dl.status_code == 200
                assert dl.content == epub_data
                assert file_key not in dl.headers["content-disposition"]

                # Bob gets 404 everywhere
                active_user[0] = bob
                assert (await client.get(f"/my-titles/{tid}")).status_code == 404
                assert (await client.get(f"/my-titles/{tid}/chapters/1")).status_code == 404
                assert (await client.get(f"/my-titles/{tid}/cover")).status_code == 404
                assert (await client.get(f"/my-titles/{tid}/assets/{aid}")).status_code == 404
                assert (await client.get(f"/my-titles/{tid}/download")).status_code == 404
                patch_bob = await client.patch(f"/my-titles/{tid}", json={"title": "X"})
                assert patch_bob.status_code == 404
                assert (await client.delete(f"/my-titles/{tid}")).status_code == 404

                # Anonymous gets redirected to /login
                active_user[0] = None
                anon = await client.get(f"/my-titles/{tid}", follow_redirects=False)
                assert anon.status_code == 303
                assert anon.headers["location"] == "/login"

                # Alice edits and deletes cleanly
                active_user[0] = alice
                patched = await client.patch(
                    f"/my-titles/{tid}",
                    json={"title": "Обновлённое", "authors": ["Новый Автор"], "description": "Оп"},
                )
                assert patched.status_code == 200
                assert patched.json()["title"] == "Обновлённое"

                deleted = await client.delete(f"/my-titles/{tid}")
                assert deleted.status_code == 200
                assert deleted.json() == {"deleted": True, "storage_cleanup_partial": False}
                assert _all_files(tmp_path / "ut_store") == []
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
