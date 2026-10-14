"""PR 345: «Обзор» - the KPIs, the activity chart, popular titles and «Требует внимания»
on an empty database and on one day of data, the switches, each block's error state, and
the SDK outcome log the ranobelib.me warnings read."""

import re
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from ranobelib import AccessBlockedError, RateLimitError, TitleNotFoundError

import app.api.admin as admin_api
from app.admin_kit import format_number
from app.admin_overview import (
    BLOCKED_PER_HOUR,
    FAILED_DOWNLOADS_MIN,
    RATE_LIMITED_PER_HOUR,
    UNREACHABLE_MIN,
    UNVERIFIED_MIN,
    attention_items,
    format_duration,
)
from app.config import get_settings
from app.db.admin_stats import AttentionCounts, attention_counts, top_titles
from app.db.connection import connection
from app.db.migrate import run_migrations
from app.services import sdk_outcomes
from app.services.titles import TitleSummary
from tests.db_reset import fresh_connection
from tests.test_admin_auth import PASSWORD, Clock, _client, _login


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    summaries: dict[str, TitleSummary] = {}

    async def fake_summaries(slugs: set[str]) -> dict[str, TitleSummary]:
        return {slug: summaries[slug] for slug in slugs if slug in summaries}

    monkeypatch.setattr(admin_api, "title_summaries", fake_summaries)
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        test_client.summaries = summaries  # type: ignore[attr-defined]
        assert _login(test_client).status_code == 303
        yield test_client
    get_settings.cache_clear()


def _kpi(html: str, label: str) -> str:
    match = re.search(
        rf"<span>{label}</span></p>\s*<p class=\"wn-admin-kpi__value\">([^<]+)</p>", html
    )
    assert match, label
    return match.group(1)


async def _seed_one_day() -> None:
    """Two readers who registered today, three chapters of two titles, ten minutes of
    reading, one failed download, one comment - all within the last hour."""
    now = datetime.now(UTC) - timedelta(minutes=5)
    async with connection() as conn:
        ids = []
        for name in ("alice", "bob"):
            cursor = await conn.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (%s, 'x', %s) "
                "RETURNING id",
                (f"{name}@example.com", now.isoformat()),
            )
            ids.append((await cursor.fetchone())["id"])
        alice, bob = ids
        for user, slug in ((alice, "1--first"), (bob, "1--first"), (alice, "2--second")):
            await conn.execute(
                "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, "
                "created_at) VALUES (%s, 'chapter_read', %s, '1', '1', %s)",
                (user, slug, now.isoformat()),
            )
        await conn.execute(
            "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
            "VALUES (%s, 'heartbeat', '1--first', 600, %s)",
            (alice, now.isoformat()),
        )
        await conn.execute(
            "INSERT INTO download_history (user_id, slug_url, fmt, status, error, finished_at) "
            "VALUES (%s, '2--second', 'epub', 'error', 'ranobelib timed out', %s)",
            (bob, now.isoformat()),
        )
        await conn.execute(
            "INSERT INTO comments (user_id, slug_url, volume, number, paragraph_index, body, "
            "created_at) VALUES (%s, '1--first', '1', '1', 0, 'hi', %s)",
            (alice, now.isoformat()),
        )


# --- the page -----------------------------------------------------------------------------


def test_an_empty_database(client: TestClient) -> None:
    response = client.get("/admin")
    html = response.text

    assert response.status_code == 200
    for label in ("Активные читатели", "Прочитано глав", "Регистрации", "Загрузки"):
        assert _kpi(html, label) == "0"
    assert _kpi(html, "Время чтения") == "0 мин"
    assert "Нет данных за 7 дней" in html  # no flat line at zero
    assert "wn-admin-chart__plot" not in html
    assert "никто ничего не читал" in html
    assert "Всё в порядке" in html
    assert "Пользователей пока нет" in html
    assert "Ошибок загрузок нет" in html
    assert "новое" not in html  # 0 against 0 isn't growth


async def test_one_day_of_data(client: TestClient) -> None:
    await _seed_one_day()
    client.summaries["1--first"] = TitleSummary("Первый тайтл", "https://cover.lib/1.jpg")  # type: ignore[attr-defined]

    html = client.get("/admin").text

    assert _kpi(html, "Активные читатели") == "2"
    assert _kpi(html, "Прочитано глав") == "3"
    assert _kpi(html, "Время чтения") == "10 мин"
    assert _kpi(html, "Регистрации") == "2"
    assert _kpi(html, "Загрузки") == "1"
    assert _kpi(html, "Комментарии") == "1"
    assert "новое" in html  # growth from an empty previous period - no percentage
    assert "+∞" not in html and "inf" not in html
    assert "wn-admin-chart__plot" in html
    # Popular titles: most readers first; the SDK name and cover, or the slug_url.
    popular = html.split("Популярные тайтлы", 1)[1].split("Требует внимания", 1)[0]
    assert popular.index("Первый тайтл") < popular.index("2--second")
    assert 'src="https://cover.lib/1.jpg"' in popular
    assert 'href="/titles/1--first"' in popular
    assert "alice@example.com" in html
    assert "ranobelib timed out" in html


def test_switches_keep_the_rest_of_the_state(client: TestClient) -> None:
    html = client.get("/admin", params={"period": "24h", "metric": "comments"}).text

    assert re.search(r'href="\?period=24h&amp;metric=comments"[^>]*aria-current="page"', html)
    assert 'href="?metric=comments"' in html  # 7d is the default - not in the URL
    assert re.search(
        r'href="\?period=24h"[^>]*>\s*<span class="wn-admin-ov__metric-label">'
        r"Активные читатели",
        html,
    )
    assert 'href="?period=24h&amp;metric=comments&amp;compare=0"' in html
    assert "За 24 часа" in html


async def test_the_comparison_can_be_turned_off(client: TestClient) -> None:
    await _seed_one_day()

    on = client.get("/admin").text
    off = client.get("/admin", params={"compare": "0"}).text

    assert "wn-admin-chart__line--previous" in on
    assert "wn-admin-chart__line--previous" not in off
    assert "wn-admin-toggle__track--on" in on and "wn-admin-toggle__track--on" not in off


def test_unknown_values_fall_back_to_the_defaults(client: TestClient) -> None:
    response = client.get("/admin", params={"period": "365d", "metric": "password_hash"})

    assert response.status_code == 200
    assert re.search(r'href="\?[^"]*"[^>]*aria-current="page"[^>]*>7 д<', response.text)
    assert "Активные читатели</span>" in response.text


def test_the_page_opens_with_the_database_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def down():  # noqa: ANN202
        raise psycopg.OperationalError("connection refused")
        yield

    monkeypatch.setattr(admin_api.db_connection, "connection", down)

    response = client.get("/admin")

    assert response.status_code == 200
    assert response.text.count("База данных недоступна") == 2  # the KPIs and the warning
    assert "connection refused" not in response.text


async def test_titles_keep_their_slug_when_ranobelib_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_one_day()

    async def unreachable(slugs: set[str]) -> dict[str, TitleSummary]:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(admin_api, "title_summaries", unreachable)

    html = client.get("/admin").text

    assert 'href="/titles/1--first">1--first</a>' in html


# --- «Требует внимания» -------------------------------------------------------------------

QUIET = AttentionCounts(0, 0, 0, 0, 0)


def _titles(items) -> list[str]:  # noqa: ANN001
    return [item.title for item in items]


def test_failed_downloads_need_a_count_and_a_share() -> None:
    def failed(n: int, total: int) -> list[str]:
        return _titles(attention_items(AttentionCounts(total, n, 0, 0, 0), False))

    assert failed(FAILED_DOWNLOADS_MIN - 1, FAILED_DOWNLOADS_MIN - 1) == []
    assert failed(FAILED_DOWNLOADS_MIN, 100) == []  # 3 of 100 is normal
    assert failed(FAILED_DOWNLOADS_MIN, 12) == ["Много неудачных загрузок"]


def test_unconfirmed_emails_must_be_many_and_growing() -> None:
    def unverified(week: int, before: int) -> list[str]:
        return _titles(attention_items(AttentionCounts(0, 0, week + 3, week, before), False))

    assert unverified(UNVERIFIED_MIN - 1, 0) == []
    assert unverified(UNVERIFIED_MIN, 0) == ["Растёт число неподтверждённых email"]
    assert unverified(UNVERIFIED_MIN, UNVERIFIED_MIN) == []  # many, but not more than before
    assert unverified(UNVERIFIED_MIN * 2, UNVERIFIED_MIN) == ["Растёт число неподтверждённых email"]


def test_ranobelib_warnings_come_from_the_last_hour_of_sdk_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(sdk_outcomes, "_now", lambda: now - timedelta(hours=2))
    for _ in range(10):  # long ago - doesn't count
        sdk_outcomes.record(sdk_outcomes.BLOCKED)
        sdk_outcomes.record(sdk_outcomes.RATE_LIMITED)
    monkeypatch.setattr(sdk_outcomes, "_now", lambda: now - timedelta(minutes=30))
    for _ in range(BLOCKED_PER_HOUR - 1):
        sdk_outcomes.record(sdk_outcomes.BLOCKED)
    for _ in range(RATE_LIMITED_PER_HOUR - 1):
        sdk_outcomes.record(sdk_outcomes.RATE_LIMITED)
    assert attention_items(QUIET, False, now) == []

    sdk_outcomes.record(sdk_outcomes.BLOCKED)
    sdk_outcomes.record(sdk_outcomes.RATE_LIMITED)
    items = attention_items(QUIET, False, now)
    assert _titles(items) == [
        "ranobelib.me блокирует наши запросы",
        "ranobelib.me ограничивает запросы",
    ]
    assert [item.tone for item in items] == ["danger", "warning"]


def test_unreachable_needs_failures_to_outweigh_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(sdk_outcomes, "_now", lambda: now - timedelta(minutes=5))
    for _ in range(UNREACHABLE_MIN):
        sdk_outcomes.record(sdk_outcomes.UNREACHABLE)
    for _ in range(UNREACHABLE_MIN + 1):
        sdk_outcomes.record(sdk_outcomes.OK)
    assert attention_items(QUIET, False, now) == []

    sdk_outcomes.record(sdk_outcomes.UNREACHABLE)
    assert _titles(attention_items(QUIET, False, now)) == ["ranobelib.me не отвечает"]


def test_the_database_comes_first() -> None:
    items = attention_items(AttentionCounts(10, 10, 0, 0, 0), True)

    assert _titles(items)[0] == "База данных недоступна"
    assert items[0].tone == "danger"


# --- the SDK outcome log ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "outcome"),
    [
        (None, sdk_outcomes.OK),
        (TitleNotFoundError("1--x"), sdk_outcomes.OK),  # ranobelib.me answered
        (RateLimitError(5), sdk_outcomes.RATE_LIMITED),
        (AccessBlockedError("https://ranobelib.me"), sdk_outcomes.BLOCKED),
        (httpx.ConnectError("refused"), sdk_outcomes.UNREACHABLE),
        (ValueError("not the SDK"), None),
    ],
)
async def test_watch_records_how_a_session_ended(
    raised: Exception | None, outcome: str | None
) -> None:
    try:
        async with sdk_outcomes.watch():
            if raised is not None:
                raise raised
    except Exception as exc:
        assert exc is raised  # passed through untouched

    expected = {outcome: 1} if outcome else {}
    assert dict(sdk_outcomes.counts_since(datetime.now(UTC) - timedelta(minutes=1))) == expected


async def test_every_sdk_client_goes_through_the_log(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.catalog as catalog_service
    import app.services.client as client_service

    class Refused:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get_info(self):  # noqa: ANN201
            raise RateLimitError()

    monkeypatch.setattr(client_service, "RanobeLib", Refused)
    monkeypatch.setattr(catalog_service, "Catalog", Refused)

    with pytest.raises(RateLimitError):
        async with client_service.open_client("1--x") as lib:
            await lib.get_info()
    async with catalog_service.get_catalog():
        pass

    counts = sdk_outcomes.counts_since(datetime.now(UTC) - timedelta(minutes=1))
    assert counts == {sdk_outcomes.RATE_LIMITED: 1, sdk_outcomes.OK: 1}


# --- queries ------------------------------------------------------------------------------


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection_ = await fresh_connection()
    await run_migrations(connection_)
    return connection_


async def _user(conn: psycopg.AsyncConnection, name: str, at: datetime, verified: bool) -> int:
    cursor = await conn.execute(
        "INSERT INTO users (email, password_hash, created_at, email_verified_at) "
        "VALUES (%s, 'x', %s, %s) RETURNING id",
        (f"{name}@example.com", at.isoformat(), at.isoformat() if verified else None),
    )
    return (await cursor.fetchone())["id"]


async def test_attention_counts_use_their_own_windows(conn: psycopg.AsyncConnection) -> None:
    now = datetime(2026, 3, 18, 12, tzinfo=UTC)
    alice = await _user(conn, "alice", now - timedelta(days=1), verified=True)
    await _user(conn, "bob", now - timedelta(days=2), verified=False)
    await _user(conn, "carol", now - timedelta(days=9), verified=False)
    await _user(conn, "dave", now - timedelta(days=20), verified=False)  # too old
    for hours, status in ((1, "error"), (5, "done"), (30, "error")):
        await conn.execute(
            "INSERT INTO download_history (user_id, slug_url, fmt, status, finished_at) "
            "VALUES (%s, 't', 'epub', %s, %s)",
            (alice, status, (now - timedelta(hours=hours)).isoformat()),
        )

    counts = await attention_counts(conn, now)

    assert counts == AttentionCounts(
        downloads_day=2,
        failed_downloads_day=1,
        registrations_week=2,
        unverified_week=1,
        unverified_previous_week=1,
    )


async def test_top_titles_by_unique_readers(conn: psycopg.AsyncConnection) -> None:
    now = datetime(2026, 3, 18, 12, tzinfo=UTC)
    users = [await _user(conn, f"u{i}", now - timedelta(days=100), True) for i in range(3)]

    async def read(user: int, slug: str, days_ago: float) -> None:
        await conn.execute(
            "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, created_at) "
            "VALUES (%s, 'chapter_read', %s, '1', '1', %s)",
            (user, slug, (now - timedelta(days=days_ago)).isoformat()),
        )

    for _ in range(5):  # one reader, many chapters
        await read(users[0], "a--binge", 1)
    for user in users[:2]:  # two readers
        await read(user, "b--shared", 1)
    await read(users[2], "c--tie", 1)
    await read(users[1], "c--tie", 1)
    await read(users[0], "b--shared", 10)  # previous period
    await read(users[0], "d--old", 10)  # only read before - not in the top

    top = await top_titles(conn, "7d", "UTC", now=now)

    assert [(t.slug_url, t.readers, t.chapters, t.previous_readers) for t in top] == [
        ("b--shared", 2, 2, 1),
        ("c--tie", 2, 2, 0),
        ("a--binge", 1, 5, 0),
    ]
    assert top[0].comparison.percent == 100.0
    assert top[1].comparison.percent is None
    assert len(await top_titles(conn, "7d", "UTC", limit=1, now=now)) == 1


def test_reading_time_reads_as_hours_and_minutes() -> None:
    assert format_duration(0) == "0 мин"
    assert format_duration(20) == "< 1 мин"
    assert format_duration(45 * 60) == "45 мин"
    assert format_duration(3 * 3600) == "3 ч"
    assert format_duration(3 * 3600 + 20 * 60) == "3 ч 20 мин"
    assert format_duration(1500 * 3600) == f"{format_number(1500)} ч"
