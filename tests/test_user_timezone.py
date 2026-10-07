"""PR 322: activity "days" follow the user's IANA time zone (users.timezone, UTC if unset)."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.db.activity as activity
from app.api.activity import _event_time
from app.config import get_settings
from app.db.migrate import run_migrations
from app.timezones import day_start_utc, local_date, user_timezone
from tests.auth_helpers import register
from tests.db_reset import fresh_connection, reset_app_database

MOSCOW = "Europe/Moscow"  # UTC+3, no DST
TODAY = date(2026, 10, 7)
# 23:30 UTC on the 6th is 02:30 on the 7th in Moscow.
LATE_UTC = "2026-10-06T23:30:00+00:00"


# --- helpers -----------------------------------------------------------------------------


def test_local_midnight_as_a_utc_timestamp() -> None:
    assert day_start_utc(TODAY, MOSCOW) == "2026-10-06T21:00:00+00:00"
    assert day_start_utc(TODAY) == "2026-10-07T00:00:00+00:00"


def test_local_date_of_a_stored_timestamp() -> None:
    assert local_date(LATE_UTC, MOSCOW) == TODAY
    assert local_date(LATE_UTC) == date(2026, 10, 6)


@pytest.mark.parametrize("stored", [None, "", "Mars/Olympus_Mons"])
def test_unset_or_unknown_zone_falls_back_to_utc(stored: str | None) -> None:
    assert user_timezone(stored) == "UTC"
    assert user_timezone(MOSCOW) == MOSCOW


def test_event_history_uses_the_users_clock_and_calendar() -> None:
    assert _event_time(LATE_UTC, TODAY, MOSCOW) == "Сегодня · 02:30"
    assert _event_time(LATE_UTC, TODAY, "UTC") == "Вчера · 23:30"


# --- day buckets in the database ---------------------------------------------------------


@pytest.fixture
async def conn(monkeypatch: pytest.MonkeyPatch) -> psycopg.AsyncConnection:
    # Pin "today" so the boundary checks don't depend on when the suite runs.
    monkeypatch.setattr(activity, "local_today", lambda tz="UTC": TODAY)
    connection = await fresh_connection()
    await run_migrations(connection)
    await connection.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (1, 'alice@example.com', 'hash', 'now')"
    )
    for kind, seconds in (("chapter_read", None), ("heartbeat", 600)):
        await connection.execute(
            "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
            "VALUES (1, %s, 'late--novel', %s, %s)",
            (kind, seconds, LATE_UTC),
        )
    return connection


async def test_late_utc_reading_lands_on_the_next_local_day(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await activity.daily_reading_activity(conn, 1, tz=MOSCOW) == {"2026-10-07": 1}
    assert await activity.daily_active_seconds(conn, 1, tz=MOSCOW) == {"2026-10-07": 600}
    assert await activity.daily_titles_read(conn, 1, tz=MOSCOW) == {"2026-10-07": ["late--novel"]}


async def test_late_utc_reading_counts_as_today_in_moscow(
    conn: psycopg.AsyncConnection,
) -> None:
    read = await activity.list_chapters_read_today(conn, 1, MOSCOW)

    assert [r.slug_url for r in read] == ["late--novel"]
    assert await activity.total_active_seconds_today(conn, 1, MOSCOW) == 600


async def test_user_without_a_zone_keeps_utc_days(conn: psycopg.AsyncConnection) -> None:
    # The default is UTC - exactly the pre-PR 322 buckets.
    assert await activity.daily_reading_activity(conn, 1) == {"2026-10-06": 1}
    assert await activity.daily_reading_activity(conn, 1, tz="UTC") == {"2026-10-06": 1}
    assert await activity.list_chapters_read_today(conn, 1) == []
    assert await activity.total_active_seconds_today(conn, 1) == 0


async def test_streak_counts_on_the_same_local_calendar(conn: psycopg.AsyncConnection) -> None:
    moscow_days = await activity.daily_reading_activity(conn, 1, tz=MOSCOW)
    utc_days = await activity.daily_reading_activity(conn, 1)

    assert activity.reading_streak_days(moscow_days, MOSCOW) == 1  # read today
    assert activity.reading_streak_days(utc_days) == 1  # read yesterday, today not yet


async def test_the_window_starts_at_local_midnight(conn: psycopg.AsyncConnection) -> None:
    # A one-week window ending 7 Oct starts on 1 Oct: at 30 Sep 21:00 UTC in Moscow, at
    # 1 Oct 00:00 UTC in UTC. 30 Sep 22:00 UTC (1 Oct 01:00 in Moscow) is only in the
    # first.
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, created_at) "
        "VALUES (1, 'chapter_read', 'edge--novel', '2026-09-30T22:00:00+00:00')"
    )

    moscow = await activity.daily_titles_read(conn, 1, weeks=1, tz=MOSCOW)
    utc = await activity.daily_titles_read(conn, 1, weeks=1)

    assert moscow["2026-10-01"] == ["edge--novel"]
    assert all("edge--novel" not in slugs for slugs in utc.values())


# --- saving the zone ---------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _selected_zone(client: TestClient) -> str:
    page = client.get("/settings/account").text
    marker = '" selected>'
    end = page.index(marker)
    return page[page.rindex('value="', 0, end) + len('value="') : end]


def test_browser_reports_the_zone_once_while_it_is_unset(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    assert "js/timezone-sync.js" in client.get("/").text
    assert _selected_zone(client) == "UTC"

    response = client.post("/settings/timezone", data={"timezone": MOSCOW})

    assert response.status_code == 204
    assert _selected_zone(client) == MOSCOW
    assert "js/timezone-sync.js" not in client.get("/").text


def test_a_browser_report_never_overrides_a_saved_zone(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    client.post("/settings/account/timezone", data={"timezone": MOSCOW})

    response = client.post("/settings/timezone", data={"timezone": "Asia/Tokyo"})

    assert response.status_code == 204
    assert _selected_zone(client) == MOSCOW


def test_the_account_picker_always_overrides(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    client.post("/settings/timezone", data={"timezone": MOSCOW})

    response = client.post("/settings/account/timezone", data={"timezone": "Asia/Tokyo"})

    assert response.status_code == 200
    assert "Часовой пояс сохранён" in response.text
    assert _selected_zone(client) == "Asia/Tokyo"


def test_unknown_zones_are_rejected(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")

    assert (
        client.post("/settings/timezone", data={"timezone": "Mars/Olympus_Mons"}).status_code == 400
    )
    picker = client.post("/settings/account/timezone", data={"timezone": "Mars/Olympus_Mons"})
    assert picker.status_code == 400
    assert "Неизвестный часовой пояс" in picker.text
    assert _selected_zone(client) == "UTC"


def test_saving_a_zone_needs_an_account(client: TestClient) -> None:
    response = client.post("/settings/timezone", data={"timezone": MOSCOW}, follow_redirects=False)
    assert response.status_code == 303
