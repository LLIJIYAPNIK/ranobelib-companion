from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.db.activity import (
    ChapterReadCount,
    daily_active_seconds,
    daily_reading_activity,
    daily_titles_read,
    list_chapters_read_today,
    record_chapter_read,
    record_heartbeat,
    total_active_seconds_today,
)
from app.db.migrate import run_migrations
from tests.db_reset import fresh_connection


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    await connection.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (1, 'alice@example.com', 'hash', 'now')"
    )
    return connection


async def _insert_event(
    conn: psycopg.AsyncConnection, *, kind: str, slug_url: str, seconds: int | None, created_at: str
) -> None:
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
        "VALUES (1, %s, %s, %s, %s)",
        (kind, slug_url, seconds, created_at),
    )


async def test_record_chapter_read_counts_towards_today(conn: psycopg.AsyncConnection) -> None:
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")

    counts = await list_chapters_read_today(conn, 1)
    assert counts == [ChapterReadCount(slug_url="6712--test-novel", chapters_read=1)]


async def test_list_chapters_read_today_groups_by_title(conn: psycopg.AsyncConnection) -> None:
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "6")
    await record_chapter_read(conn, 1, "999--other-novel", "1", "1")

    counts = {c.slug_url: c.chapters_read for c in await list_chapters_read_today(conn, 1)}
    assert counts == {"6712--test-novel": 2, "999--other-novel": 1}


async def test_list_chapters_read_today_excludes_yesterday(conn: psycopg.AsyncConnection) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await _insert_event(
        conn, kind="chapter_read", slug_url="old--novel", seconds=None, created_at=yesterday
    )

    assert await list_chapters_read_today(conn, 1) == []


async def test_list_chapters_read_today_excludes_other_users(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await record_chapter_read(conn, 2, "6712--test-novel", "1", "5")

    assert await list_chapters_read_today(conn, 1) == []


async def test_list_chapters_read_today_ignores_heartbeat_events(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_heartbeat(conn, 1, "6712--test-novel", 30)

    assert await list_chapters_read_today(conn, 1) == []


async def test_total_active_seconds_today_sums_heartbeats(conn: psycopg.AsyncConnection) -> None:
    await record_heartbeat(conn, 1, "6712--test-novel", 30)
    await record_heartbeat(conn, 1, "999--other-novel", 45)

    assert await total_active_seconds_today(conn, 1) == 75


async def test_total_active_seconds_today_excludes_yesterday(conn: psycopg.AsyncConnection) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await _insert_event(
        conn, kind="heartbeat", slug_url="old--novel", seconds=999, created_at=yesterday
    )

    assert await total_active_seconds_today(conn, 1) == 0


async def test_total_active_seconds_today_zero_when_no_heartbeats(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await total_active_seconds_today(conn, 1) == 0


async def test_daily_reading_activity_is_empty_with_no_history(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await daily_reading_activity(conn, 1) == {}


async def test_daily_reading_activity_counts_chapters_read_today(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "1")
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "2")

    today = datetime.now(UTC).date().isoformat()
    assert await daily_reading_activity(conn, 1) == {today: 2}


async def test_daily_reading_activity_groups_by_calendar_day(conn: psycopg.AsyncConnection) -> None:
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    await _insert_event(
        conn,
        kind="chapter_read",
        slug_url="6712--test-novel",
        seconds=None,
        created_at=three_days_ago,
    )
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "5")

    today = datetime.now(UTC).date().isoformat()
    three_days_ago_date = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    assert await daily_reading_activity(conn, 1) == {today: 1, three_days_ago_date: 1}


async def test_daily_reading_activity_excludes_events_outside_the_window(
    conn: psycopg.AsyncConnection,
) -> None:
    too_old = (datetime.now(UTC) - timedelta(weeks=53)).isoformat()
    await _insert_event(
        conn, kind="chapter_read", slug_url="6712--test-novel", seconds=None, created_at=too_old
    )

    assert await daily_reading_activity(conn, 1, weeks=52) == {}


async def test_daily_reading_activity_ignores_heartbeat_events(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_heartbeat(conn, 1, "6712--test-novel", 30)

    assert await daily_reading_activity(conn, 1) == {}


async def test_daily_reading_activity_is_scoped_to_the_user(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await record_chapter_read(conn, 2, "6712--test-novel", "1", "5")

    assert await daily_reading_activity(conn, 1) == {}


async def test_daily_active_seconds_is_empty_with_no_history(conn: psycopg.AsyncConnection) -> None:
    assert await daily_active_seconds(conn, 1) == {}


async def test_daily_active_seconds_sums_heartbeats_today(conn: psycopg.AsyncConnection) -> None:
    await record_heartbeat(conn, 1, "6712--test-novel", 30)
    await record_heartbeat(conn, 1, "999--other-novel", 45)

    today = datetime.now(UTC).date().isoformat()
    assert await daily_active_seconds(conn, 1) == {today: 75}


async def test_daily_active_seconds_groups_by_calendar_day(conn: psycopg.AsyncConnection) -> None:
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    await _insert_event(
        conn, kind="heartbeat", slug_url="6712--test-novel", seconds=20, created_at=three_days_ago
    )
    await record_heartbeat(conn, 1, "6712--test-novel", 30)

    today = datetime.now(UTC).date().isoformat()
    three_days_ago_date = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    assert await daily_active_seconds(conn, 1) == {today: 30, three_days_ago_date: 20}


async def test_daily_active_seconds_excludes_events_outside_the_window(
    conn: psycopg.AsyncConnection,
) -> None:
    too_old = (datetime.now(UTC) - timedelta(weeks=53)).isoformat()
    await _insert_event(
        conn, kind="heartbeat", slug_url="6712--test-novel", seconds=30, created_at=too_old
    )

    assert await daily_active_seconds(conn, 1, weeks=52) == {}


async def test_daily_active_seconds_ignores_chapter_read_events(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "1")

    assert await daily_active_seconds(conn, 1) == {}


async def test_daily_active_seconds_is_scoped_to_the_user(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await record_heartbeat(conn, 2, "6712--test-novel", 30)

    assert await daily_active_seconds(conn, 1) == {}


async def test_daily_titles_read_is_empty_with_no_history(conn: psycopg.AsyncConnection) -> None:
    assert await daily_titles_read(conn, 1) == {}


async def test_daily_titles_read_lists_the_one_title_read_that_day(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "1")

    today = datetime.now(UTC).date().isoformat()
    assert await daily_titles_read(conn, 1) == {today: ["6712--test-novel"]}


async def test_daily_titles_read_deduplicates_a_title_read_multiple_times(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "1")
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "2")

    today = datetime.now(UTC).date().isoformat()
    assert await daily_titles_read(conn, 1) == {today: ["6712--test-novel"]}


async def test_daily_titles_read_orders_most_recently_read_first(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_chapter_read(conn, 1, "6712--first-novel", "1", "1")
    await record_chapter_read(conn, 1, "999--second-novel", "1", "1")

    today = datetime.now(UTC).date().isoformat()
    assert await daily_titles_read(conn, 1) == {today: ["999--second-novel", "6712--first-novel"]}


async def test_daily_titles_read_groups_by_calendar_day(conn: psycopg.AsyncConnection) -> None:
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    await _insert_event(
        conn, kind="chapter_read", slug_url="old--novel", seconds=None, created_at=three_days_ago
    )
    await record_chapter_read(conn, 1, "6712--test-novel", "1", "1")

    today = datetime.now(UTC).date().isoformat()
    three_days_ago_date = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    assert await daily_titles_read(conn, 1) == {
        today: ["6712--test-novel"],
        three_days_ago_date: ["old--novel"],
    }


async def test_daily_titles_read_excludes_events_outside_the_window(
    conn: psycopg.AsyncConnection,
) -> None:
    too_old = (datetime.now(UTC) - timedelta(weeks=53)).isoformat()
    await _insert_event(
        conn, kind="chapter_read", slug_url="6712--test-novel", seconds=None, created_at=too_old
    )

    assert await daily_titles_read(conn, 1, weeks=52) == {}


async def test_daily_titles_read_ignores_heartbeat_events(conn: psycopg.AsyncConnection) -> None:
    await record_heartbeat(conn, 1, "6712--test-novel", 30)

    assert await daily_titles_read(conn, 1) == {}


async def test_daily_titles_read_is_scoped_to_the_user(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await record_chapter_read(conn, 2, "6712--test-novel", "1", "1")

    assert await daily_titles_read(conn, 1) == {}
