"""Unit and ASGI HTTP tests for publishing user EPUB titles (issue 434, no Postgres needed)."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException, Request

from app.admin_moderation import (
    item_key,
    parse_item,
    queue_rows,
    tabs,
    user_title_card,
)
from app.config import get_settings
from app.db.admin_reports import (
    USER_TITLE,
    ObjectReports,
    Queue,
    QueueItem,
    ReasonCount,
    ReportedUserTitle,
)
from app.db.user_titles import (
    VISIBILITY_HIDDEN,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLISHED,
    UserTitle,
    UserTitleAsset,
    UserTitleChapter,
    UserTitleChapterSummary,
    UserTitleFile,
)
from app.db.users import User
from app.user_titles.service import (
    PUBLISH_RATE_LIMIT_MAX,
    PublishRateLimitError,
    UserTitleError,
    UserTitleForbiddenError,
    publish_user_title,
    unpublish_user_title,
)
from tests.epub_helpers import TINY_JPEG, TINY_PNG, ChapterSpec, build_epub_bytes

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "app/db/migrations/0034_user_titles_publishing.sql"


def _make_title(
    *,
    title_id: uuid.UUID | None = None,
    user_id: int = 1,
    title: str = "Книга",
    visibility: str = VISIBILITY_PRIVATE,
    chapter_count: int = 2,
    allow_download: bool = False,
    hidden_reason: str | None = None,
) -> UserTitle:
    return UserTitle(
        id=title_id or uuid.uuid4(),
        user_id=user_id,
        title=title,
        description="Описание",
        authors=["Автор"],
        cover_storage_key=None,
        cover_media_type=None,
        last_read_position=None,
        last_read_at=None,
        created_at="2026-11-22T00:00:00+00:00",
        updated_at="2026-11-22T00:00:00+00:00",
        chapter_count=chapter_count,
        visibility=visibility,
        published_at="2026-11-22T01:00:00+00:00" if visibility == VISIBILITY_PUBLISHED else None,
        hidden_reason=hidden_reason,
        hidden_at="2026-11-22T02:00:00+00:00" if visibility == VISIBILITY_HIDDEN else None,
        allow_download=allow_download,
        owner_name="Alice",
    )


def _make_file(title_id: uuid.UUID, *, import_status: str = "ready") -> UserTitleFile:
    return UserTitleFile(
        id=uuid.uuid4(),
        title_id=title_id,
        storage_key=f"epubs/{uuid.uuid4()}.epub",
        original_filename="book.epub",
        media_type="application/epub+zip",
        size_bytes=1024,
        sha256="deadbeef",
        import_status=import_status,
        import_error=None,
        is_active=True,
        uploaded_at="2026-11-22T00:00:00+00:00",
    )


# --- Migration contract -------------------------------------------------------------------


def test_migration_0034_defines_required_schema_and_progress_backfill() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'" in sql
    assert "CHECK (visibility IN ('private', 'published', 'hidden'))" in sql
    assert "ADD COLUMN published_at TEXT" in sql
    assert "ADD COLUMN hidden_reason TEXT" in sql
    assert "ADD COLUMN hidden_at TEXT" in sql
    assert "ADD COLUMN allow_download BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "WHERE visibility = 'published'" in sql
    assert "CREATE TABLE user_title_reading_progress" in sql
    assert "PRIMARY KEY (user_id, title_id)" in sql
    assert "INSERT INTO user_title_reading_progress" in sql
    assert "CHECK (target_type IN ('comment', 'profile', 'user_title'))" in sql
    assert "ADD COLUMN target_uuid UUID" in sql
    assert "ADD COLUMN user_title_id UUID REFERENCES user_titles(id)" in sql


# --- Service unit tests -------------------------------------------------------------------


async def test_publish_rejects_hidden_not_ready_and_empty_books() -> None:
    tid = uuid.uuid4()

    # 1. Moderator-hidden book cannot be re-published by owner
    hidden = _make_title(title_id=tid, visibility=VISIBILITY_HIDDEN, hidden_reason="Копирайт")
    with (
        patch("app.user_titles.service.get_user_title", return_value=hidden),
        pytest.raises(UserTitleForbiddenError) as exc_hidden,
    ):
        await publish_user_title(AsyncMock(), user_id=1, title_id=tid)
    assert exc_hidden.value.status_code == 403

    # 2. Book with import_status != 'ready' cannot be published
    pending_title = _make_title(title_id=tid, visibility=VISIBILITY_PRIVATE)
    with (
        patch("app.user_titles.service.get_user_title", return_value=pending_title),
        patch(
            "app.user_titles.service.get_active_title_file",
            return_value=_make_file(tid, import_status="pending"),
        ),
        pytest.raises(UserTitleError) as exc_pending,
    ):
        await publish_user_title(AsyncMock(), user_id=1, title_id=tid)
    assert exc_pending.value.status_code == 400

    # 3. Book without chapters cannot be published
    empty_title = _make_title(title_id=tid, visibility=VISIBILITY_PRIVATE, chapter_count=0)
    with (
        patch("app.user_titles.service.get_user_title", return_value=empty_title),
        patch(
            "app.user_titles.service.get_active_title_file",
            return_value=_make_file(tid, import_status="ready"),
        ),
        pytest.raises(UserTitleError) as exc_empty,
    ):
        await publish_user_title(AsyncMock(), user_id=1, title_id=tid)
    assert exc_empty.value.status_code == 400


async def test_publish_rate_limit_enforced() -> None:
    tid = uuid.uuid4()
    private_title = _make_title(title_id=tid, visibility=VISIBILITY_PRIVATE)
    published_title = replace(private_title, visibility=VISIBILITY_PUBLISHED)
    ready_file = _make_file(tid, import_status="ready")

    with (
        patch("app.user_titles.service.get_user_title", return_value=private_title),
        patch("app.user_titles.service.get_active_title_file", return_value=ready_file),
        patch("app.user_titles.service.set_user_title_published", return_value=published_title),
    ):
        for _ in range(PUBLISH_RATE_LIMIT_MAX):
            result = await publish_user_title(AsyncMock(), user_id=99, title_id=tid)
            assert result is not None and result.visibility == VISIBILITY_PUBLISHED
        with pytest.raises(PublishRateLimitError) as exc_info:
            await publish_user_title(AsyncMock(), user_id=99, title_id=tid)
    assert exc_info.value.status_code == 429


async def test_unpublish_cannot_unlock_moderator_hidden_book() -> None:
    tid = uuid.uuid4()
    hidden = _make_title(title_id=tid, visibility=VISIBILITY_HIDDEN)
    with (
        patch("app.user_titles.service.get_user_title", return_value=hidden),
        pytest.raises(UserTitleForbiddenError),
    ):
        await unpublish_user_title(AsyncMock(), user_id=1, title_id=tid)


# --- Admin moderation presentation tests --------------------------------------------------


def test_moderation_presentation_supports_user_title_uuids() -> None:
    tid = str(uuid.uuid4())
    key = item_key(USER_TITLE, tid)
    assert key == f"user_title-{tid}"
    assert parse_item(key) == (USER_TITLE, tid)
    assert parse_item("user_title-not-a-uuid") is None

    q = Queue(
        items=[
            QueueItem(
                target_type=USER_TITLE,
                target_id=tid,
                reports=3,
                first_at="2026-11-22T10:00:00+00:00",
                last_at="2026-11-22T11:00:00+00:00",
                top_reason="copyright",
                author_id=7,
                author="Alice",
                text="Хроники Заката",
                hidden=False,
                deleted=False,
            )
        ],
        total=1,
        counts={USER_TITLE: 1},
        reports=3,
    )
    tab_list = tabs(q, None)
    assert [t.label for t in tab_list] == ["Все · 1", "Комментарии · 0", "Профили · 0", "Книги · 1"]
    rows = queue_rows(q.items, None, key, "UTC")
    assert len(rows) == 1
    assert rows[0].kind == "Книга"
    assert rows[0].text == "Хроники Заката"
    assert rows[0].reason == "Нарушение авторских прав"

    reports = ObjectReports(
        count=3,
        first_at="2026-11-22T10:00:00+00:00",
        reasons=[ReasonCount("copyright", 3)],
        notes=[],
    )
    card = user_title_card(
        ReportedUserTitle(
            id=tid,
            user_id=7,
            owner="Alice",
            title="Хроники Заката",
            authors=["Анна"],
            description="Описание книги",
            visibility=VISIBILITY_PUBLISHED,
            hidden_reason=None,
            hidden_at=None,
            published_at="2026-11-22T09:00:00+00:00",
            chapter_count=12,
            has_cover=True,
        ),
        tid,
        reports,
        "UTC",
    )
    assert card.state == VISIBILITY_PUBLISHED
    assert card.where_href == f"/community-titles/{tid}"
    assert card.title_name == "Хроники Заката"
    assert card.chapter_count == 12


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_report_form_js_handles_user_title_uuid_string() -> None:
    tid = str(uuid.uuid4())
    script = f"""
    const fs = require('node:fs');
    const vm = require('node:vm');
    const source = fs.readFileSync({str(ROOT / "app/static/js/report-form.js")!r}, 'utf8');
    const listeners = {{}};
    const mkEl = (extra = {{}}) => ({{
      hidden: false, textContent: '', value: '', maxLength: 500, dataset: {{}},
      addEventListener: () => {{}}, setAttribute: () => {{}}, removeAttribute: () => {{}},
      querySelector: () => null, focus: () => {{}}, ...extra,
    }});
    const targetType = mkEl({{ name: 'target_type' }});
    const targetId = mkEl({{ name: 'target_id' }});
    const details = mkEl({{ name: 'details' }});
    const form = mkEl({{
      elements: {{ details, target_type: targetType, target_id: targetId }},
      reset: () => {{}},
    }});
    const root = mkEl({{
      parentNode: {{ insertBefore: () => {{}} }}, nextSibling: null,
      querySelector: (s) => ({{
        '[data-role="report-form-form"]': form,
        '[data-role="report-count"]': mkEl(),
        '[data-role="report-error"]': mkEl(),
        '[data-role="report-submit"]': mkEl(),
        '[data-role="report-cancel"]': mkEl(),
      }})[s] || null,
    }});
    const dialogTitle = mkEl();
    const dialog = mkEl({{
      showModal: () => {{ dialog.open = true; }},
      querySelector: (s) => ({{
        '[data-role="report-dialog-title"]': dialogTitle,
        '[data-role="report-dialog-body"]': {{ append: () => {{}} }},
        '[data-role="report-dialog-close"]': mkEl(),
      }})[s] || null,
    }});
    const ctx = {{
      document: {{
        querySelector: (s) => ({{
          '[data-role="report-form"]': root,
          '[data-role="report-dialog"]': dialog,
        }})[s],
        addEventListener: (t, fn) => (listeners[t] ||= []).push(fn),
      }},
      matchMedia: () => ({{ matches: false }}),
      setTimeout: () => 1, clearTimeout: () => {{}},
    }};
    ctx.window = ctx;
    vm.runInNewContext(source, ctx);
    const btn = mkEl({{ dataset: {{ reportTargetType: 'user_title', reportTargetId: {tid!r} }} }});
    listeners.click[0]({{ target: {{ closest: () => btn }}, preventDefault: () => {{}} }});
    process.stdout.write(JSON.stringify({{
      open: Boolean(dialog.open),
      title: dialogTitle.textContent,
      targetType: targetType.value,
      targetId: targetId.value,
    }}));
    """
    completed = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    )
    expected = (
        '{"open":true,"title":"Пожаловаться на книгу",'
        f'"targetType":"user_title","targetId":"{tid}"}}'
    )
    assert completed.stdout == expected


# --- Full ASGI HTTP flow without Postgres -------------------------------------------------


class _InMemoryCommunityStore:
    def __init__(self) -> None:
        self.titles: dict[uuid.UUID, UserTitle] = {}
        self.files: dict[uuid.UUID, UserTitleFile] = {}
        self.chapters: dict[uuid.UUID, list[UserTitleChapter]] = {}
        self.assets: dict[uuid.UUID, UserTitleAsset] = {}
        self.progress: dict[tuple[int, uuid.UUID], tuple[int, str]] = {}

    def with_progress(self, title: UserTitle, viewer_id: int | None) -> UserTitle:
        prog = self.progress.get((viewer_id, title.id)) if viewer_id is not None else None
        return replace(
            title,
            last_read_position=prog[0] if prog else None,
            last_read_at=prog[1] if prog else None,
        )


async def test_publish_community_access_progress_download_and_unpublish_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.auth.dependencies import get_current_user, require_current_user
    from app.db.connection import get_connection
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
        email_verified_at="2026-11-22T00:00:00+00:00",
    )
    bob = User(
        id=2,
        email="bob@example.com",
        password_hash="x",
        created_at="2026-11-22T00:00:00+00:00",
        nickname="Bob",
        email_verified_at="2026-11-22T00:00:00+00:00",
    )
    active_user: list[User | None] = [alice]
    mem = _InMemoryCommunityStore()

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
            visibility=VISIBILITY_PRIVATE,
            allow_download=False,
            owner_name="Alice",
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

    async def fake_get_owner(conn: Any, user_id: int, title_id: uuid.UUID) -> UserTitle | None:
        t = mem.titles.get(title_id)
        return mem.with_progress(t, user_id) if t and t.user_id == user_id else None

    async def fake_file_owner(conn: Any, user_id: int, title_id: uuid.UUID) -> UserTitleFile | None:
        t = mem.titles.get(title_id)
        return mem.files.get(title_id) if t and t.user_id == user_id else None

    async def fake_summaries_owner(
        conn: Any, user_id: int, title_id: uuid.UUID
    ) -> list[UserTitleChapterSummary]:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id:
            return []
        return [
            UserTitleChapterSummary(c.id, c.title_id, c.position, c.title, c.source_href)
            for c in mem.chapters.get(title_id, [])
        ]

    async def fake_chapter_owner(
        conn: Any, user_id: int, title_id: uuid.UUID, position: int
    ) -> UserTitleChapter | None:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id:
            return None
        return next((c for c in mem.chapters.get(title_id, []) if c.position == position), None)

    async def fake_set_published(
        conn: Any, user_id: int, title_id: uuid.UUID, *, allow_download: bool = False
    ) -> UserTitle | None:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id or t.visibility == VISIBILITY_HIDDEN:
            return None
        updated = replace(
            t,
            visibility=VISIBILITY_PUBLISHED,
            published_at=t.published_at or "2026-11-22T05:00:00+00:00",
            allow_download=allow_download,
        )
        mem.titles[title_id] = updated
        return mem.with_progress(updated, user_id)

    async def fake_set_private(conn: Any, user_id: int, title_id: uuid.UUID) -> UserTitle | None:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id or t.visibility != VISIBILITY_PUBLISHED:
            return None
        updated = replace(t, visibility=VISIBILITY_PRIVATE)
        mem.titles[title_id] = updated
        return mem.with_progress(updated, user_id)

    async def fake_set_dl(
        conn: Any, user_id: int, title_id: uuid.UUID, *, allow_download: bool
    ) -> UserTitle | None:
        t = mem.titles.get(title_id)
        if not t or t.user_id != user_id:
            return None
        updated = replace(t, allow_download=allow_download)
        mem.titles[title_id] = updated
        return mem.with_progress(updated, user_id)

    async def fake_record_read(conn: Any, user_id: int, title_id: uuid.UUID, position: int) -> None:
        mem.progress[(user_id, title_id)] = (position, "2026-11-22T06:00:00+00:00")

    async def fake_list_pub(
        conn: Any,
        *,
        query: str = "",
        sort: str = "newest",
        page: int = 1,
        page_size: int = 24,
        viewer_user_id: int | None = None,
    ) -> tuple[list[UserTitle], int]:
        items = [
            mem.with_progress(t, viewer_user_id)
            for t in mem.titles.values()
            if t.visibility == VISIBILITY_PUBLISHED
        ]
        q = query.strip().lower()
        if q:
            items = [
                t
                for t in items
                if q in t.title.lower()
                or any(q in a.lower() for a in t.authors)
                or q in (t.description or "").lower()
            ]
        return items, len(items)

    async def fake_get_pub(
        conn: Any, title_id: uuid.UUID, *, viewer_user_id: int | None = None
    ) -> UserTitle | None:
        t = mem.titles.get(title_id)
        if not t or t.visibility != VISIBILITY_PUBLISHED:
            return None
        return mem.with_progress(t, viewer_user_id)

    async def fake_pub_summaries(
        conn: Any, title_id: uuid.UUID
    ) -> list[UserTitleChapterSummary]:
        t = mem.titles.get(title_id)
        if not t or t.visibility != VISIBILITY_PUBLISHED:
            return []
        return [
            UserTitleChapterSummary(c.id, c.title_id, c.position, c.title, c.source_href)
            for c in mem.chapters.get(title_id, [])
        ]

    async def fake_pub_chapter(
        conn: Any, title_id: uuid.UUID, position: int
    ) -> UserTitleChapter | None:
        t = mem.titles.get(title_id)
        if not t or t.visibility != VISIBILITY_PUBLISHED:
            return None
        return next((c for c in mem.chapters.get(title_id, []) if c.position == position), None)

    async def fake_pub_asset(
        conn: Any, title_id: uuid.UUID, asset_id: uuid.UUID
    ) -> UserTitleAsset | None:
        t = mem.titles.get(title_id)
        a = mem.assets.get(asset_id)
        if not t or t.visibility != VISIBILITY_PUBLISHED or not a or a.title_id != title_id:
            return None
        return a

    async def fake_pub_file(conn: Any, title_id: uuid.UUID) -> UserTitleFile | None:
        t = mem.titles.get(title_id)
        if not t or t.visibility != VISIBILITY_PUBLISHED:
            return None
        return mem.files.get(title_id)

    async def fake_current_user(request: Request) -> User | None:
        request.state.current_user = active_user[0]
        request.state.ban_notice = None
        request.state.active_ban = None
        return active_user[0]

    def fake_require_user() -> User:
        if active_user[0] is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        return active_user[0]

    async def fake_conn() -> Any:
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[require_current_user] = fake_require_user
    app.dependency_overrides[get_connection] = fake_conn

    epub_data = build_epub_bytes(
        title="Публичная новелла",
        authors=["Алиса Автор"],
        description="История для всех читателей.",
        chapters=[
            ChapterSpec(
                "ch1.xhtml",
                "Глава 1",
                '<p>Текст первой главы.</p><p><img src="img.png" alt="art"/></p>'
                '<p><a href="ch2.xhtml">Дальше</a></p><script>alert(1)</script>',
            ),
            ChapterSpec("ch2.xhtml", "Глава 2", "<p>Текст второй главы.</p>"),
        ],
        cover=("cover.jpg", "image/jpeg", TINY_JPEG),
        extra_files={"img.png": ("image/png", TINY_PNG)},
    )

    try:
        with (
            patch("app.user_titles.service.create_user_title_bundle", side_effect=fake_create),
            patch("app.user_titles.service.get_user_title", side_effect=fake_get_owner),
            patch("app.user_titles.service.get_active_title_file", side_effect=fake_file_owner),
            patch(
                "app.user_titles.service.set_user_title_published", side_effect=fake_set_published
            ),
            patch("app.user_titles.service.set_user_title_private", side_effect=fake_set_private),
            patch(
                "app.user_titles.service.set_user_title_allow_download", side_effect=fake_set_dl
            ),
            patch("app.api.user_titles.get_user_title", side_effect=fake_get_owner),
            patch("app.api.user_titles.get_active_title_file", side_effect=fake_file_owner),
            patch("app.api.user_titles.list_chapter_summaries", side_effect=fake_summaries_owner),
            patch("app.api.user_titles.get_chapter", side_effect=fake_chapter_owner),
            patch("app.api.user_titles.record_user_title_read", side_effect=fake_record_read),
            patch("app.api.user_titles.list_published_user_titles", side_effect=fake_list_pub),
            patch("app.api.user_titles.get_published_user_title", side_effect=fake_get_pub),
            patch(
                "app.api.user_titles.list_published_chapter_summaries",
                side_effect=fake_pub_summaries,
            ),
            patch("app.api.user_titles.get_published_chapter", side_effect=fake_pub_chapter),
            patch("app.api.user_titles.get_published_title_asset", side_effect=fake_pub_asset),
            patch("app.api.user_titles.get_published_active_title_file", side_effect=fake_pub_file),
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. Alice uploads EPUB -> starts private.
                up = await client.post(
                    "/my-titles/upload",
                    files={"file": ("pub.epub", epub_data, "application/epub+zip")},
                    headers={"accept": "application/json"},
                )
                assert up.status_code == 201
                body = up.json()
                assert body["visibility"] == VISIBILITY_PRIVATE
                assert body["allow_download"] is False
                tid = uuid.UUID(body["id"])
                aid = next(iter(mem.assets.keys()))
                file_key = mem.files[tid].storage_key

                # 2. Private book is invisible on /community-titles for Bob AND Guest.
                for viewer in (bob, None):
                    active_user[0] = viewer
                    listing = await client.get(
                        "/community-titles", headers={"accept": "application/json"}
                    )
                    assert listing.json()["items"] == []
                    assert (await client.get(f"/community-titles/{tid}")).status_code == 404
                    assert (
                        await client.get(f"/community-titles/{tid}/chapters/1")
                    ).status_code == 404
                    assert (await client.get(f"/community-titles/{tid}/cover")).status_code == 404
                    assert (
                        await client.get(f"/community-titles/{tid}/assets/{aid}")
                    ).status_code == 404
                    assert (
                        await client.get(f"/community-titles/{tid}/download")
                    ).status_code == 404

                # 3. Bob cannot publish Alice's book.
                active_user[0] = bob
                bob_pub = await client.post(
                    f"/my-titles/{tid}/publish",
                    data={"confirm": "1"},
                    headers={"accept": "application/json"},
                )
                assert bob_pub.status_code == 404

                # 4. Alice must confirm publication explicitly.
                active_user[0] = alice
                no_confirm = await client.post(
                    f"/my-titles/{tid}/publish",
                    data={},
                    headers={"accept": "application/json"},
                )
                assert no_confirm.status_code == 400

                pub_resp = await client.post(
                    f"/my-titles/{tid}/publish",
                    data={"confirm": "1"},
                    headers={"accept": "application/json"},
                )
                assert pub_resp.status_code == 200
                assert pub_resp.json()["visibility"] == VISIBILITY_PUBLISHED
                assert pub_resp.json()["allow_download"] is False

                # 5. Alice reads chapter 1; Bob reads chapter 2 -> separate progress!
                assert (await client.get(f"/my-titles/{tid}/chapters/1")).status_code == 200
                assert mem.progress[(alice.id, tid)][0] == 1

                active_user[0] = bob
                comm_html = (await client.get("/community-titles?q=Новелла")).text
                assert "Публичная новелла" in comm_html
                assert "Alice" in comm_html
                assert file_key not in comm_html

                card_html = (await client.get(f"/community-titles/{tid}")).text
                assert "Публичная новелла" in card_html
                assert 'data-report-target-type="user_title"' in card_html
                assert f'data-report-target-id="{tid}"' in card_html
                # Download disabled by default for non-owners.
                assert f"/community-titles/{tid}/download" not in card_html
                assert (await client.get(f"/community-titles/{tid}/download")).status_code == 404

                # Bob reads chapter 1 (rewritten community URLs, no XSS) and chapter 2.
                ch1_bob = await client.get(f"/community-titles/{tid}/chapters/1")
                assert ch1_bob.status_code == 200
                assert "Текст первой главы." in ch1_bob.text
                assert "<script>alert(1)</script>" not in ch1_bob.text
                assert f'src="/community-titles/{tid}/assets/{aid}"' in ch1_bob.text
                assert f'href="/community-titles/{tid}/chapters/2"' in ch1_bob.text
                assert (await client.get(f"/community-titles/{tid}/chapters/2")).status_code == 200
                assert mem.progress[(bob.id, tid)][0] == 2
                assert mem.progress[(alice.id, tid)][0] == 1  # Alice's progress untouched!

                # Cover & asset serve with nosniff.
                cov = await client.get(f"/community-titles/{tid}/cover")
                assert cov.status_code == 200 and cov.content == TINY_JPEG
                assert cov.headers["x-content-type-options"] == "nosniff"
                ast = await client.get(f"/community-titles/{tid}/assets/{aid}")
                assert ast.status_code == 200 and ast.content == TINY_PNG
                assert ast.headers["x-content-type-options"] == "nosniff"

                # Bob cannot unpublish or change download policy.
                assert (await client.post(f"/my-titles/{tid}/unpublish")).status_code == 404
                assert (
                    await client.post(
                        f"/my-titles/{tid}/download-policy", data={"allow_download": "1"}
                    )
                ).status_code == 404

                # 6. Guest reads published book & cover; no report button or saved progress.
                active_user[0] = None
                guest_card = await client.get(f"/community-titles/{tid}")
                assert guest_card.status_code == 200
                assert "Публичная новелла" in guest_card.text
                assert 'data-role="community-title-report"' not in guest_card.text
                assert (await client.get(f"/community-titles/{tid}/chapters/1")).status_code == 200
                assert (await client.get(f"/community-titles/{tid}/download")).status_code == 404
                assert set(mem.progress.keys()) == {(alice.id, tid), (bob.id, tid)}

                # 7. Alice enables allow_download -> Bob can download EPUB.
                active_user[0] = alice
                dl_on = await client.post(
                    f"/my-titles/{tid}/download-policy",
                    data={"allow_download": "1"},
                    headers={"accept": "application/json"},
                )
                assert dl_on.status_code == 200 and dl_on.json()["allow_download"] is True

                active_user[0] = bob
                bob_dl = await client.get(f"/community-titles/{tid}/download")
                assert bob_dl.status_code == 200 and bob_dl.content == epub_data
                assert file_key not in bob_dl.headers["content-disposition"]

                # 8. Alice unpublishes -> immediately disappears & 404 everywhere for Bob & Guest.
                active_user[0] = alice
                unpub = await client.post(
                    f"/my-titles/{tid}/unpublish", headers={"accept": "application/json"}
                )
                assert unpub.status_code == 200
                assert unpub.json()["visibility"] == VISIBILITY_PRIVATE

                for viewer in (bob, None):
                    active_user[0] = viewer
                    listing = await client.get(
                        "/community-titles", headers={"accept": "application/json"}
                    )
                    assert listing.json()["items"] == []
                    assert (await client.get(f"/community-titles/{tid}")).status_code == 404
                    assert (
                        await client.get(f"/community-titles/{tid}/chapters/1")
                    ).status_code == 404
                    assert (await client.get(f"/community-titles/{tid}/cover")).status_code == 404
                    assert (
                        await client.get(f"/community-titles/{tid}/assets/{aid}")
                    ).status_code == 404
                    assert (
                        await client.get(f"/community-titles/{tid}/download")
                    ).status_code == 404
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


async def test_reports_endpoint_handles_user_title_access_rules_without_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    from app.auth.dependencies import get_current_user
    from app.db.reports import Report, UserTitleTarget
    from app.main import app

    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()

    alice = User(
        id=1,
        email="alice@example.com",
        password_hash="x",
        created_at="2026-11-22T00:00:00+00:00",
        nickname="Alice",
        email_verified_at="2026-11-22T00:00:00+00:00",
    )
    bob = User(
        id=2,
        email="bob@example.com",
        password_hash="x",
        created_at="2026-11-22T00:00:00+00:00",
        nickname="Bob",
        email_verified_at="2026-11-22T00:00:00+00:00",
    )
    active_user: list[User | None] = [bob]
    tid = uuid.uuid4()
    target_state: list[UserTitleTarget | None] = [
        UserTitleTarget(user_id=alice.id, visibility=VISIBILITY_PRIVATE)
    ]
    filed: list[tuple[int, str, uuid.UUID, str]] = []

    @asynccontextmanager
    async def fake_cm() -> Any:
        yield AsyncMock()

    async def fake_ut_target(conn: Any, target_id: uuid.UUID) -> UserTitleTarget | None:
        return target_state[0] if target_id == tid else None

    async def fake_open(conn: Any, uid: int, ttype: str, target: Any) -> Report | None:
        return None

    async def fake_daily(conn: Any, uid: int) -> int:
        return len(filed)

    async def fake_file(
        conn: Any, uid: int, ttype: str, target: Any, *, reason: str, details: str
    ) -> tuple[Report, bool]:
        created = not any(r[0] == uid and r[2] == target for r in filed)
        filed.append((uid, ttype, target, reason))
        return (
            Report(
                id=1,
                reporter_user_id=uid,
                target_type=ttype,
                target_id=None,
                target_uuid=target,
                reason=reason,
                details=details or None,
                status="open",
                resolution=None,
                resolved_at=None,
                created_at="2026-11-22T00:00:00+00:00",
            ),
            created,
        )

    async def fake_current_user(request: Request) -> User | None:
        request.state.current_user = active_user[0]
        request.state.ban_notice = None
        request.state.active_ban = None
        return active_user[0]

    app.dependency_overrides[get_current_user] = fake_current_user
    try:
        with (
            patch("app.api.reports.connection", side_effect=fake_cm),
            patch("app.api.reports.user_title_target", side_effect=fake_ut_target),
            patch("app.api.reports.open_report", side_effect=fake_open),
            patch("app.api.reports.reports_filed_last_day", side_effect=fake_daily),
            patch("app.api.reports.file_report", side_effect=fake_file),
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Malformed UUID -> 422
                bad = await client.post(
                    "/reports",
                    data={"target_type": "user_title", "target_id": "not-uuid", "reason": "spam"},
                )
                assert bad.status_code == 422

                # Private book -> 404
                priv = await client.post(
                    "/reports",
                    data={"target_type": "user_title", "target_id": str(tid), "reason": "spam"},
                )
                assert priv.status_code == 404

                # Published book -> Bob succeeds (201), repeat -> 200
                target_state[0] = UserTitleTarget(
                    user_id=alice.id, visibility=VISIBILITY_PUBLISHED
                )
                ok = await client.post(
                    "/reports",
                    data={
                        "target_type": "user_title",
                        "target_id": str(tid),
                        "reason": "copyright",
                        "details": "Копия",
                    },
                )
                assert ok.status_code == 201
                repeat = await client.post(
                    "/reports",
                    data={
                        "target_type": "user_title",
                        "target_id": str(tid),
                        "reason": "spam",
                    },
                )
                assert repeat.status_code == 200

                # Owner cannot report own book -> 400
                active_user[0] = alice
                own = await client.post(
                    "/reports",
                    data={"target_type": "user_title", "target_id": str(tid), "reason": "spam"},
                )
                assert own.status_code == 400

                # Hidden book -> 409
                active_user[0] = bob
                target_state[0] = UserTitleTarget(user_id=alice.id, visibility=VISIBILITY_HIDDEN)
                hid = await client.post(
                    "/reports",
                    data={"target_type": "user_title", "target_id": str(tid), "reason": "spam"},
                )
                assert hid.status_code == 409
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
