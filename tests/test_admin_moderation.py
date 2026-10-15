"""PR 358: «Модерация» - the queue of reported comments and profiles (the most reported
first, then the longest waiting; tabs by type), the card (the comment with its paragraph
and the comment it answers, or the profile; reasons with counts; the author's history),
the three decisions with a confirmation, an action-log entry and «Отменить», and the
phone layout's list → card."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.comments import create_comment
from app.db.connection import connection
from tests.test_admin_auth import PASSWORD, Clock, _client, _login

NOW = datetime(2026, 3, 18, 12, tzinfo=UTC)
SLUG = "1--some-title"
WHERE = (SLUG, "1", "5", "")  # slug_url, volume, number, branch_id


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
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        _login(test_client)
        yield test_client
    get_settings.cache_clear()


async def _user(email: str, nickname: str | None = None, bio: str | None = None) -> int:
    async with connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO users (email, nickname, bio, password_hash, created_at, "
            "email_verified_at) VALUES (%s, %s, %s, 'x', %s, %s) RETURNING id",
            (email, nickname, bio, NOW.isoformat(), NOW.isoformat()),
        )
        return (await cursor.fetchone())["id"]


async def _comment(user: int, body: str, **kw) -> int:
    paragraph = kw.pop("paragraph", 0)
    async with connection() as conn:
        return (await create_comment(conn, user, *WHERE, paragraph, body, **kw)).id


async def _report(
    reporter: int,
    target_type: str,
    target_id: int,
    reason: str = "spam",
    details: str | None = None,
    minutes_ago: int = 0,
    status: str = "open",
) -> int:
    at = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    async with connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO reports (reporter_user_id, target_type, target_id, reason, details, "
            "status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (reporter, target_type, target_id, reason, details, status, at),
        )
        return (await cursor.fetchone())["id"]


async def _reporters(n: int) -> list[int]:
    return [await _user(f"reader{i}@example.com", f"reader{i}") for i in range(n)]


def _queue(html: str) -> list[str]:
    """The queue's objects in order, as their ``item`` keys."""
    return re.findall(r'class="wn-admin-mod__item" href="[^"]*item=([a-z]+-\d+)"', html)


# --- the queue ----------------------------------------------------------------------------


async def test_an_empty_queue(client: TestClient) -> None:
    html = client.get("/admin/moderation").text

    assert "Очередь пуста" in html
    assert "Все жалобы разобраны" in html
    assert _queue(html) == []
    assert 'aria-current="page">Все · 0</a>' in html


async def test_most_reported_first_then_the_longest_waiting(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    a, b, c = [await _comment(author, text) for text in ("раз", "два", "три")]
    readers = await _reporters(3)
    # b: three reports; a and c: one each, c's waiting longer.
    for reader in readers:
        await _report(reader, "comment", b)
    await _report(readers[0], "comment", a, minutes_ago=5)
    await _report(readers[0], "comment", c, minutes_ago=50)
    profile = await _user("loud@example.com", "loud")
    await _report(readers[1], "profile", profile, minutes_ago=10)
    await _report(readers[2], "profile", profile, minutes_ago=1)

    html = client.get("/admin/moderation").text

    assert _queue(html) == [f"comment-{b}", f"profile-{profile}", f"comment-{c}", f"comment-{a}"]
    assert "Все · 4" in html and "Комментарии · 3" in html and "Профили · 1" in html


async def test_closed_reports_dont_count(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    comment = await _comment(author, "текст")
    [reader] = await _reporters(1)
    await _report(reader, "comment", comment, status="dismissed")

    assert _queue(client.get("/admin/moderation").text) == []


async def test_tabs_filter_by_type(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    comment = await _comment(author, "текст")
    [reader] = await _reporters(1)
    await _report(reader, "comment", comment)
    await _report(reader, "profile", author)

    profiles = client.get("/admin/moderation?type=profile").text
    comments = client.get("/admin/moderation?type=comment").text

    assert _queue(profiles) == [f"profile-{author}"]
    assert 'aria-current="page">Профили · 1</a>' in profiles
    assert _queue(comments) == [f"comment-{comment}"]
    # The tab is kept in the queue's links.
    assert f'href="/admin/moderation?type=comment&amp;item=comment-{comment}"' in comments


async def test_a_queue_row(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    comment = await _comment(author, "Главы раньше всех — в моём канале " + "x" * 300)
    readers = await _reporters(5)
    for i, reader in enumerate(readers):
        await _report(reader, "comment", comment, reason="abuse" if i == 0 else "spam")

    html = client.get("/admin/moderation").text

    row = html[html.index('class="wn-admin-mod__item"') :]
    row = row[: row.index("</a>")]
    assert "Комментарий" in row and "aster" in row
    assert "wn-admin-mod__count--many" in row and ">5</span>" in row
    assert "Главы раньше всех — в моём канале" in row and "…" in row
    assert "Спам или реклама" in row  # the most common reason


# --- the card -----------------------------------------------------------------------------


def _card(html: str) -> str:
    start = html.index('class="wn-admin-panel wn-admin-mod__card"')
    return html[start : html.index("</article>", start)]


async def test_the_top_of_the_queue_opens_by_default(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    quiet, loud = await _comment(author, "тихий"), await _comment(author, "громкий")
    readers = await _reporters(2)
    await _report(readers[0], "comment", quiet)
    for reader in readers:
        await _report(reader, "comment", loud)

    html = client.get("/admin/moderation").text

    assert "громкий" in _card(html)
    assert f'item=comment-{loud}" aria-current="true"' in html
    assert "wn-admin-mod--detail" not in html  # nothing asked - the phone shows the list


async def test_a_comment_card(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    other = await _user("other@example.com", "rook")
    parent = await _comment(other, "Кто читал дальше?", paragraph=2)
    reply = await _comment(author, "**Спойлер**: он выживет", paragraph=2, parent_comment_id=parent)
    readers = await _reporters(4)
    for reader, reason in zip(readers, ["spoiler", "spoiler", "spoiler", "abuse"], strict=True):
        await _report(reader, "comment", reply, reason=reason)
    await _report(readers[0], "comment", parent, status="actioned")  # an earlier, decided one
    await _report(readers[1], "profile", author, status="dismissed")
    async with connection() as conn:
        await conn.execute(
            "UPDATE reports SET details = 'Раскрыл концовку' WHERE target_id = %s "
            "AND reporter_user_id = %s",
            (reply, readers[1]),
        )

    card = _card(client.get(f"/admin/moderation?item=comment-{reply}").text)

    assert "Комментарий" in card and "4 жалобы" in card
    assert "Поднятие уровня в одиночку · Том 1 · Глава 5 · абзац 3" in card
    assert "Текст абзаца 3 главы 5" in card
    assert "В ответ на комментарий rook" in card and "Кто читал дальше?" in card
    assert "<strong>Спойлер</strong>: он выживет" in card
    # Reasons, the most common first, with their counts.
    assert re.findall(r"<span>([^<]+)</span><b>(\d+)</b>", card) == [
        ("Спойлер без пометки", "3"),
        ("Оскорбления", "1"),
    ]
    assert 'style="width: 75%"' in card
    # The reporters' own words, with who wrote them - for the admin only.
    assert "Раскрыл концовку" in card and "reader1" in card
    # The author's history: the decided report on their profile counts, the one on
    # someone else's comment doesn't.
    history = card[card.index("История автора") :]
    assert "Комментариев</dt><dd>1" in history
    assert "Прошлые жалобы</dt><dd>1 · подтверждено 0" in history
    assert "Блокировок</dt><dd>0" in history


async def test_a_profile_card(client: TestClient) -> None:
    target = await _user("casino@example.com", "senpai404", bio="Бонус 300% по промокоду RANOBE")
    [reader] = await _reporters(1)
    await _report(reader, "profile", target)

    html = client.get(f"/admin/moderation?item=profile-{target}").text
    card = _card(html)

    assert "Профиль" in card and "1 жалоба" in card
    assert f'href="/profile/{target}"' in card
    assert "Бонус 300% по промокоду RANOBE" in card
    assert "Никнейм</dt><dd>senpai404" in card and "Аватар</dt><dd>нет" in card
    assert "wn-admin-mod--detail" in html  # asked for - the phone shows the card


async def test_a_blocked_author_is_marked(client: TestClient) -> None:
    from app.db.bans import ban_user

    target = await _user("bad@example.com", "bad")
    [reader] = await _reporters(1)
    await _report(reader, "profile", target)
    async with connection() as conn:
        await ban_user(conn, target, term="forever")

    card = _card(client.get(f"/admin/moderation?item=profile-{target}").text)

    assert "заблокирован бессрочно" in card
    assert "Блокировок</dt><dd>1" in card


async def test_a_comment_gone_for_good_can_only_be_dismissed(client: TestClient) -> None:
    [reader] = await _reporters(1)
    await _report(reader, "comment", 999)

    card = _card(client.get("/admin/moderation?item=comment-999").text)

    assert "Комментария больше нет" in card and "Жалобы можно только закрыть" in card


async def test_an_object_without_open_reports(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    comment, other = await _comment(author, "текст"), await _comment(author, "другой")
    [reader] = await _reporters(1)
    await _report(reader, "comment", other)

    html = client.get(f"/admin/moderation?item=comment-{comment}").text

    assert "Открытых жалоб на этот объект нет" in html
    assert _queue(html) == [f"comment-{other}"]


@pytest.mark.parametrize("item", ["title-1", "comment-x", "comment-", "comment-99999999999"])
async def test_a_bad_item_is_ignored(client: TestClient, item: str) -> None:
    assert client.get(f"/admin/moderation?item={item}").status_code == 200


# --- decisions ----------------------------------------------------------------------------


def _token(client: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/admin/moderation").text).group(1)


def _post(client: TestClient, path: str, **fields: str):
    data = {"csrf": _token(client), "back": "/admin/moderation", **fields}
    return client.post(path, data=data, follow_redirects=False)


async def _statuses(target_type: str, target_id: int) -> list[tuple[str, str | None]]:
    async with connection() as conn:
        cursor = await conn.execute(
            "SELECT status, resolution, resolved_at FROM reports "
            "WHERE target_type = %s AND target_id = %s ORDER BY id",
            (target_type, target_id),
        )
        rows = await cursor.fetchall()
    assert all((row["resolved_at"] is None) == (row["status"] == "open") for row in rows)
    return [(row["status"], row["resolution"]) for row in rows]


async def _log() -> list[dict]:
    async with connection() as conn:
        cursor = await conn.execute(
            "SELECT id, action, entity, entity_id, entity_label, details FROM admin_audit_log "
            "WHERE action LIKE 'reports_%%' ORDER BY id"
        )
        return await cursor.fetchall()


def _undo_entry(html: str) -> str:
    form = re.search(r'action="/admin/moderation/undo">(.*?)</form>', html, re.S)
    assert form, "no «Отменить» in the toast"
    return re.search(r'name="entry" value="(\d+)"', form.group(1)).group(1)


async def _reported_comment(n: int = 2, body: str = "Главы раньше всех — в моём канале"):
    author = await _user("author@example.com", "aster")
    comment = await _comment(author, body)
    for reader in await _reporters(n):
        await _report(reader, "comment", comment)
    return author, comment


async def _hidden_at(comment: int) -> str | None:
    async with connection() as conn:
        cursor = await conn.execute("SELECT hidden_at FROM comments WHERE id = %s", (comment,))
        return (await cursor.fetchone())["hidden_at"]


async def test_approve_closes_the_reports_and_leaves_the_comment(client: TestClient) -> None:
    _, comment = await _reported_comment()

    response = _post(client, f"/admin/moderation/comment/{comment}/dismiss")

    assert response.status_code == 303 and response.headers["location"] == "/admin/moderation"
    assert await _statuses("comment", comment) == [("dismissed", "dismissed")] * 2
    assert await _hidden_at(comment) is None
    [entry] = await _log()
    assert (entry["action"], entry["entity"], entry["entity_id"]) == (
        "reports_dismiss",
        "comment",
        str(comment),
    )
    assert entry["details"]["reports"] == 2 and len(entry["details"]["report_ids"]) == 2
    assert entry["details"]["text"] == "Главы раньше всех — в моём канале"
    html = client.get("/admin/moderation").text
    assert "Одобрено: жалобы закрыты, контент остаётся" in html
    assert _undo_entry(html) == str(entry["id"])
    assert "Очередь пуста" in html


async def test_hide_a_comment(client: TestClient) -> None:
    _, comment = await _reported_comment()

    _post(client, f"/admin/moderation/comment/{comment}/hide")

    assert await _hidden_at(comment) is not None
    assert await _statuses("comment", comment) == [("actioned", "comment_hidden")] * 2
    [entry] = await _log()
    assert entry["action"] == "reports_hide_comment"
    assert entry["details"]["before"] == {"hidden_at": None}
    assert "Комментарий скрыт от читателей" in client.get("/admin/moderation").text


async def test_hide_a_comment_hidden_already(client: TestClient) -> None:
    from app.db.comments import hide_comment

    _, comment = await _reported_comment()
    async with connection() as conn:
        await hide_comment(conn, comment)

    _post(client, f"/admin/moderation/comment/{comment}/hide")

    assert await _statuses("comment", comment) == [("actioned", "comment_hidden")] * 2
    [entry] = await _log()
    assert entry["details"]["before"] == {} and entry["details"]["after"] == {}
    assert "уже был скрыт" in client.get("/admin/moderation").text


async def test_clear_a_profile(client: TestClient) -> None:
    target = await _user("casino@example.com", "senpai404", bio="Бонус по промокоду")
    async with connection() as conn:
        await conn.execute("UPDATE users SET avatar_path = 'a.png' WHERE id = %s", (target,))
    [reader] = await _reporters(1)
    await _report(reader, "profile", target)

    _post(client, f"/admin/moderation/profile/{target}/hide", bio="1")

    async with connection() as conn:
        cursor = await conn.execute("SELECT bio, avatar_path FROM users WHERE id = %s", (target,))
        assert await cursor.fetchone() == {"bio": None, "avatar_path": "a.png"}
    assert await _statuses("profile", target) == [("actioned", "profile_cleared")]
    [entry] = await _log()
    assert entry["action"] == "reports_clear_profile"
    assert entry["details"]["before"] == {"bio": "Бонус по промокоду"}
    assert "«о себе» очищено" in client.get("/admin/moderation").text


async def test_clearing_nothing_changes_nothing(client: TestClient) -> None:
    target = await _user("casino@example.com", "senpai404", bio="Бонус")
    [reader] = await _reporters(1)
    await _report(reader, "profile", target)

    _post(client, f"/admin/moderation/profile/{target}/hide")
    first = client.get("/admin/moderation").text
    _post(client, f"/admin/moderation/profile/{target}/hide", avatar="1")  # there's none

    assert "отметьте «о себе» или аватар" in first
    assert "в профиле нечего убирать" in client.get("/admin/moderation").text
    assert await _statuses("profile", target) == [("open", None)]
    assert await _log() == []


async def test_ban_the_author(client: TestClient) -> None:
    author, comment = await _reported_comment()

    _post(client, f"/admin/moderation/comment/{comment}/ban", term="7d", reason="спам")

    async with connection() as conn:
        cursor = await conn.execute(
            "SELECT id, reason, expires_at, lifted_at FROM user_bans WHERE user_id = %s", (author,)
        )
        [ban] = await cursor.fetchall()
    assert ban["reason"] == "спам" and ban["expires_at"] and ban["lifted_at"] is None
    assert await _statuses("comment", comment) == [("actioned", "author_banned")] * 2
    [entry] = await _log()
    assert entry["action"] == "reports_ban_author"
    assert (entry["details"]["ban_id"], entry["details"]["author_id"]) == (ban["id"], author)
    assert await _hidden_at(comment) is None  # the comment itself is left as it is
    assert "«aster» заблокирован (7 дней)" in client.get("/admin/moderation").text


async def test_ban_refused(client: TestClient) -> None:
    from app.db.bans import ban_user

    author, comment = await _reported_comment()
    _post(client, f"/admin/moderation/comment/{comment}/ban", term="1y")
    no_term = client.get("/admin/moderation").text
    async with connection() as conn:
        await ban_user(conn, author, term="1d")
    _post(client, f"/admin/moderation/comment/{comment}/ban", term="7d")

    assert "выберите срок" in no_term
    assert "«aster» уже заблокирован" in client.get("/admin/moderation").text
    assert await _statuses("comment", comment) == [("open", None)] * 2
    assert await _log() == []


async def test_an_object_gone_can_only_be_dismissed(client: TestClient) -> None:
    [reader] = await _reporters(1)
    await _report(reader, "comment", 999)

    _post(client, "/admin/moderation/comment/999/hide")
    refused = client.get("/admin/moderation").text
    _post(client, "/admin/moderation/comment/999/ban", term="7d")
    _post(client, "/admin/moderation/comment/999/dismiss")

    assert "Объекта больше нет" in refused
    assert await _statuses("comment", 999) == [("dismissed", "dismissed")]


async def test_a_decision_on_nothing_open(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    comment = await _comment(author, "текст")

    _post(client, f"/admin/moderation/comment/{comment}/hide")

    assert "Жалоб на этот объект уже нет" in client.get("/admin/moderation").text
    assert await _hidden_at(comment) is None  # put back
    assert await _log() == []


async def test_decisions_need_the_csrf_token_and_stay_on_the_screen(client: TestClient) -> None:
    _, comment = await _reported_comment()

    refused = client.post(
        f"/admin/moderation/comment/{comment}/dismiss", data={}, follow_redirects=False
    )
    elsewhere = _post(client, f"/admin/moderation/comment/{comment}/dismiss", back="//evil.example")

    assert refused.status_code == 403
    assert elsewhere.headers["location"] == "/admin/moderation"


async def test_the_dialogs(client: TestClient) -> None:
    _, comment = await _reported_comment()

    html = client.get(f"/admin/moderation?item=comment-{comment}").text

    for dialog in ("admin-mod-dismiss", "admin-mod-hide", "admin-mod-ban"):
        assert f'data-kit-open="{dialog}"' in html and f'id="{dialog}"' in html
    assert f'action="/admin/moderation/comment/{comment}/ban"' in html
    assert '<option value="7d" selected>7 дней</option>' in html
    assert "admin-mod-clear" not in html  # a comment isn't cleared, it's hidden


# --- «Отменить» ---------------------------------------------------------------------------


def _undo(client: TestClient, item: str, back: str = "/admin/moderation"):
    entry = _undo_entry(client.get("/admin/moderation").text)
    return _post(client, "/admin/moderation/undo", entry=entry, item=item, back=back)


async def test_undo_an_approval(client: TestClient) -> None:
    _, comment = await _reported_comment()
    _post(client, f"/admin/moderation/comment/{comment}/dismiss")

    response = _undo(client, f"comment-{comment}", back="/admin/moderation?type=comment")

    assert response.headers["location"] == (
        f"/admin/moderation?type=comment&item=comment-{comment}"
    )
    assert await _statuses("comment", comment) == [("open", None)] * 2
    decision, undo = await _log()
    assert undo["action"] == "reports_undo"
    assert undo["details"]["undoes"] == decision["id"]
    assert undo["details"]["reopened"] == decision["details"]["report_ids"]
    html = client.get(response.headers["location"]).text
    assert "Решение отменено — жалобы вернулись в очередь" in html
    assert _queue(html) == [f"comment-{comment}"]


async def test_undo_a_hide(client: TestClient) -> None:
    _, comment = await _reported_comment()
    _post(client, f"/admin/moderation/comment/{comment}/hide")

    _undo(client, f"comment-{comment}")

    assert await _hidden_at(comment) is None
    assert await _statuses("comment", comment) == [("open", None)] * 2
    assert (await _log())[-1]["details"]["shown"] is True


async def test_undo_closing_a_comment_hidden_already_keeps_it_hidden(client: TestClient) -> None:
    from app.db.comments import hide_comment

    _, comment = await _reported_comment()
    async with connection() as conn:
        await hide_comment(conn, comment)
    _post(client, f"/admin/moderation/comment/{comment}/hide")

    _undo(client, f"comment-{comment}")

    assert await _hidden_at(comment) is not None  # it wasn't this decision that hid it
    assert await _statuses("comment", comment) == [("open", None)] * 2


async def test_undo_a_profile_cleanup_keeps_what_was_filled_in_since(client: TestClient) -> None:
    target = await _user("casino@example.com", "senpai404", bio="Бонус по промокоду")
    async with connection() as conn:
        await conn.execute("UPDATE users SET avatar_path = 'a.png' WHERE id = %s", (target,))
    [reader] = await _reporters(1)
    await _report(reader, "profile", target)
    _post(client, f"/admin/moderation/profile/{target}/hide", bio="1", avatar="1")
    async with connection() as conn:
        await conn.execute("UPDATE users SET bio = 'Новое о себе' WHERE id = %s", (target,))

    _undo(client, f"profile-{target}")

    async with connection() as conn:
        cursor = await conn.execute("SELECT bio, avatar_path FROM users WHERE id = %s", (target,))
        assert await cursor.fetchone() == {"bio": "Новое о себе", "avatar_path": "a.png"}
    assert (await _log())[-1]["details"]["restored"] == ["avatar_path"]


async def test_undo_a_ban(client: TestClient) -> None:
    from app.db.bans import active_ban

    author, comment = await _reported_comment()
    _post(client, f"/admin/moderation/comment/{comment}/ban", term="forever")

    _undo(client, f"comment-{comment}")

    async with connection() as conn:
        assert await active_ban(conn, author) is None
    assert await _statuses("comment", comment) == [("open", None)] * 2
    assert (await _log())[-1]["details"]["unbanned"] is True


async def test_undo_leaves_a_newer_block_alone(client: TestClient) -> None:
    from app.db.bans import active_ban, ban_user, lift_ban

    author, comment = await _reported_comment()
    _post(client, f"/admin/moderation/comment/{comment}/ban", term="7d")
    async with connection() as conn:
        await lift_ban(conn, author)
        newer = await ban_user(conn, author, term="30d")

    _undo(client, f"comment-{comment}")

    async with connection() as conn:
        assert (await active_ban(conn, author)).id == newer.id


async def test_undo_only_once(client: TestClient) -> None:
    _, comment = await _reported_comment()
    _post(client, f"/admin/moderation/comment/{comment}/dismiss")
    entry = _undo_entry(client.get("/admin/moderation").text)
    _post(client, "/admin/moderation/undo", entry=entry)
    _post(client, f"/admin/moderation/comment/{comment}/dismiss")  # decided again

    _post(client, "/admin/moderation/undo", entry=entry)

    assert "Это решение уже отменено" in client.get("/admin/moderation").text
    assert await _statuses("comment", comment) == [("dismissed", "dismissed")] * 2


async def test_undo_skips_a_report_filed_again_meanwhile(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    comment = await _comment(author, "текст")
    [reader] = await _reporters(1)
    await _report(reader, "comment", comment)
    _post(client, f"/admin/moderation/comment/{comment}/dismiss")
    entry = _undo_entry(client.get("/admin/moderation").text)
    await _report(reader, "comment", comment, reason="abuse")  # the same reader, again

    _post(client, "/admin/moderation/undo", entry=entry)

    assert await _statuses("comment", comment) == [("dismissed", "dismissed"), ("open", None)]


@pytest.mark.parametrize("entry", ["999999", "1"])
async def test_undo_of_something_else_does_nothing(client: TestClient, entry: str) -> None:
    # Entry 1 is the admin's login - not a moderation decision.
    _post(client, "/admin/moderation/undo", entry=entry)

    assert "Отменять нечего" in client.get("/admin/moderation").text


@pytest.mark.parametrize("action", ["dismiss", "hide", "ban"])
async def test_an_unknown_target_type_is_a_404(client: TestClient, action: str) -> None:
    # Titles can't be reported (their text is ranobelib.me's), so there's no such queue.
    assert _post(client, f"/admin/moderation/title/1/{action}").status_code == 404


# --- the phone layout ---------------------------------------------------------------------


def test_on_a_phone_the_list_and_the_card_take_turns() -> None:
    css = (Path(__file__).resolve().parents[1] / "app/static/css/app.css").read_text(
        encoding="utf-8"
    )
    phone = css[css.index("/* «Модерация» on a phone") :]
    phone = phone[: phone.index("\n}\n", phone.index("@media")) + 3]

    assert "@media (max-width: 767px)" in phone
    assert "grid-template-columns: minmax(0, 1fr);" in phone
    assert (
        ".wn-admin-mod:not(.wn-admin-mod--detail) .wn-admin-mod__card,\n"
        "  .wn-admin-mod--detail .wn-admin-mod__queue {\n    display: none;"
    ) in phone.replace("\r\n", "\n")
    assert ".wn-admin-mod__back {\n    display: inline-flex;" in phone.replace("\r\n", "\n")
