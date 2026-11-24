"""End-to-end Postgres + ASGI tests for publishing user titles, per-reader progress,
profile privacy, reporting, and /admin moderation (issue 434)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.notifications import KIND_USER_TITLE_HIDDEN, list_recent_notifications
from app.db.user_titles import (
    VISIBILITY_HIDDEN,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLISHED,
    get_reading_progress,
    get_user_title,
)
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.db_reset import reset_app_database
from tests.epub_helpers import TINY_JPEG, TINY_PNG, ChapterSpec, build_epub_bytes
from tests.test_admin_auth import PASSWORD as ADMIN_PASSWORD
from tests.test_admin_auth import _login as admin_login


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("USER_TITLES_DIR", str(tmp_path / "user_titles"))
    monkeypatch.setenv("USER_EPUB_MAX_SIZE_BYTES", str(2 * 1024 * 1024))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _sample_epub(title: str = "Книга Алисы") -> bytes:
    return build_epub_bytes(
        title=title,
        authors=["Алиса Автор"],
        description="Описание опубликованной книги.",
        chapters=[
            ChapterSpec(
                "ch1.xhtml",
                "Глава 1",
                '<p>Первая глава.</p><p><img src="images/pic.png" alt="img"></p>',
            ),
            ChapterSpec("ch2.xhtml", "Глава 2", "<p>Вторая глава.</p>"),
        ],
        cover=("images/cover.jpg", "image/jpeg", TINY_JPEG),
        extra_files={"images/pic.png": ("image/png", TINY_PNG)},
    )


async def _user_id(email: str) -> int:
    async with connection() as conn:
        user = await get_user_by_email(conn, email)
    assert user is not None
    return user.id


def _upload(client: TestClient, title: str = "Книга Алисы") -> dict:
    resp = client.post(
        "/my-titles/upload",
        files={"file": ("book.epub", _sample_epub(title), "application/epub+zip")},
        headers={"accept": "application/json"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _admin_csrf(client: TestClient) -> str:
    html = client.get("/admin/moderation").text
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def test_schema_constraints_and_progress_migration(client: TestClient) -> None:
    register(client, "alice@example.com", nickname="Alice")
    alice_id = await _user_id("alice@example.com")
    tid = uuid.UUID(_upload(client)["id"])

    async with connection() as conn:
        ut = await get_user_title(conn, alice_id, tid)
        assert ut is not None
        assert ut.visibility == VISIBILITY_PRIVATE
        assert ut.allow_download is False

        # Invalid visibility rejected by CHECK constraint.
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                "UPDATE user_titles SET visibility = 'draft' WHERE id = %s",
                (tid,),
            )

        # Invalid reading progress position rejected by CHECK constraint.
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                "INSERT INTO user_title_reading_progress "
                "(user_id, title_id, last_read_position, last_read_at) "
                "VALUES (%s, %s, 0, '2026-11-22T00:00:00+00:00')",
                (alice_id, tid),
            )

        # reports CHECK enforces target_uuid for user_title and target_id for profile.
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                "INSERT INTO reports (reporter_user_id, target_type, target_id, reason, "
                "created_at) VALUES (%s, 'user_title', 1, 'spam', '2026-11-22T00:00:00+00:00')",
                (alice_id,),
            )


async def test_publish_per_reader_progress_profile_privacy_and_unpublish(
    client: TestClient,
) -> None:
    register(client, "alice@example.com", nickname="Alice")
    alice_id = await _user_id("alice@example.com")
    payload = _upload(client, title="Сказание о Севере")
    tid = uuid.UUID(payload["id"])

    # Alice reads chapter 1 before publishing.
    assert client.get(f"/my-titles/{tid}/chapters/1").status_code == 200
    async with connection() as conn:
        assert (await get_reading_progress(conn, alice_id, tid))[0] == 1

    # Publish with confirmation.
    pub = client.post(
        f"/my-titles/{tid}/publish",
        data={"confirm": "1"},
        headers={"accept": "application/json"},
    )
    assert pub.status_code == 200
    assert pub.json()["visibility"] == VISIBILITY_PUBLISHED

    alice_cookies = dict(client.cookies)

    # Bob sees the book in /community-titles and on Alice's public profile.
    client.cookies.clear()
    register(client, "bob@example.com", nickname="Bob")
    bob_id = await _user_id("bob@example.com")

    assert "Сказание о Севере" in client.get("/community-titles").text
    assert "Сказание о Севере" in client.get(f"/profile/{alice_id}").text

    # Bob reads chapter 2 -> his progress is 2, Alice's stays 1.
    ch2 = client.get(f"/community-titles/{tid}/chapters/2")
    assert ch2.status_code == 200
    assert "Вторая глава." in ch2.text
    async with connection() as conn:
        assert (await get_reading_progress(conn, bob_id, tid))[0] == 2
        assert (await get_reading_progress(conn, alice_id, tid))[0] == 1

    # Bob cannot mutate Alice's published book.
    assert client.post(f"/my-titles/{tid}/unpublish").status_code == 404
    assert client.delete(f"/my-titles/{tid}").status_code == 404

    # Alice hides her library in privacy settings -> published books hide on public profile
    # for Bob, while Alice still sees them on her own /profile.
    client.cookies.clear()
    client.cookies.update(alice_cookies)
    client.post(
        "/settings/account/privacy",
        data={"show_currently_reading": "on", "show_friends": "on"},
    )
    assert "Сказание о Севере" in client.get("/profile").text

    bob_cookies = None
    client.cookies.clear()
    register(client, "carol@example.com", nickname="Carol")
    assert "Сказание о Севере" not in client.get(f"/profile/{alice_id}").text
    # Still visible in /community-titles until unpublished.
    assert "Сказание о Севере" in client.get("/community-titles").text

    # Guest can read published book & cover without saving progress.
    bob_cookies = dict(client.cookies)
    client.cookies.clear()
    assert client.get(f"/community-titles/{tid}").status_code == 200
    assert client.get(f"/community-titles/{tid}/chapters/1").status_code == 200
    assert client.get(f"/community-titles/{tid}/cover").status_code == 200
    assert client.get(f"/community-titles/{tid}/download").status_code == 404

    # Alice unpublishes -> immediately 404 for Carol and Guest, gone from /community-titles.
    client.cookies.clear()
    client.cookies.update(alice_cookies)
    unpub = client.post(
        f"/my-titles/{tid}/unpublish", headers={"accept": "application/json"}
    )
    assert unpub.status_code == 200
    assert unpub.json()["visibility"] == VISIBILITY_PRIVATE

    client.cookies.clear()
    client.cookies.update(bob_cookies)
    assert "Сказание о Севере" not in client.get("/community-titles").text
    assert client.get(f"/community-titles/{tid}").status_code == 404
    assert client.get(f"/community-titles/{tid}/chapters/1").status_code == 404
    assert client.get(f"/community-titles/{tid}/cover").status_code == 404


async def test_reporting_and_admin_moderation_hide_notify_and_undo(client: TestClient) -> None:
    register(client, "alice@example.com", nickname="Alice")
    alice_id = await _user_id("alice@example.com")
    tid = uuid.UUID(_upload(client, title="Спорная книга")["id"])

    # Cannot report private book or own book.
    alice_cookies = dict(client.cookies)
    client.cookies.clear()
    register(client, "bob@example.com", nickname="Bob")
    bob_cookies = dict(client.cookies)

    priv_rep = client.post(
        "/reports",
        data={"target_type": "user_title", "target_id": str(tid), "reason": "copyright"},
    )
    assert priv_rep.status_code == 404

    # Alice publishes -> Alice still cannot report own book.
    client.cookies.clear()
    client.cookies.update(alice_cookies)
    assert (
        client.post(
            f"/my-titles/{tid}/publish",
            data={"confirm": "1"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    own_rep = client.post(
        "/reports",
        data={"target_type": "user_title", "target_id": str(tid), "reason": "spam"},
    )
    assert own_rep.status_code == 400

    # Bob reports Alice's published book (201, repeat -> 200).
    client.cookies.clear()
    client.cookies.update(bob_cookies)
    rep1 = client.post(
        "/reports",
        data={
            "target_type": "user_title",
            "target_id": str(tid),
            "reason": "copyright",
            "details": "Нарушение авторских прав",
        },
    )
    assert rep1.status_code == 201
    rep2 = client.post(
        "/reports",
        data={
            "target_type": "user_title",
            "target_id": str(tid),
            "reason": "copyright",
            "details": "Уточнение жалобы",
        },
    )
    assert rep2.status_code == 200

    # Admin logs into /admin and moderates the reported book.
    client.cookies.clear()
    admin_login(client)
    mod_html = client.get(f"/admin/moderation?item=user_title-{tid}").text
    assert "Спорная книга" in mod_html
    assert "Книги · 1" in mod_html
    assert "Уточнение жалобы" in mod_html

    csrf = _admin_csrf(client)
    hide_resp = client.post(
        f"/admin/moderation/user_title/{tid}/hide",
        data={
            "csrf": csrf,
            "reason": "Нарушение авторских прав",
            "back": "/admin/moderation",
        },
        follow_redirects=False,
    )
    assert hide_resp.status_code == 303

    # Verify DB visibility, audit log entry, and owner notification.
    async with connection() as conn:
        ut = await get_user_title(conn, alice_id, tid)
        assert ut is not None
        assert ut.visibility == VISIBILITY_HIDDEN
        assert ut.hidden_reason == "Нарушение авторских прав"

        cur = await conn.execute(
            "SELECT id, action, entity, entity_id, details FROM admin_audit_log "
            "WHERE action = 'reports_hide_user_title' ORDER BY id DESC LIMIT 1"
        )
        audit_row = await cur.fetchone()
        assert audit_row is not None
        assert audit_row["entity"] == "user_title"
        assert audit_row["entity_id"] == str(tid)
        assert audit_row["details"]["reason"] == "Нарушение авторских прав"
        audit_entry_id = audit_row["id"]

        notices = await list_recent_notifications(conn, alice_id, limit=10)
        assert any(
            n.kind == KIND_USER_TITLE_HIDDEN
            and n.user_title_id == str(tid)
            and "Нарушение авторских прав" in (n.message or "")
            for n in notices
        )

    # Book is immediately 404 on /community-titles for Bob and Guest.
    admin_cookies = dict(client.cookies)
    client.cookies.clear()
    assert client.get(f"/community-titles/{tid}").status_code == 404
    assert client.get(f"/community-titles/{tid}/chapters/1").status_code == 404

    # Alice sees hidden status & reason on /my-titles/{tid} and CANNOT re-publish!
    client.cookies.clear()
    client.cookies.update(alice_cookies)
    my_detail = client.get(f"/my-titles/{tid}").text
    assert "Книга скрыта модератором" in my_detail
    assert "Нарушение авторских прав" in my_detail
    repub = client.post(
        f"/my-titles/{tid}/publish",
        data={"confirm": "1"},
        headers={"accept": "application/json"},
    )
    assert repub.status_code == 403

    # Notification renders cleanly in Alice's notifications page.
    notif_html = client.get("/notifications").text
    assert "скрыла вашу книгу с публикации" in notif_html
    assert "Спорная книга" in notif_html

    # Admin undoes the moderation hide -> book returns to published, reports reopen.
    client.cookies.clear()
    client.cookies.update(admin_cookies)
    undo_resp = client.post(
        "/admin/moderation/undo",
        data={
            "csrf": _admin_csrf(client),
            "entry": str(audit_entry_id),
            "item": f"user_title-{tid}",
            "back": "/admin/moderation",
        },
        follow_redirects=False,
    )
    assert undo_resp.status_code == 303
    async with connection() as conn:
        ut_after = await get_user_title(conn, alice_id, tid)
        assert ut_after is not None and ut_after.visibility == VISIBILITY_PUBLISHED
    assert client.get(f"/community-titles/{tid}").status_code == 200
