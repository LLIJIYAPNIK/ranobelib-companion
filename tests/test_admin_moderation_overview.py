"""PR 359: the moderation queue outside «Модерация» - the sidebar's badge on every admin page,
«Жалобы ждут модерации» in «Требует внимания» and the overview's «Ждут модерации» block,
including the empty queue."""

import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.admin_overview import attention_items
from app.config import get_settings
from app.db.admin_reports import Queue
from app.db.admin_stats import AttentionCounts
from tests.test_admin_auth import PASSWORD, Clock, _client, _login
from tests.test_admin_moderation import _comment, _post, _report, _reporters, _user

QUIET = AttentionCounts(0, 0, 0, 0, 0)
BADGE = re.compile(
    r'<span class="wn-admin-side__badge"><span class="wn-admin-sr">в очереди: </span>([^<]+)</span>'
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import app.auth.admin as admin_auth

    monkeypatch.setattr(admin_auth, "_now", Clock())
    monkeypatch.setattr(admin_auth, "_failures", {})
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        _login(test_client)
        yield test_client
    get_settings.cache_clear()


def _badge(html: str) -> str | None:
    match = BADGE.search(html)
    return match.group(1) if match else None


def _block(html: str) -> str:
    start = html.index('aria-labelledby="ov-moderation-title"')
    return html[start : html.index("</section>", start)]


async def _queue_of_four() -> list[str]:
    """Four reported objects; returns their texts in queue order."""
    author = await _user("author@example.com", "aster")
    readers = await _reporters(4)
    texts = ["четыре жалобы", "три жалобы", "две жалобы"]
    for count, text in zip((4, 3, 2), texts, strict=True):
        comment = await _comment(author, text)
        for reader in readers[:count]:
            await _report(reader, "comment", comment)
    target = await _user("loud@example.com", "loud", bio="одна жалоба")
    await _report(readers[0], "profile", target)
    return [*texts, "одна жалоба"]


async def test_an_empty_queue(client: TestClient) -> None:
    html = client.get("/admin").text

    assert _badge(html) is None
    block = _block(html)
    assert "Очередь пуста" in block and 'href="/admin/moderation"' in block
    assert "Жалобы ждут модерации" not in html


async def test_the_badge_on_every_admin_page(client: TestClient) -> None:
    await _queue_of_four()

    for path in ("/admin", "/admin/users", "/admin/comments", "/admin/moderation", "/admin/audit"):
        html = client.get(path).text
        assert _badge(html) == "4", path
        assert 'title="Модерация · в очереди: 4"' in html


async def test_the_badge_follows_a_decision_at_once(client: TestClient) -> None:
    author = await _user("author@example.com", "aster")
    comment = await _comment(author, "текст")
    [reader] = await _reporters(1)
    await _report(reader, "comment", comment)
    assert _badge(client.get("/admin").text) == "1"

    _post(client, f"/admin/moderation/comment/{comment}/dismiss")

    html = client.get("/admin").text
    assert _badge(html) is None
    assert "Очередь пуста" in _block(html)  # no one-minute cache on the queue


async def test_a_long_queue_shows_99_plus(client: TestClient) -> None:
    [reader] = await _reporters(1)
    for comment_id in range(1, 101):
        await _report(reader, "comment", comment_id)

    assert _badge(client.get("/admin/users").text) == "99+"


async def test_a_failed_count_costs_only_the_badge(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.admin as admin_api

    async def broken(conn):
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(admin_api, "waiting_objects", broken)

    response = client.get("/admin/users")

    assert response.status_code == 200 and _badge(response.text) is None


async def test_the_overview_block(client: TestClient) -> None:
    texts = await _queue_of_four()

    block = _block(client.get("/admin").text)

    assert "4 объекта в очереди" in block
    rows = re.findall(r'class="wn-admin-ov-mod__text" href="([^"]+)">([^<]+)</a>', block)
    assert [text for _, text in rows] == texts[:3]
    assert all(href.startswith("/admin/moderation?item=comment-") for href, _ in rows)
    assert "Комментарий · aster · Спам или реклама · 4 жалобы" in block


async def test_the_attention_item(client: TestClient) -> None:
    await _queue_of_four()

    html = client.get("/admin").text
    alerts = html[html.index('class="wn-admin-alerts"') :]

    assert "Жалобы ждут модерации" in alerts
    assert "В очереди 4 объекта, на них 10 жалоб." in alerts
    assert '<a class="wn-admin-alerts__link" href="/admin/moderation">Открыть очередь</a>' in alerts


def test_attention_items_for_the_queue() -> None:
    assert attention_items(QUIET, False, waiting=Queue([], 0, {})) == []
    [item] = attention_items(QUIET, False, waiting=Queue([], 1, {"comment": 1}, reports=1))
    assert (item.tone, item.title, item.text) == (
        "warning",
        "Жалобы ждут модерации",
        "В очереди 1 объект, на них 1 жалоба.",
    )
    assert (item.href, item.link) == ("/admin/moderation", "Открыть очередь")
    # Without the queue (its block failed) there's simply no item.
    assert attention_items(QUIET, False) == []
