import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.db.activity import (
    ChapterReadCount,
    daily_active_seconds,
    daily_reading_activity,
    list_chapters_read_today,
    record_chapter_read,
    record_heartbeat,
    total_active_seconds_today,
)
from app.db.migrate import run_migrations


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (1, 'alice@example.com', 'hash', 'now')"
    )
    connection.commit()
    return connection


def _insert_event(
    conn: sqlite3.Connection, *, kind: str, slug_url: str, seconds: int | None, created_at: str
) -> None:
    conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
        "VALUES (1, ?, ?, ?, ?)",
        (kind, slug_url, seconds, created_at),
    )
    conn.commit()


def test_record_chapter_read_counts_towards_today(conn: sqlite3.Connection) -> None:
    record_chapter_read(conn, 1, "6712--test-novel", "1", "5")

    counts = list_chapters_read_today(conn, 1)
    assert counts == [ChapterReadCount(slug_url="6712--test-novel", chapters_read=1)]


def test_list_chapters_read_today_groups_by_title(conn: sqlite3.Connection) -> None:
    record_chapter_read(conn, 1, "6712--test-novel", "1", "5")
    record_chapter_read(conn, 1, "6712--test-novel", "1", "6")
    record_chapter_read(conn, 1, "999--other-novel", "1", "1")

    counts = {c.slug_url: c.chapters_read for c in list_chapters_read_today(conn, 1)}
    assert counts == {"6712--test-novel": 2, "999--other-novel": 1}


def test_list_chapters_read_today_excludes_yesterday(conn: sqlite3.Connection) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _insert_event(
        conn, kind="chapter_read", slug_url="old--novel", seconds=None, created_at=yesterday
    )

    assert list_chapters_read_today(conn, 1) == []


def test_list_chapters_read_today_excludes_other_users(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    record_chapter_read(conn, 2, "6712--test-novel", "1", "5")

    assert list_chapters_read_today(conn, 1) == []


def test_list_chapters_read_today_ignores_heartbeat_events(conn: sqlite3.Connection) -> None:
    record_heartbeat(conn, 1, "6712--test-novel", 30)

    assert list_chapters_read_today(conn, 1) == []


def test_total_active_seconds_today_sums_heartbeats(conn: sqlite3.Connection) -> None:
    record_heartbeat(conn, 1, "6712--test-novel", 30)
    record_heartbeat(conn, 1, "999--other-novel", 45)

    assert total_active_seconds_today(conn, 1) == 75


def test_total_active_seconds_today_excludes_yesterday(conn: sqlite3.Connection) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _insert_event(conn, kind="heartbeat", slug_url="old--novel", seconds=999, created_at=yesterday)

    assert total_active_seconds_today(conn, 1) == 0


def test_total_active_seconds_today_zero_when_no_heartbeats(conn: sqlite3.Connection) -> None:
    assert total_active_seconds_today(conn, 1) == 0


def test_daily_reading_activity_is_empty_with_no_history(conn: sqlite3.Connection) -> None:
    assert daily_reading_activity(conn, 1) == {}


def test_daily_reading_activity_counts_chapters_read_today(conn: sqlite3.Connection) -> None:
    record_chapter_read(conn, 1, "6712--test-novel", "1", "1")
    record_chapter_read(conn, 1, "6712--test-novel", "1", "2")

    today = datetime.now(UTC).date().isoformat()
    assert daily_reading_activity(conn, 1) == {today: 2}


def test_daily_reading_activity_groups_by_calendar_day(conn: sqlite3.Connection) -> None:
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    _insert_event(
        conn,
        kind="chapter_read",
        slug_url="6712--test-novel",
        seconds=None,
        created_at=three_days_ago,
    )
    record_chapter_read(conn, 1, "6712--test-novel", "1", "5")

    today = datetime.now(UTC).date().isoformat()
    three_days_ago_date = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    assert daily_reading_activity(conn, 1) == {today: 1, three_days_ago_date: 1}


def test_daily_reading_activity_excludes_events_outside_the_window(
    conn: sqlite3.Connection,
) -> None:
    too_old = (datetime.now(UTC) - timedelta(weeks=53)).isoformat()
    _insert_event(
        conn, kind="chapter_read", slug_url="6712--test-novel", seconds=None, created_at=too_old
    )

    assert daily_reading_activity(conn, 1, weeks=52) == {}


def test_daily_reading_activity_ignores_heartbeat_events(conn: sqlite3.Connection) -> None:
    record_heartbeat(conn, 1, "6712--test-novel", 30)

    assert daily_reading_activity(conn, 1) == {}


def test_daily_reading_activity_is_scoped_to_the_user(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    record_chapter_read(conn, 2, "6712--test-novel", "1", "5")

    assert daily_reading_activity(conn, 1) == {}


def test_daily_active_seconds_is_empty_with_no_history(conn: sqlite3.Connection) -> None:
    assert daily_active_seconds(conn, 1) == {}


def test_daily_active_seconds_sums_heartbeats_today(conn: sqlite3.Connection) -> None:
    record_heartbeat(conn, 1, "6712--test-novel", 30)
    record_heartbeat(conn, 1, "999--other-novel", 45)

    today = datetime.now(UTC).date().isoformat()
    assert daily_active_seconds(conn, 1) == {today: 75}


def test_daily_active_seconds_groups_by_calendar_day(conn: sqlite3.Connection) -> None:
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    _insert_event(
        conn, kind="heartbeat", slug_url="6712--test-novel", seconds=20, created_at=three_days_ago
    )
    record_heartbeat(conn, 1, "6712--test-novel", 30)

    today = datetime.now(UTC).date().isoformat()
    three_days_ago_date = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    assert daily_active_seconds(conn, 1) == {today: 30, three_days_ago_date: 20}


def test_daily_active_seconds_excludes_events_outside_the_window(
    conn: sqlite3.Connection,
) -> None:
    too_old = (datetime.now(UTC) - timedelta(weeks=53)).isoformat()
    _insert_event(conn, kind="heartbeat", slug_url="6712--test-novel", seconds=30, created_at=too_old)

    assert daily_active_seconds(conn, 1, weeks=52) == {}


def test_daily_active_seconds_ignores_chapter_read_events(conn: sqlite3.Connection) -> None:
    record_chapter_read(conn, 1, "6712--test-novel", "1", "1")

    assert daily_active_seconds(conn, 1) == {}


def test_daily_active_seconds_is_scoped_to_the_user(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    record_heartbeat(conn, 2, "6712--test-novel", 30)

    assert daily_active_seconds(conn, 1) == {}
