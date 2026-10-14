"""PR 347: «Поведение читателей» - the weekday x hour heatmap and new against returning
readers, both on the admin's clock: the zone moves events across hours, weekdays and
days, and the day boundary is local midnight. Empty and thin data don't invent a peak."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.api.admin as admin_api
from app.admin_analytics import HEATMAP_MIN_READS, reading_times
from app.config import get_settings
from app.db.admin_analytics import NEW_AND_RETURNING_DAYS, new_and_returning, reading_heatmap
from app.db.connection import connection
from app.db.migrate import run_migrations
from tests.db_reset import fresh_connection
from tests.test_admin_auth import PASSWORD, Clock, _client, _login

NOW = datetime(2026, 3, 18, 12, tzinfo=UTC)  # a Wednesday
MONDAY = datetime(2026, 3, 16, tzinfo=UTC)


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection_ = await fresh_connection()
    await run_migrations(connection_)
    return connection_


async def _user(conn: psycopg.AsyncConnection, registered: datetime) -> int:
    cursor = await conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (%s, 'x', %s) RETURNING id",
        (f"u{registered.timestamp()}@example.com", registered.isoformat()),
    )
    return (await cursor.fetchone())["id"]


async def _read(conn: psycopg.AsyncConnection, user: int, at: datetime) -> None:
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, created_at) "
        "VALUES (%s, 'chapter_read', 't', '1', '1', %s)",
        (user, at.isoformat()),
    )


# --- the heatmap --------------------------------------------------------------------------


async def test_the_zone_moves_reads_across_hours_and_weekdays(
    conn: psycopg.AsyncConnection,
) -> None:
    user = await _user(conn, MONDAY - timedelta(days=100))
    await _read(conn, user, MONDAY + timedelta(hours=21, minutes=30))  # Mon 21:30 UTC

    utc = await reading_heatmap(conn, "30d", "UTC", NOW)
    moscow = await reading_heatmap(conn, "30d", "Europe/Moscow", NOW)  # UTC+3
    kolkata = await reading_heatmap(conn, "30d", "Asia/Kolkata", NOW)  # UTC+5:30

    assert utc[0][21] == 1  # Monday, 21:00
    assert moscow[1][0] == 1  # Tuesday, 00:00
    assert kolkata[1][3] == 1  # Tuesday 03:00
    for grid in (utc, moscow, kolkata):
        assert sum(map(sum, grid)) == 1
        assert len(grid) == 7 and all(len(row) == 24 for row in grid)


async def test_local_midnight_is_the_day_boundary(conn: psycopg.AsyncConnection) -> None:
    user = await _user(conn, MONDAY - timedelta(days=100))
    midnight = MONDAY + timedelta(hours=21)  # Tue 00:00 in Moscow
    await _read(conn, user, midnight - timedelta(seconds=1))
    await _read(conn, user, midnight)

    grid = await reading_heatmap(conn, "30d", "Europe/Moscow", NOW)

    assert grid[0][23] == 1  # Monday 23:59:59
    assert grid[1][0] == 1  # Tuesday 00:00:00


async def test_the_heatmap_covers_only_its_period(conn: psycopg.AsyncConnection) -> None:
    user = await _user(conn, NOW - timedelta(days=200))
    await _read(conn, user, NOW - timedelta(days=10))
    await _read(conn, user, NOW - timedelta(days=60))

    assert sum(map(sum, await reading_heatmap(conn, "7d", "UTC", NOW))) == 0
    assert sum(map(sum, await reading_heatmap(conn, "30d", "UTC", NOW))) == 1
    assert sum(map(sum, await reading_heatmap(conn, "90d", "UTC", NOW))) == 2


def test_thin_data_names_no_peak() -> None:
    grid = [[0] * 24 for _ in range(7)]
    grid[4][21] = HEATMAP_MIN_READS - 1

    thin = reading_times(grid)
    grid[4][21] = HEATMAP_MIN_READS
    enough = reading_times(grid)
    empty = reading_times([[0] * 24 for _ in range(7)])

    assert not thin.enough
    assert enough.enough and enough.chart.peak == "Пт, 21:00"
    assert empty.total == 0 and empty.chart.peak is None


# --- new and returning --------------------------------------------------------------------


async def test_new_means_active_on_the_local_day_of_registration(
    conn: psycopg.AsyncConnection,
) -> None:
    # Registered at 23:30 and read at 00:30 Moscow time: the same day in UTC (20:30 and
    # 21:30), but the next one in Moscow.
    registered = datetime(2026, 3, 17, 20, 30, tzinfo=UTC)
    newcomer = await _user(conn, registered)
    await _read(conn, newcomer, registered + timedelta(hours=1))
    regular = await _user(conn, NOW - timedelta(days=100))
    await _read(conn, regular, registered + timedelta(hours=1))
    await _read(conn, regular, registered + timedelta(hours=2))  # one reader-day
    # Registered at 00:30 and read at 01:00 Moscow time - both on the 18th there, while
    # the registration is still the 17th in UTC: new only if the day is the local one.
    night_owl = await _user(conn, datetime(2026, 3, 17, 21, 30, tzinfo=UTC))
    await _read(conn, night_owl, datetime(2026, 3, 17, 22, tzinfo=UTC))

    utc = {d.day: d for d in await new_and_returning(conn, "UTC", now=NOW)}
    moscow = {d.day: d for d in await new_and_returning(conn, "Europe/Moscow", now=NOW)}

    assert (utc["2026-03-17"].new, utc["2026-03-17"].returning) == (2, 1)
    assert (moscow["2026-03-18"].new, moscow["2026-03-18"].returning) == (1, 2)


async def test_every_day_of_the_window_is_there_oldest_first(
    conn: psycopg.AsyncConnection,
) -> None:
    days = await new_and_returning(conn, "Europe/Moscow", now=NOW)

    assert len(days) == NEW_AND_RETURNING_DAYS
    assert days[0].day == "2026-03-05" and days[-1].day == "2026-03-18"
    assert all(d.new == d.returning == 0 for d in days)


# --- the page -----------------------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(
        monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD, ADMIN_TIMEZONE="Europe/Moscow"
    ) as test_client:
        assert _login(test_client).status_code == 303
        yield test_client
    get_settings.cache_clear()


def test_an_empty_page(client: TestClient) -> None:
    html = client.get("/admin/analytics/behaviour").text

    assert "За 30 дней ничего не читали" in html
    assert f"За {NEW_AND_RETURNING_DAYS} дней никто не читал" in html
    assert "Europe/Moscow" in html and "МСК" not in html
    assert 'href="/admin/analytics/behaviour" aria-current="page"' in html


async def test_a_thin_page_says_so(client: TestClient) -> None:
    async with connection() as conn:
        user = await _user(conn, datetime.now(UTC) - timedelta(days=100))
        await _read(conn, user, datetime.now(UTC) - timedelta(hours=1))

    html = client.get("/admin/analytics/behaviour").text

    assert "Пока мало данных: 1 прочитанных глав" in html
    assert "Пик:" not in html
    assert "Вернувшиеся — 100%" in html


def test_a_24h_period_falls_back_to_30_days(client: TestClient) -> None:
    html = client.get("/admin/analytics/behaviour", params={"period": "24h"}).text

    assert "за 30 дней" in html
    assert ">24 ч<" not in html


def test_a_zone_postgres_doesnt_know_falls_back_to_utc(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unknown(conn, name: str) -> bool:  # noqa: ANN001
        return False

    monkeypatch.setattr(admin_api, "is_valid_timezone", unknown)

    html = client.get("/admin/analytics/behaviour").text

    assert "· UTC" in html and "Europe/Moscow</p>" not in html
