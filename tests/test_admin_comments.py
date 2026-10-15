"""PR 350: the admin panel's «Комментарии» - a hidden comment reaches only its author
(threads, counts, the friend feed, the profile counter, reactions and replies), hiding and
showing, deleting for good (with and without replies under it), the list's search,
filters and drawer, and the admin actions with their log entries."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.admin_comments import comment_audit_details, safe_back, without_drawer
from app.config import get_settings
from app.db import admin_audit
from app.db.admin_comments import comment_detail, list_comments
from app.db.comment_reactions import LIKE, toggle_comment_reaction
from app.db.comments import (
    count_comments,
    count_comments_by_user,
    count_comments_for_paragraph,
    create_comment,
    delete_comment,
    hide_comment,
    list_comments_for_paragraph,
    list_recent_comments_by_user,
    purge_comment,
    show_comment,
)
from app.db.connection import connection
from app.db.migrate import run_migrations
from app.db.notifications import notify_comment_reaction
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.db_reset import fresh_connection
from tests.test_admin_auth import PASSWORD, Clock, _client, _csrf, _login

NOW = datetime(2026, 3, 18, 12, tzinfo=UTC)
SLUG = "1--some-title"
WHERE = (SLUG, "1", "5", "")  # slug_url, volume, number, branch_id

# --- storage ------------------------------------------------------------------------------


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection_ = await fresh_connection()
    await run_migrations(connection_)
    return connection_


async def _user(conn: psycopg.AsyncConnection, email: str, nickname: str | None = None) -> int:
    cursor = await conn.execute(
        "INSERT INTO users (email, nickname, password_hash, created_at) "
        "VALUES (%s, %s, 'x', %s) RETURNING id",
        (email, nickname, NOW.isoformat()),
    )
    return (await cursor.fetchone())["id"]


async def _comment(conn: psycopg.AsyncConnection, user: int, body: str, **kw) -> int:
    paragraph = kw.pop("paragraph", 0)
    return (await create_comment(conn, user, *WHERE, paragraph, body, **kw)).id


async def _thread(conn: psycopg.AsyncConnection, viewer: int | None) -> list:
    return await list_comments_for_paragraph(conn, *WHERE, 0, viewer)


async def test_a_hidden_comment_reaches_only_its_author(conn: psycopg.AsyncConnection) -> None:
    alice = await _user(conn, "alice@example.com", "aster")
    bob = await _user(conn, "bob@example.com")
    spam = await _comment(conn, alice, "Главы раньше всех — в моём канале")
    kept = await _comment(conn, bob, "Спасибо за перевод")
    await hide_comment(conn, spam)

    [own_hidden, own_kept] = await _thread(conn, alice)
    assert (own_hidden.id, own_hidden.is_hidden, own_hidden.body) == (
        spam,
        True,
        "Главы раньше всех — в моём канале",
    )
    assert own_kept.id == kept and not own_kept.is_hidden
    for viewer in (bob, None):
        assert [c.id for c in await _thread(conn, viewer)] == [kept]
        assert await count_comments_for_paragraph(conn, *WHERE, 0, viewer) == 1
        assert await count_comments(conn, *WHERE, viewer) == {0: 1}
    assert await count_comments_for_paragraph(conn, *WHERE, 0, alice) == 2
    assert await count_comments(conn, *WHERE, alice) == {0: 2}
    assert await count_comments_by_user(conn, alice) == 0
    assert await count_comments_by_user(conn, alice, include_hidden=True) == 1
    assert await list_recent_comments_by_user(conn, alice) == []


async def test_a_hidden_comment_with_replies_stays_as_an_empty_placeholder(
    conn: psycopg.AsyncConnection,
) -> None:
    alice = await _user(conn, "alice@example.com")
    bob = await _user(conn, "bob@example.com")
    carol = await _user(conn, "carol@example.com")
    root = await _comment(conn, alice, "Спойлер!", attachment_path="a.mp4", attachment_kind="gif")
    reply = await _comment(conn, bob, "Не спойлерь", parent_comment_id=root)
    hidden_reply = await _comment(conn, carol, "Ты тоже", parent_comment_id=root)
    await hide_comment(conn, root)
    await hide_comment(conn, hidden_reply)

    [placeholder] = await _thread(conn, bob)

    assert placeholder.id == root and placeholder.is_hidden
    assert (placeholder.body, placeholder.attachment_path, placeholder.attachment_url) == (
        "",
        None,
        None,
    )
    assert [r.id for r in placeholder.replies] == [reply]  # carol's hidden one is gone
    [carols_view] = await _thread(conn, carol)
    assert [r.id for r in carols_view.replies] == [reply, hidden_reply]
    assert carols_view.body == ""  # alice's text still isn't carol's to see
    # Once the visible reply is gone too, nothing of the thread is left for bob.
    await delete_comment(conn, reply, bob)
    assert [c.id for c in await _thread(conn, bob)] == [root]  # the deleted reply's placeholder
    await hide_comment(conn, reply)  # deleted - nothing to hide
    assert (await _thread(conn, bob))[0].replies[0].is_deleted


async def test_nobody_reacts_or_replies_to_a_hidden_comment(conn: psycopg.AsyncConnection) -> None:
    alice = await _user(conn, "alice@example.com")
    bob = await _user(conn, "bob@example.com")
    comment = await _comment(conn, alice, "Текст")
    await toggle_comment_reaction(conn, bob, comment, LIKE)
    await hide_comment(conn, comment)

    with pytest.raises(ValueError):
        await toggle_comment_reaction(conn, bob, comment, LIKE)
    with pytest.raises(ValueError):
        await _comment(conn, bob, "Ответ", parent_comment_id=comment)
    # Shown again - both work, and the earlier like was never lost.
    await show_comment(conn, comment)
    assert await toggle_comment_reaction(conn, bob, comment, LIKE) is None  # the same like, undone
    await _comment(conn, bob, "Ответ", parent_comment_id=comment)


async def test_hide_and_show_only_change_what_needs_changing(
    conn: psycopg.AsyncConnection,
) -> None:
    alice = await _user(conn, "alice@example.com")
    comment = await _comment(conn, alice, "Текст")
    deleted = await _comment(conn, alice, "Удалю")
    await delete_comment(conn, deleted, alice)

    hidden_at = await hide_comment(conn, comment)

    assert hidden_at and await hide_comment(conn, comment) is None
    assert await hide_comment(conn, deleted) is None
    assert await hide_comment(conn, 999_999) is None
    assert await show_comment(conn, comment) == hidden_at
    assert await show_comment(conn, comment) is None
    assert await show_comment(conn, 999_999) is None


async def test_deleting_for_good_a_comment_without_replies(conn: psycopg.AsyncConnection) -> None:
    alice = await _user(conn, "alice@example.com")
    bob = await _user(conn, "bob@example.com")
    root = await _comment(conn, alice, "Корень")
    leaf = await _comment(
        conn, bob, "Ответ", parent_comment_id=root, attachment_path="b.mp4", attachment_kind="gif"
    )
    await toggle_comment_reaction(conn, alice, leaf, LIKE)
    await notify_comment_reaction(conn, leaf, alice)
    await toggle_comment_reaction(conn, bob, root, LIKE)
    await hide_comment(conn, leaf)

    purged = await purge_comment(conn, leaf)

    assert (purged.removed, purged.was_hidden, purged.attachment_path, purged.body) == (
        True,
        True,
        "b.mp4",
        "Ответ",
    )
    cursor = await conn.execute(
        "SELECT (SELECT COUNT(*) FROM comments WHERE id = %(id)s) AS comments, "
        "(SELECT COUNT(*) FROM comment_reactions WHERE comment_id = %(id)s) AS reactions, "
        "(SELECT COUNT(*) FROM notifications WHERE comment_id = %(id)s) AS notifications, "
        "(SELECT COUNT(*) FROM comment_reactions WHERE comment_id = %(root)s) AS root_likes",
        {"id": leaf, "root": root},
    )
    assert await cursor.fetchone() == {
        "comments": 0,
        "reactions": 0,
        "notifications": 0,
        "root_likes": 1,  # someone else's comment keeps its reactions
    }
    assert await purge_comment(conn, leaf) is None


async def test_deleting_for_good_keeps_the_thread_for_replies(
    conn: psycopg.AsyncConnection,
) -> None:
    alice = await _user(conn, "alice@example.com")
    bob = await _user(conn, "bob@example.com")
    root = await _comment(
        conn, alice, "Оскорбление", attachment_path="c.mp4", attachment_kind="gif"
    )
    reply = await _comment(conn, bob, "Не надо так", parent_comment_id=root)
    await hide_comment(conn, root)

    purged = await purge_comment(conn, root)

    assert not purged.removed and purged.attachment_path == "c.mp4"
    [placeholder] = await _thread(conn, None)
    assert placeholder.is_deleted and not placeholder.is_hidden
    assert (placeholder.body, placeholder.attachment_path) == ("", None)
    assert [r.id for r in placeholder.replies] == [reply]
    # The author doesn't get the text back either.
    assert (await _thread(conn, alice))[0].body == ""


async def test_the_admin_list_searches_filters_and_sorts(conn: psycopg.AsyncConnection) -> None:
    alice = await _user(conn, "alice@example.com", "aster")
    bob = await _user(conn, "bob@example.com")
    first = await _comment(conn, alice, "Перевод огонь, 100% лучший")
    second = await _comment(conn, bob, "Опечатка в абзаце", parent_comment_id=first)
    third = await _comment(conn, bob, "Реклама канала")
    await hide_comment(conn, third)
    await toggle_comment_reaction(conn, alice, second, LIKE)
    gone = await _comment(conn, alice, "Удалю")
    await delete_comment(conn, gone, alice)

    async def ids(**kw) -> list[int]:
        return [row.id for row in (await list_comments(conn, **kw)).rows]

    assert await ids() == [gone, third, second, first]  # newest first
    assert await ids(query="ASTER") == [gone, first]
    assert await ids(query="100%") == [first]
    assert await ids(query="%") == [first]  # no wildcards
    assert await ids(query=str(third)) == [third]
    assert await ids(query="some-title") == [gone, third, second, first]
    assert await ids(status="hidden") == [third]
    assert await ids(status="deleted") == [gone]
    assert await ids(status="published") == [second, first]
    assert await ids(kind="reply") == [second]
    assert await ids(kind="root") == [gone, third, first]
    assert (await ids(sort="reactions"))[0] == second
    assert await ids(sort="body; DROP TABLE comments", descending=False) == [
        first,
        second,
        third,
        gone,
    ]
    page = await list_comments(conn, page=99)
    assert (page.page, page.pages, page.total) == (1, 1, 4)
    [row] = (await list_comments(conn, query=str(second))).rows
    assert (row.author, row.likes, row.dislikes, row.status) == (
        "bob@example.com",
        1,
        0,
        "published",
    )


async def test_the_drawer_has_the_thread_and_the_authors_history(
    conn: psycopg.AsyncConnection,
) -> None:
    alice = await _user(conn, "alice@example.com", "aster")
    bob = await _user(conn, "bob@example.com")
    root = await _comment(conn, alice, "Корень")
    reply = await _comment(conn, bob, "Ответ", parent_comment_id=root, paragraph=0)
    other = await _comment(conn, bob, "Ещё")
    await hide_comment(conn, other)

    detail = await comment_detail(conn, reply)
    root_detail = await comment_detail(conn, root)

    assert detail.parent.id == root and detail.parent.author == "aster"
    assert (detail.author.comments, detail.author.hidden) == (2, 1)
    assert root_detail.parent is None and root_detail.replies == 1
    assert await comment_detail(conn, 999_999) is None
    assert comment_audit_details(detail.comment) == {
        "author_id": bob,
        "author": "bob@example.com",
        "where": f"{SLUG} · Том 1 · Глава 5 · абзац 1",
        "text": "Ответ",
    }


def test_back_only_leads_to_the_comments_list() -> None:
    assert safe_back("/admin/comments?status=hidden&comment=4") == (
        "/admin/comments?status=hidden&comment=4"
    )
    for odd in ("https://evil.example/", "/admin/users", "//evil.example", "/admin/comments?x=//a"):
        assert safe_back(odd) == "/admin/comments"
    assert without_drawer("/admin/comments?q=a&comment=4&page=2") == "/admin/comments?q=a&page=2"
    assert without_drawer("/admin/comments?comment=4") == "/admin/comments"


# --- the admin screen and actions ---------------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    import app.api.admin as admin_api
    from app.services.titles import TitleSummary

    async def summaries(slugs: set[str]) -> dict[str, TitleSummary]:
        return {SLUG: TitleSummary("Поднятие уровня в одиночку", None)} if SLUG in slugs else {}

    monkeypatch.setattr(admin_api, "title_summaries", summaries)

    async def quote(slug_url: str, volume: str, number: str, branch_id: str, index: int):
        return None if index == 6 else f"Текст абзаца {index + 1} главы {number}"

    monkeypatch.setattr(admin_api, "paragraph_text", quote)
    (tmp_path / "attachments").mkdir()
    with _client(
        monkeypatch,
        tmp_path,
        ADMIN_PASSWORD=PASSWORD,
        COMMENT_ATTACHMENT_DIR=str(tmp_path / "attachments"),
    ) as test_client:
        yield test_client
    get_settings.cache_clear()


def _browser() -> TestClient:
    from app.main import app

    return TestClient(app)


async def _site_comment(email: str, body: str, **kw) -> tuple[int, int]:
    async with connection() as conn:
        user = (await get_user_by_email(conn, email)).id
        return user, await _comment(conn, user, body, **kw)


async def _entries(action: str) -> list[admin_audit.AuditEntry]:
    async with connection() as conn:
        return [e for e in await admin_audit.list_entries(conn) if e.action == action]


def _public_thread(browser: TestClient) -> dict:
    return browser.get(
        f"/titles/{SLUG}/chapters/1/5/comments", params={"paragraph_index": 0}
    ).json()


async def test_the_list_and_the_drawer(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass", nickname="aster")
    _, root = await _site_comment("alice@example.com", "Переводчик, а можно глоссарий?")
    _, reply = await _site_comment(
        "alice@example.com", "**Сам** отвечу", parent_comment_id=root, paragraph=0
    )
    client.post("/logout")
    _login(client)

    page = client.get("/admin/comments").text
    drawer = client.get("/admin/comments", params={"comment": reply, "q": "отвечу"}).text

    assert "Комментариев: 2" in page and "Поднятие уровня в одиночку" in page
    assert 'href="/admin/users?user=' in page
    assert f'href="/titles/{SLUG}/chapters/1/5"' in page
    assert "<strong>Сам</strong> отвечу" in drawer  # rendered like the site renders it
    assert "В ответ на комментарий aster" in drawer and "абзац 1" in drawer
    assert "Текст абзаца 1 главы 5" in drawer  # the paragraph it was left on
    _, unloadable = await _site_comment("alice@example.com", "Где-то", paragraph=6)
    without = client.get("/admin/comments", params={"comment": unloadable}).text
    assert "Текст абзаца сейчас недоступен" in without and "абзац 7" in without
    assert f'action="/admin/comments/{reply}/hide"' in drawer
    assert "Есть вариант мягче" in drawer and "Удалить навсегда" in drawer
    missing = client.get("/admin/comments", params={"comment": 999_999}).text
    assert "Комментарий не найден" in missing


async def test_hiding_with_undo(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass", nickname="aster")
    alice, comment = await _site_comment("alice@example.com", "Реклама канала")
    guest = _browser()
    _login(client)
    csrf = _csrf(client, "/admin/comments")
    back = f"/admin/comments?q=%D0%A0&comment={comment}"

    hidden = client.post(
        f"/admin/comments/{comment}/hide", data={"csrf": csrf, "back": back}, follow_redirects=False
    )
    toast = client.get(hidden.headers["location"]).text
    again = client.post(f"/admin/comments/{comment}/hide", data={"csrf": csrf})

    # The list again, the drawer closed - a modal drawer would cover the toast's «Отменить».
    assert hidden.status_code == 303 and hidden.headers["location"] == "/admin/comments?q=%D0%A0"
    assert 'id="admin-comment"' not in toast
    assert f'action="/admin/comments/{comment}/show"' in toast and "Отменить" in toast
    assert 'name="back" value="/admin/comments?q=%D0%A0"' in toast
    assert "уже скрыт" in again.text
    assert _public_thread(guest)["comments"] == []
    [entry] = await _entries(admin_audit.COMMENT_HIDE)
    assert (entry.entity, entry.entity_id, entry.entity_label) == (
        "comment",
        str(comment),
        f"#{comment} · aster",
    )
    assert entry.details["before"] == {"hidden_at": None} and entry.details["after"]["hidden_at"]
    assert (entry.details["author_id"], entry.details["text"]) == (alice, "Реклама канала")

    client.post(f"/admin/comments/{comment}/show", data={"csrf": csrf, "back": back})

    assert [c["id"] for c in _public_thread(guest)["comments"]] == [comment]
    [shown] = await _entries(admin_audit.COMMENT_SHOW)
    assert shown.details["after"] == {"hidden_at": None}


async def test_the_author_sees_the_hidden_comment_and_nobody_can_react(
    client: TestClient,
) -> None:
    register(client, "alice@example.com", "hunter2pass")
    _, comment = await _site_comment("alice@example.com", "Мой текст")
    bob = _browser()
    register(bob, "bob@example.com", "hunter2pass")
    async with connection() as conn:
        await hide_comment(conn, comment)

    [own] = _public_thread(client)["comments"]
    reaction = bob.post(
        f"/titles/{SLUG}/chapters/1/5/comments/{comment}/reactions", data={"value": "1"}
    )
    counts = bob.get(f"/titles/{SLUG}/chapters/1/5/comments/counts").json()

    assert (own["is_hidden"], own["body"]) == (True, "Мой текст")
    assert _public_thread(bob) == {"paragraph_index": 0, "count": 0, "comments": []}
    assert reaction.status_code == 404
    assert counts == {"counts": {}}
    assert client.get(f"/titles/{SLUG}/chapters/1/5/comments/counts").json() == {"counts": {"0": 1}}


async def test_deleting_for_good(client: TestClient, tmp_path: Path) -> None:
    register(client, "alice@example.com", "hunter2pass", nickname="aster")
    (tmp_path / "attachments" / "gif.mp4").write_bytes(b"x")
    _, comment = await _site_comment(
        "alice@example.com", "Оскорбление", attachment_path="gif.mp4", attachment_kind="gif"
    )
    _login(client)
    csrf = _csrf(client, "/admin/comments")
    back = f"/admin/comments?status=published&comment={comment}"

    unconfirmed = client.post(
        f"/admin/comments/{comment}/delete", data={"csrf": csrf, "back": back}
    )
    assert "нужно подтвердить" in unconfirmed.text
    assert (tmp_path / "attachments" / "gif.mp4").exists()

    deleted = client.post(
        f"/admin/comments/{comment}/delete",
        data={"csrf": csrf, "back": back, "confirm": "1"},
        follow_redirects=False,
    )

    assert deleted.headers["location"] == "/admin/comments?status=published"
    assert not (tmp_path / "attachments" / "gif.mp4").exists()
    async with connection() as conn:
        assert await comment_detail(conn, comment) is None
    [entry] = await _entries(admin_audit.COMMENT_DELETE)
    assert entry.details["before"] == {"status": "published"}
    assert entry.details["after"] == {"status": "removed"}
    assert (entry.details["text"], entry.details["attachment_removed"]) == ("Оскорбление", True)
    assert (
        client.post(
            f"/admin/comments/{comment}/delete", data={"csrf": csrf, "confirm": "1"}
        ).status_code
        == 404
    )


async def test_deleting_one_with_replies_keeps_the_drawer_and_the_thread(
    client: TestClient,
) -> None:
    register(client, "alice@example.com", "hunter2pass")
    _, root = await _site_comment("alice@example.com", "Корень")
    await _site_comment("alice@example.com", "Ответ", parent_comment_id=root)
    _login(client)
    csrf = _csrf(client, "/admin/comments")
    back = f"/admin/comments?comment={root}"

    deleted = client.post(
        f"/admin/comments/{root}/delete",
        data={"csrf": csrf, "back": back, "confirm": "1"},
        follow_redirects=False,
    )
    drawer = client.get(back).text
    again = client.post(f"/admin/comments/{root}/delete", data={"csrf": csrf, "confirm": "1"})

    assert deleted.headers["location"] == back
    assert "ответы под ним остаются" in drawer
    assert f'action="/admin/comments/{root}/delete"' not in drawer  # nothing left to delete
    assert "уже удалён" in again.text
    [entry] = await _entries(admin_audit.COMMENT_DELETE)
    assert entry.details["after"] == {"status": "deleted_kept_for_replies"}
    assert entry.details["replies"] == 1


@pytest.mark.parametrize("action", ["hide", "show", "delete"])
async def test_actions_need_csrf_and_the_admin(client: TestClient, action: str) -> None:
    register(client, "alice@example.com", "hunter2pass")
    _, comment = await _site_comment("alice@example.com", "Текст")
    if action == "show":
        async with connection() as conn:
            await hide_comment(conn, comment)
    data = {"csrf": "x", "confirm": "1", "back": "https://evil.example/"}

    as_site_user = client.post(
        f"/admin/comments/{comment}/{action}", data=data, follow_redirects=False
    )
    _login(client)
    forged = client.post(f"/admin/comments/{comment}/{action}", data=data, follow_redirects=False)
    csrf = _csrf(client, "/admin/comments")
    unknown = client.post(
        f"/admin/comments/999999/{action}", data={**data, "csrf": csrf}, follow_redirects=False
    )

    assert as_site_user.headers["location"] == "/admin/login"
    assert forged.status_code == 403 and unknown.status_code == 404
    async with connection() as conn:
        assert (await comment_detail(conn, comment)).comment.status == (
            "hidden" if action == "show" else "published"
        )
    assert await _entries(f"comment_{action}") == []
    done = client.post(
        f"/admin/comments/{comment}/{action}", data={**data, "csrf": csrf}, follow_redirects=False
    )
    assert done.headers["location"] == "/admin/comments"  # never the outside «back»
    assert re.search(r"comment_", str(await _entries(admin_audit.LOGIN))) is None
