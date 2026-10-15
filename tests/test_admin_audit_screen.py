"""PR 353: «Журнал действий» - the log PR 343 writes on screen (newest first, the object's
link, «− было / + стало», search by object or IP, the «Действие» filter, «Кто» only with
more than one actor, the retention from config, CSV of the selection that is itself
logged) - and the global search in the top bar: screens, accounts, comments and tables,
never a secret, and a 404 for anyone but the admin."""

import csv
import io
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.admin_audit import audit_csv, diff_lines, format_value
from app.config import get_settings
from app.db import admin_audit
from app.db.admin_audit import AuditEntry
from app.db.comments import create_comment
from app.db.connection import connection
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.test_admin_auth import PASSWORD, Clock, _client, _login

SECRET_HASH = "$2b$12$never-shown-anywhere-in-the-panel"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        yield test_client
    get_settings.cache_clear()


def _admin(client: TestClient) -> TestClient:
    assert _login(client).status_code == 303
    return client


async def _user(client: TestClient, email: str, nickname: str) -> int:
    register(client, email, "hunter2pass", nickname=nickname)
    client.post("/logout")
    async with connection() as conn:
        user = await get_user_by_email(conn, email)
        assert user is not None
        await conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s", (SECRET_HASH, user.id)
        )
    return user.id


def _entries(html: str) -> list[str]:
    return re.findall(r'<li class="wn-admin-audit__item">(.*?)</li>', html, re.S)


# --- the log screen -----------------------------------------------------------------------


async def test_the_log_lists_entries_newest_first_with_a_link_to_the_object(
    client: TestClient,
) -> None:
    async with connection() as conn:
        await admin_audit.record_change(
            conn,
            admin_audit.USER_BAN,
            ip="203.0.113.5",
            entity="user",
            entity_id=42,
            entity_label="aster",
            before={"banned": False},
            after={"banned": True, "expires_at": "2026-03-25T12:00:00+00:00"},
            extra={"reason": "спам"},
        )
        await admin_audit.record_change(
            conn,
            admin_audit.COMMENT_HIDE,
            ip="198.51.100.7",
            entity="comment",
            entity_id=7,
            entity_label="#7 · aster",
            before={"hidden_at": None},
            after={"hidden_at": "2026-03-18T12:00:00+00:00"},
        )

    html = _admin(client).get("/admin/audit").text

    entries = _entries(html)
    # The two actions, then the admin's own login (newest first).
    assert "Вход" in entries[0]
    assert "Скрытие" in entries[1] and 'href="/admin/comments?comment=7"' in entries[1]
    ban = entries[2]
    assert "Блокировка" in ban and "Пользователь" in ban and "aster" in ban
    assert 'href="/admin/users?user=42"' in ban
    assert "203.0.113.5" in ban
    assert "2 поля" in ban
    # «− было / + стало» per changed field, the timestamp on the admin's clock (UTC).
    assert re.search(r"заблокирован</dt>\s*<dd>\s*<span[^>]*>.*?− нет</span>", ban, re.S)
    assert "+ да" in ban
    assert "+ 25 мар 2026, 12:00" in ban
    # What else was recorded, under the diff.
    assert "причина" in ban and "спам" in ban
    assert 'aria-current="page"' in re.search(r'href="/admin/audit"[^>]*>', html).group(0)


async def test_search_and_the_action_filter(client: TestClient) -> None:
    async with connection() as conn:
        for label, ip, action in (
            ("aster", "203.0.113.5", admin_audit.USER_BAN),
            ("bramble", "198.51.100.7", admin_audit.USER_BAN),
            ("aster", "198.51.100.7", admin_audit.USER_END_SESSIONS),
        ):
            await admin_audit.record_change(
                conn, action, ip=ip, entity="user", entity_id=1, entity_label=label
            )
    _admin(client)

    by_name = _entries(client.get("/admin/audit", params={"q": "ASTER"}).text)
    by_ip = _entries(client.get("/admin/audit", params={"q": "198.51.100"}).text)
    bans = _entries(client.get("/admin/audit", params={"action": "user_ban"}).text)
    both = _entries(client.get("/admin/audit", params={"q": "aster", "action": "user_ban"}).text)
    wildcard = client.get("/admin/audit", params={"q": "%"}).text

    assert len(by_name) == 2
    assert len(by_ip) == 2
    assert len(bans) == 2 and all("Блокировка" in entry for entry in bans)
    assert len(both) == 1 and "aster" in both[0]
    assert "Ничего не найдено" in wildcard  # a literal %, not a wildcard
    # An unknown action is no filter at all, not an error.
    assert client.get("/admin/audit", params={"action": "drop_table"}).status_code == 200


async def test_who_is_a_filter_only_with_more_than_one_actor(client: TestClient) -> None:
    html = _admin(client).get("/admin/audit").text
    assert 'name="action"' in html
    assert 'name="actor"' not in html

    async with connection() as conn:
        await conn.execute(
            "INSERT INTO admin_audit_log (at, actor, action) VALUES (%s, 'moderator', 'login')",
            (datetime.now(UTC).isoformat(),),
        )

    html = client.get("/admin/audit").text
    assert 'name="actor"' in html
    only = _entries(client.get("/admin/audit", params={"actor": "moderator"}).text)
    assert len(only) == 1 and "moderator" in only[0]


def test_the_retention_comes_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    with _client(
        monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD, ADMIN_AUDIT_RETENTION_DAYS="30"
    ) as client:
        html = _admin(client).get("/admin/audit").text
    get_settings.cache_clear()

    assert "Записи хранятся 30 дней и не редактируются" in html


async def test_the_log_screen_changes_nothing(client: TestClient) -> None:
    html = _admin(client).get("/admin/audit").text

    # The only form on the page that posts is the sidebar's logout - no edit, no delete.
    assert re.findall(r'<form[^>]*method="post"[^>]*action="([^"]+)"', html) == ["/admin/logout"]


def test_a_filter_that_matches_nothing_offers_a_reset(client: TestClient) -> None:
    _admin(client)
    # Filtered to an action that never happened: the no-results state with a reset.
    html = client.get("/admin/audit", params={"action": "comment_delete"}).text
    assert "Ничего не найдено" in html and 'href="?"' in html


# --- CSV ----------------------------------------------------------------------------------


def _rows(body: bytes) -> list[list[str]]:
    assert body.startswith("﻿".encode())
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


async def test_the_export_is_the_selection_and_is_itself_logged(client: TestClient) -> None:
    async with connection() as conn:
        await admin_audit.record_change(
            conn,
            admin_audit.USER_BAN,
            ip="203.0.113.5",
            entity="user",
            entity_id=42,
            entity_label='=HYPERLINK("http://evil")',
            before={"banned": False},
            after={"banned": True},
            extra={"reason": "спам", "token": "abc123"},
        )
        await admin_audit.record_change(
            conn, admin_audit.USER_END_SESSIONS, ip="203.0.113.5", entity="user", entity_id=43
        )
    _admin(client)

    response = client.get("/admin/audit/csv", params={"action": "user_ban"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="admin-audit-' in response.headers["content-disposition"]
    header, *rows = _rows(response.content)
    assert header[:3] == ["Время", "Кто", "Действие"]
    [row] = rows
    assert row[2] == "Блокировка"
    assert row[5].startswith("'=HYPERLINK")  # a formula arrives as text
    assert row[7] == "заблокирован: нет → да"
    assert "причина: спам" in row[8]
    assert "abc123" not in response.text  # scrubbed when it was written (PR 343)
    async with connection() as conn:
        [export, *_] = await admin_audit.list_entries(conn)
    assert export.action == admin_audit.AUDIT_EXPORT
    assert export.details == {
        "rows": 1,
        "matching": 1,
        "query": None,
        "action": "user_ban",
        "actor": None,
    }


def test_values_read_as_words() -> None:
    tz = "Europe/Moscow"
    assert format_value(None, tz) == "—"
    assert format_value(True, tz) == "да"
    assert format_value("2026-03-18T12:00:00+00:00", tz) == "18 мар 2026, 15:00"
    assert format_value("removed", tz) == "удалён целиком"
    assert format_value({"a": 1}, tz) == '{"a": 1}'
    # A creation has nothing before, a deletion nothing after.
    [created] = diff_lines({"before": {}, "after": {"status": "deleted"}}, tz)
    assert (created.before, created.after) == (None, "удалён")
    body = audit_csv(
        [AuditEntry(1, "2026-03-18T12:00:00+00:00", "admin", "login", None, None, None, None, {})],
        tz,
    ).decode("utf-8-sig")
    assert "2026-03-18 15:00:00,Администратор,Вход" in body


# --- global search ------------------------------------------------------------------------


def _search(client: TestClient, query: str) -> dict:
    response = client.get("/admin/search/suggest", params={"q": query})
    assert response.status_code == 200
    return response.json()


def _hits(data: dict, group: str) -> list[dict]:
    return next((g["hits"] for g in data["groups"] if g["key"] == group), [])


async def test_search_finds_accounts_comments_tables_and_screens(client: TestClient) -> None:
    alice = await _user(client, "alice@example.com", "aster")
    async with connection() as conn:
        comment = (
            await create_comment(conn, alice, "1--some-title", "1", "5", "", 0, "Сцена с чаепитием")
        ).id
    _admin(client)

    by_email = _hits(_search(client, "ALICE@"), "users")
    by_nick = _hits(_search(client, "ast"), "users")
    by_id = _hits(_search(client, str(alice)), "users")
    comments = _hits(_search(client, "чаепит"), "comments")
    tables = _hits(_search(client, "download"), "tables")
    screens = _hits(_search(client, "журнал"), "screens")

    assert [hit["href"] for hit in by_email] == [f"/admin/users?user={alice}"]
    assert by_nick[0]["label"] == "aster" and "alice@example.com" in by_nick[0]["sub"]
    assert by_id[0]["href"] == f"/admin/users?user={alice}"
    assert comments == [
        {
            "label": "Сцена с чаепитием",
            "href": f"/admin/comments?comment={comment}",
            "sub": f"#{comment} · aster · 1--some-title",
        }
    ]
    assert "download_history" in {hit["label"] for hit in tables}
    assert screens[0] == {"label": "Журнал", "href": "/admin/audit", "sub": "Платформа"}
    # PR 358: «Модерация» is found by what it holds, not only by its name.
    assert _hits(_search(client, "жалобы"), "screens")[0] == {
        "label": "Модерация",
        "href": "/admin/moderation",
        "sub": "Сообщество",
    }
    data = _search(client, "aster")
    assert data["all"] == "/admin/search?q=aster"
    assert _hits(data, "users") and data["groups"][0]["key"] == "users"


async def test_search_never_shows_a_secret(client: TestClient) -> None:
    await _user(client, "alice@example.com", "aster")
    _admin(client)

    for query in (SECRET_HASH, "$2b$12", "never-shown"):
        data = _search(client, query)
        assert not _hits(data, "users"), query
        assert SECRET_HASH not in str(data["groups"])  # the query itself is echoed back
    found = client.get("/admin/search/suggest", params={"q": "alice"}).text
    page = client.get("/admin/search", params={"q": "alice"}).text
    for body in (found, page):
        assert "alice@example.com" in body
        assert SECRET_HASH not in body
        assert "password_hash" not in body and "session_version" not in body


async def test_short_queries_and_titles_find_nothing(client: TestClient) -> None:
    await _user(client, "alice@example.com", "aster")
    _admin(client)

    assert _search(client, "a")["groups"] == []  # one letter matches too much
    assert _search(client, "   ")["groups"] == []
    page = client.get("/admin/search", params={"q": "a"}).text
    assert "Слишком коротко" in page
    # Titles from the SDK's catalog aren't searched at all.
    assert {g["key"] for g in _search(client, "some-title")["groups"]} <= {"comments"}


def test_the_results_page_works_without_a_script(client: TestClient) -> None:
    html = _admin(client).get("/admin/search", params={"q": "users"}).text

    assert '<h1 class="wn-admin-top__title" id="admin-page-title">Поиск</h1>' in html
    assert 'href="/admin/users"' in html  # the screen
    assert 'href="/admin/tables/users"' in html  # the table
    # The top bar's box keeps what was searched.
    assert re.search(r'data-role="admin-search-input"', html)
    assert 'name="q" value="users"' in html


async def test_the_drop_down_is_only_for_the_admin(client: TestClient) -> None:
    await _user(client, "alice@example.com", "aster")

    anonymous = client.get("/admin/search/suggest", params={"q": "alice"})
    register(client, "bob@example.com", "hunter2pass")  # a site user isn't the admin
    site_user = client.get("/admin/search/suggest", params={"q": "alice"})

    for response in (anonymous, site_user):
        assert response.status_code == 404
        assert "alice" not in response.text


def test_every_admin_page_has_the_search_box(client: TestClient) -> None:
    html = _admin(client).get("/admin").text

    assert '<form class="wn-admin-search" method="get" action="/admin/search" role="search"' in html
    assert 'aria-controls="admin-search-results"' in html
    assert "js/admin-search.js" in html
    # Without the script the phone's icon is a plain link to the results page.
    assert 'class="wn-admin-search__toggle" href="/admin/search"' in html
