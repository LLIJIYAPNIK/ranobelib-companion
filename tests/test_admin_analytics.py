"""PR 346: «Аналитика» - the titles list and a title's retention by chapter. Chapters
always follow the SDK's table of contents (gaps in numbering, odd order, several
volumes), a title with one chapter or no readers doesn't break anything, «Дочитали» and
the hints are computed only from what's there."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Volume

import app.api.admin as admin_api
from app.admin_analytics import (
    DROP_MIN_READERS,
    PAUSE_DAYS,
    SHARP_DROP,
    biggest_drops,
    finished,
    hints,
    retention,
)
from app.config import get_settings
from app.db.admin_analytics import chapter_reach, title_rows, title_totals
from app.db.connection import connection
from app.db.migrate import run_migrations
from app.services.titles import TitleDetails, TitleSummary
from tests.db_reset import fresh_connection
from tests.test_admin_auth import PASSWORD, Clock, _client, _login

NOW = datetime(2026, 3, 18, 12, tzinfo=UTC)
RELEASED = datetime(2025, 1, 1, tzinfo=UTC)


def _toc(*chapters: tuple[str, str] | tuple[str, str, int]) -> list[Volume]:
    """Volumes in the given order; a chapter's optional third item is its release day."""
    volumes: dict[str, list[Chapter]] = {}
    for item in chapters:
        volume, number = item[0], item[1]
        branches = []
        if len(item) == 3:
            branches = [
                {
                    "id": 1,
                    "created_at": (RELEASED + timedelta(days=item[2])).isoformat(),
                    "user": {"id": 1, "username": "tr"},
                }
            ]
        volumes.setdefault(volume, []).append(
            Chapter.model_validate(
                {"id": len(volumes), "volume": volume, "number": number, "branches": branches}
            )
        )
    return [Volume(number=number, chapters=items) for number, items in volumes.items()]


# --- retention, on the SDK's order --------------------------------------------------------


def test_a_title_with_one_chapter() -> None:
    points = retention(_toc(("1", "1")), 4, {("1", "1"): 4})

    assert [(p.label, p.reach, p.drop) for p in points] == [("Гл. 1", 100.0, None)]
    assert biggest_drops(points) == []
    assert hints(_toc(("1", "1")), points) == []


def test_chapters_follow_the_table_of_contents_not_their_numbers() -> None:
    # Gaps (3, 4 missing), an extra chapter (5.5) and an order the SDK chose (10 before 9).
    toc = _toc(("1", "1"), ("1", "2"), ("1", "5"), ("1", "5.5"), ("2", "10"), ("2", "9"))
    reached = {("1", "1"): 10, ("1", "2"): 8, ("1", "5"): 8, ("1", "5.5"): 4, ("2", "9"): 2}

    points = retention(toc, 10, reached)

    assert [p.label for p in points] == [
        "Т. 1, гл. 1", "Т. 1, гл. 2", "Т. 1, гл. 5", "Т. 1, гл. 5.5", "Т. 2, гл. 10", "Т. 2, гл. 9"
    ]  # fmt: skip
    assert [p.readers for p in points] == [10, 8, 8, 4, 0, 2]
    assert [p.drop for p in points] == [None, 20.0, 0.0, 50.0, 100.0, None]  # nobody before 9


def test_a_title_nobody_read() -> None:
    points = retention(_toc(("1", "1"), ("1", "2")), 0, {})

    assert [(p.reach, p.drop) for p in points] == [(0.0, None), (0.0, None)]
    assert biggest_drops(points) == []


def test_a_drop_needs_enough_readers_before_it() -> None:
    few = DROP_MIN_READERS - 1
    toc = _toc(("1", "1"), ("1", "2"), ("1", "3"))

    small = retention(toc, few, {("1", "1"): few, ("1", "2"): 0})
    enough = retention(toc, 10, {("1", "1"): 10, ("1", "2"): 3, ("1", "3"): 3})

    assert biggest_drops(small) == []
    assert [p.label for p in biggest_drops(enough)] == ["Гл. 2"]


def test_hints_are_sharp_drops_and_release_pauses_only() -> None:
    toc = _toc(("1", "1", 0), ("1", "2", 3), ("1", "3", 3 + PAUSE_DAYS), ("1", "4", 40))
    keep = 100 - SHARP_DROP  # exactly at the threshold
    points = retention(toc, 100, {("1", "1"): 100, ("1", "2"): int(keep), ("1", "3"): 69})

    found = hints(toc, points)

    assert [(h.kind, h.title) for h in found] == [
        ("drop", "Гл. 2: резкий обрыв"),
        ("pause", "Гл. 3: пауза в выпуске"),
        ("drop", "Гл. 4: резкий обрыв"),
    ]
    assert f"через {PAUSE_DAYS} дн." in found[1].text
    assert "Из 70 читателей предыдущей главы её открыли 69" in found[1].text


def test_no_pause_without_release_dates() -> None:
    toc = _toc(("1", "1"), ("1", "2", 400))  # the first has no translations listed

    assert hints(toc, retention(toc, 0, {})) == []


def test_finished_is_the_last_chapter_of_the_table_of_contents() -> None:
    toc = _toc(("1", "1"), ("1", "2"), ("1", "3"))

    result = finished(toc, [("1", "3"), ("1", "1"), ("1", "3"), ("9", "99")])

    assert (result.done, result.total) == (2, 3)  # the unknown chapter isn't counted
    assert result.text == "67% (2 из 3)"
    assert finished(toc, [("9", "99")]) is None
    assert finished(toc, []) is None


# --- queries ------------------------------------------------------------------------------


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection_ = await fresh_connection()
    await run_migrations(connection_)
    return connection_


async def _user(conn: psycopg.AsyncConnection, name: str) -> int:
    cursor = await conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (%s, 'x', %s) RETURNING id",
        (f"{name}@example.com", (NOW - timedelta(days=400)).isoformat()),
    )
    return (await cursor.fetchone())["id"]


async def _read(
    conn: psycopg.AsyncConnection, user: int, slug: str, number: str, at: datetime = NOW
) -> None:
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, created_at) "
        "VALUES (%s, 'chapter_read', %s, '1', %s, %s)",
        (user, slug, number, (at - timedelta(hours=1)).isoformat()),
    )


async def _add(conn: psycopg.AsyncConnection, user: int, slug: str, at: datetime = NOW) -> None:
    await conn.execute(
        "INSERT INTO library_entries (user_id, slug_url, added_at) VALUES (%s, %s, %s)",
        (user, slug, (at - timedelta(hours=1)).isoformat()),
    )


async def test_title_rows_sort_and_count(conn: psycopg.AsyncConnection) -> None:
    a, b, c = [await _user(conn, n) for n in "abc"]
    for user in (a, b):
        await _read(conn, user, "1--popular", "1")
    for _ in range(4):
        await _read(conn, c, "2--binge", "1")
    await _read(conn, a, "2--binge", "1", NOW - timedelta(days=10))  # previous period
    await _add(conn, a, "3--only-added")
    await _add(conn, b, "3--only-added")
    await _read(conn, a, "4--old", "1", NOW - timedelta(days=10))  # nothing this period

    by_readers = await title_rows(conn, "7d", "UTC", now=NOW)
    by_library = await title_rows(conn, "7d", "UTC", sort="library", now=NOW)
    ascending = await title_rows(conn, "7d", "UTC", sort="chapters", descending=False, now=NOW)
    unknown = await title_rows(conn, "7d", "UTC", sort="password_hash; --", now=NOW)

    assert [(r.slug_url, r.readers, r.previous_readers, r.chapters, r.library_adds)
            for r in by_readers] == [
        ("1--popular", 2, 0, 2, 0),
        ("2--binge", 1, 1, 4, 0),
        ("3--only-added", 0, 0, 0, 2),
    ]  # fmt: skip
    assert by_library[0].slug_url == "3--only-added"
    assert [r.slug_url for r in ascending][0] == "3--only-added"
    assert [r.slug_url for r in unknown] == [r.slug_url for r in by_readers]
    assert len(await title_rows(conn, "7d", "UTC", limit=1, now=NOW)) == 1


async def test_chapter_reach_counts_only_the_first_chapters_readers(
    conn: psycopg.AsyncConnection,
) -> None:
    a, b, c = [await _user(conn, n) for n in "abc"]
    for user, last in ((a, 3), (b, 2)):
        for n in range(1, last + 1):
            await _read(conn, user, "1--t", str(n), NOW - timedelta(days=100))
    await _read(conn, c, "1--t", "3")  # started from chapter 3 - not in the cohort
    await _read(conn, c, "2--other", "1")

    reach = await chapter_reach(conn, "1--t", ("1", "1"))

    assert reach.cohort == 2
    assert reach.reached == {("1", "1"): 2, ("1", "2"): 2, ("1", "3"): 1}
    assert (await chapter_reach(conn, "9--nobody", ("1", "1"))).cohort == 0


async def test_title_totals(conn: psycopg.AsyncConnection) -> None:
    a, b = await _user(conn, "a"), await _user(conn, "b")
    await _read(conn, a, "1--t", "1")
    await _read(conn, a, "1--t", "2")
    await _read(conn, b, "1--t", "1", NOW - timedelta(days=9))
    await _add(conn, a, "1--t")

    totals = await title_totals(conn, "1--t", "7d", "UTC", now=NOW)

    assert (totals.readers.current, totals.readers.previous) == (1, 1)
    assert (totals.chapters.current, totals.chapters.previous) == (2, 1)
    assert totals.library_adds.current == 1
    assert totals.seconds.percent is None


# --- the pages ----------------------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def sdk() -> dict[str, TitleDetails]:
    """The SDK's titles, by slug_url - a title not in here is one the SDK can't resolve."""
    return {}


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock, sdk: dict[str, TitleDetails]
) -> Iterator[TestClient]:
    async def summaries(slugs: set[str]) -> dict[str, TitleSummary]:
        return {s: TitleSummary(sdk[s].name, None) for s in slugs if s in sdk}

    async def contents(slugs: list[str]) -> dict[str, list[Volume]]:
        return {s: sdk[s].volumes for s in slugs if s in sdk}

    async def details(slug: str) -> TitleDetails | None:
        return sdk.get(slug)

    monkeypatch.setattr(admin_api, "title_summaries", summaries)
    monkeypatch.setattr(admin_api, "tables_of_contents", contents)
    monkeypatch.setattr(admin_api, "title_details", details)
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        assert _login(test_client).status_code == 303
        yield test_client
    get_settings.cache_clear()


async def _seed(*reads: tuple[str, str, str]) -> None:
    """(user, slug_url, chapter number) reads, an hour ago."""
    async with connection() as conn:
        users: dict[str, int] = {}
        for name, slug, number in reads:
            if name not in users:
                users[name] = await _user(conn, name)
            await _read(conn, users[name], slug, number, datetime.now(UTC))


def _details(name: str, toc: list[Volume]) -> TitleDetails:
    return TitleDetails(name=name, cover_url=None, status="Онгоинг", volumes=toc)


def test_an_empty_list(client: TestClient) -> None:
    html = client.get("/admin/analytics").text

    assert "За 7 дней ничего не читали" in html


async def test_the_list_with_finished_for_the_top(
    client: TestClient, sdk: dict[str, TitleDetails]
) -> None:
    await _seed(("a", "1--known", "1"), ("b", "1--known", "1"), ("a", "2--gone", "1"))
    sdk["1--known"] = _details("Известный тайтл", _toc(("1", "1"), ("1", "2")))
    async with connection() as conn:
        user = (await (await conn.execute("SELECT id FROM users LIMIT 1")).fetchone())["id"]
        await conn.execute(
            "INSERT INTO library_entries (user_id, slug_url, added_at, last_read_volume, "
            "last_read_number) VALUES (%s, '1--known', %s, '1', '2')",
            (user, datetime.now(UTC).isoformat()),
        )

    html = client.get("/admin/analytics").text

    assert 'href="/admin/analytics/1--known"' in html
    assert "Известный тайтл" in html
    assert ">2--gone<" in html  # the SDK couldn't resolve it - its slug_url instead
    assert "100% (1 из 1)" in html
    assert "нет данных" in html  # no table of contents for 2--gone


async def test_sorting_comes_from_a_whitelist(client: TestClient) -> None:
    await _seed(("a", "1--t", "1"))

    response = client.get("/admin/analytics", params={"sort": "evil", "order": "asc"})

    assert response.status_code == 200
    assert re.search(r'aria-sort="ascending"[^>]*>\s*<a[^>]*>Читатели', response.text)


async def test_a_title_page(client: TestClient, sdk: dict[str, TitleDetails]) -> None:
    await _seed(*[(u, "1--t", n) for u in "abcd" for n in ("1", "2")], ("a", "1--t", "3"))
    sdk["1--t"] = _details("Тайтл", _toc(("1", "1"), ("1", "2"), ("1", "3")))

    html = client.get("/admin/analytics/1--t").text

    assert "Первую главу открыли: 4" in html
    assert html.count('class="wn-admin-chart__bar') == 3
    assert "Гл. 3" in html and "−75%" in html  # 1 of 4 went on to chapter 3
    assert "Гл. 3: резкий обрыв" in html


async def test_a_title_with_one_chapter_page(
    client: TestClient, sdk: dict[str, TitleDetails]
) -> None:
    await _seed(("a", "1--short", "1"))
    sdk["1--short"] = _details("Короткий", _toc(("1", "1")))

    html = client.get("/admin/analytics/1--short").text

    assert html.count('class="wn-admin-chart__bar') == 1
    assert "Оттока не видно" in html
    assert "Проверять нечего" in html


def test_a_title_without_readers(client: TestClient, sdk: dict[str, TitleDetails]) -> None:
    sdk["1--new"] = _details("Новый", _toc(("1", "1"), ("1", "2")))

    response = client.get("/admin/analytics/1--new")

    assert response.status_code == 200
    assert "Первую главу ещё никто не открывал" in response.text
    assert "wn-admin-chart__bar" not in response.text


async def test_without_the_sdk_the_page_shows_our_own_numbers(client: TestClient) -> None:
    await _seed(("a", "1--gone", "1"))

    html = client.get("/admin/analytics/1--gone").text

    assert "Оглавление недоступно" in html
    assert "Удержание по главам" not in html
    assert client.get("/admin/analytics/9--nobody-knows").status_code == 404
    assert client.get("/admin/analytics/" + "x" * 201).status_code == 422
