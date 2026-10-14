"""PR 344: the admin metrics layer - periods and their buckets in ADMIN_TIMEZONE (day and
month boundaries, DST, half-hour zones), the metric series, the comparison with the
previous period (including empty periods and division by zero), read-only and cached."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

import app.db.admin_data as admin_data
import app.db.admin_stats as admin_stats
from app.db.admin_stats import Comparison, build_period, compare, parse_period, period_stats
from app.db.migrate import run_migrations
from tests.db_reset import fresh_connection

NOW = datetime(2026, 3, 18, 14, 25, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


# --- periods ------------------------------------------------------------------------------


def test_24h_is_24_hours_ending_with_the_current_one() -> None:
    period = build_period("24h", "UTC", NOW)

    assert period.granularity == "hour"
    assert len(period.starts) == 24
    assert period.end == datetime(2026, 3, 18, 15, tzinfo=UTC)
    assert period.start == datetime(2026, 3, 17, 15, tzinfo=UTC)
    assert period.starts[-1] <= NOW < period.end
    assert all(b - a == HOUR for a, b in zip(period.starts, period.starts[1:], strict=False))


def test_day_periods_are_local_days_with_today_last() -> None:
    for key, days in (("7d", 7), ("30d", 30), ("90d", 90)):
        period = build_period(key, "UTC", NOW)
        assert period.granularity == "day"
        assert len(period.starts) == days
        assert period.end == datetime(2026, 3, 19, tzinfo=UTC)
        assert period.starts[-1] == datetime(2026, 3, 18, tzinfo=UTC)
        assert period.start == datetime(2026, 3, 19, tzinfo=UTC) - days * DAY


def test_days_start_at_local_midnight_of_the_admin_timezone() -> None:
    period = build_period("7d", "Europe/Moscow", datetime(2026, 3, 18, 22, 30, tzinfo=UTC))

    # 01:30 on the 19th in Moscow (UTC+3): the 19th is already today there.
    assert [label.date().isoformat() for label in period.labels][-1] == "2026-03-19"
    assert period.starts[-1] == datetime(2026, 3, 18, 21, tzinfo=UTC)
    assert all(label.hour == 0 for label in period.labels)


def test_a_dst_switch_makes_a_day_shorter_or_longer_but_hours_stay_hours() -> None:
    spring = build_period("7d", "Europe/Berlin", datetime(2026, 3, 31, 12, tzinfo=UTC))
    lengths = [b - a for a, b in zip(spring.starts, (*spring.starts[1:], spring.end), strict=True)]
    assert timedelta(hours=23) in lengths  # 29 March 2026
    assert all(label.hour == 0 for label in spring.labels)

    autumn = build_period("7d", "Europe/Berlin", datetime(2026, 10, 27, 12, tzinfo=UTC))
    lengths = [b - a for a, b in zip(autumn.starts, (*autumn.starts[1:], autumn.end), strict=True)]
    assert timedelta(hours=25) in lengths  # 25 October 2026

    hours = build_period("24h", "Europe/Berlin", datetime(2026, 10, 25, 12, tzinfo=UTC))
    assert hours.end - hours.start == 24 * HOUR
    local_hours = [label.hour for label in hours.labels]
    assert local_hours.count(2) == 2  # 02:00-03:00 happens twice that night


def test_hours_follow_a_half_hour_zone() -> None:
    period = build_period("24h", "Asia/Kolkata", datetime(2026, 3, 18, 0, 40, tzinfo=UTC))

    # 06:10 in Kolkata (UTC+5:30): the current local hour ends at 07:00 = 01:30 UTC.
    assert period.end == datetime(2026, 3, 18, 1, 30, tzinfo=UTC)
    assert all(label.minute == 0 for label in period.labels)


def test_the_previous_period_is_the_same_length_right_before() -> None:
    for key in admin_stats.PERIODS:
        period = build_period(key, "Europe/Berlin", NOW)
        previous = period.previous()
        assert previous.end == period.start
        assert len(previous.starts) == len(period.starts)
        assert list(previous.starts) == sorted(previous.starts)


def test_periods_cross_month_and_year_boundaries() -> None:
    period = build_period("30d", "UTC", datetime(2026, 1, 10, 9, tzinfo=UTC))
    previous = period.previous()

    assert period.start == datetime(2025, 12, 12, tzinfo=UTC)
    assert previous.start == datetime(2025, 11, 12, tzinfo=UTC)
    days = [label.date() for label in (*previous.labels, *period.labels)]
    assert all(b - a == DAY for a, b in zip(days, days[1:], strict=False))


def test_an_unknown_period_is_the_default_from_a_request_and_an_error_in_code() -> None:
    assert parse_period("30d") == "30d"
    assert parse_period(None) == admin_stats.DEFAULT_PERIOD
    assert parse_period("365d") == admin_stats.DEFAULT_PERIOD
    with pytest.raises(ValueError):
        build_period("365d", "UTC", NOW)


# --- comparison ---------------------------------------------------------------------------


def test_comparison_gives_the_change_and_its_percent() -> None:
    assert compare(150, 100) == Comparison(current=150, previous=100, delta=50, percent=50.0)
    assert compare(0, 40) == Comparison(current=0, previous=40, delta=-40, percent=-100.0)
    assert compare(1, 3).percent == pytest.approx(-66.667, abs=0.001)


def test_comparison_with_an_empty_previous_period_has_no_percent() -> None:
    assert compare(7, 0) == Comparison(current=7, previous=0, delta=7, percent=None)
    assert compare(0, 0) == Comparison(current=0, previous=0, delta=0, percent=None)


# --- series -------------------------------------------------------------------------------


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    admin_stats.clear_cache()
    return connection


async def _user(conn: psycopg.AsyncConnection, name: str, at: datetime = NOW - 400 * DAY) -> int:
    cursor = await conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (%s, 'x', %s) RETURNING id",
        (f"{name}@example.com", at.isoformat()),
    )
    return (await cursor.fetchone())["id"]


async def _read(conn: psycopg.AsyncConnection, user_id: int, at: datetime) -> None:
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, volume, number, created_at) "
        "VALUES (%s, 'chapter_read', 'title', '1', '1', %s)",
        (user_id, at.isoformat()),
    )


async def _heartbeat(conn: psycopg.AsyncConnection, user_id: int, at: datetime, s: int) -> None:
    await conn.execute(
        "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
        "VALUES (%s, 'heartbeat', 'title', %s, %s)",
        (user_id, s, at.isoformat()),
    )


async def test_an_empty_database_gives_zero_series_and_no_percent(
    conn: psycopg.AsyncConnection,
) -> None:
    stats = await period_stats(conn, "7d", "UTC", NOW)

    assert list(stats.metrics) == list(admin_stats.METRICS)
    for metric in stats.metrics.values():
        assert metric.current == [0] * 7
        assert metric.previous == [0] * 7
        assert metric.comparison == Comparison(current=0, previous=0, delta=0, percent=None)


async def test_rows_land_in_their_local_day_and_the_edges_belong_to_one_bucket(
    conn: psycopg.AsyncConnection,
) -> None:
    alice = await _user(conn, "alice")
    period = build_period("7d", "Europe/Moscow", NOW)
    today = period.starts[-1]  # local midnight, 21:00 UTC the day before

    await _read(conn, alice, today)  # exactly midnight - today
    await _read(conn, alice, today - timedelta(microseconds=1))  # yesterday
    await _read(conn, alice, period.end - timedelta(microseconds=1))  # last moment of today
    await _read(conn, alice, period.end)  # tomorrow - outside
    await _read(conn, alice, period.start)  # first moment of the period
    await _read(conn, alice, period.start - timedelta(seconds=1))  # previous period, last day
    await _read(conn, alice, period.previous().start)  # previous period, first moment
    await _read(conn, alice, period.previous().start - timedelta(seconds=1))  # outside

    chapters = (await period_stats(conn, "7d", "Europe/Moscow", NOW)).metrics["chapters_read"]
    assert chapters.current == [1, 0, 0, 0, 0, 1, 2]
    assert chapters.previous == [1, 0, 0, 0, 0, 0, 1]
    assert chapters.comparison == Comparison(current=4, previous=2, delta=2, percent=100.0)


async def test_hourly_buckets(conn: psycopg.AsyncConnection) -> None:
    alice = await _user(conn, "alice")
    period = build_period("24h", "UTC", NOW)
    await _read(conn, alice, NOW)
    await _read(conn, alice, period.start + timedelta(minutes=59))
    await _read(conn, alice, period.start - timedelta(minutes=1))

    chapters = (await period_stats(conn, "24h", "UTC", NOW)).metrics["chapters_read"]
    assert len(chapters.current) == 24
    assert chapters.current[0] == 1
    assert chapters.current[-1] == 1
    assert chapters.previous[-1] == 1


async def test_active_readers_are_distinct_per_bucket_and_per_period(
    conn: psycopg.AsyncConnection,
) -> None:
    alice, bob = await _user(conn, "alice"), await _user(conn, "bob")
    for days_ago in (0, 1, 2):
        await _read(conn, alice, NOW - days_ago * DAY)
        await _read(conn, alice, NOW - days_ago * DAY - HOUR)
    await _heartbeat(conn, bob, NOW, 30)
    await _heartbeat(conn, bob, NOW - 10 * DAY, 30)

    readers = (await period_stats(conn, "7d", "UTC", NOW)).metrics["active_readers"]
    assert readers.current[-3:] == [1, 1, 2]
    # Three days of alice is still one reader for the week.
    assert readers.comparison == Comparison(current=2, previous=1, delta=1, percent=100.0)


async def test_reading_time_and_chapters_count_their_own_kind(
    conn: psycopg.AsyncConnection,
) -> None:
    alice = await _user(conn, "alice")
    await _heartbeat(conn, alice, NOW, 30)
    await _heartbeat(conn, alice, NOW - timedelta(minutes=1), 45)
    await _read(conn, alice, NOW)

    metrics = (await period_stats(conn, "7d", "UTC", NOW)).metrics
    assert metrics["reading_seconds"].current[-1] == 75
    assert metrics["chapters_read"].current[-1] == 1


async def test_registrations_downloads_and_comments(conn: psycopg.AsyncConnection) -> None:
    alice = await _user(conn, "alice", NOW - HOUR)
    await _user(conn, "bob", NOW - 8 * DAY)
    for status in ("done", "error"):
        await conn.execute(
            "INSERT INTO download_history (user_id, slug_url, fmt, status, finished_at) "
            "VALUES (%s, 'title', 'epub', %s, %s)",
            (alice, status, NOW.isoformat()),
        )
    await conn.execute(
        "INSERT INTO comments (user_id, slug_url, volume, number, paragraph_index, body, "
        "created_at, is_deleted) VALUES (%s, 'title', '1', '1', 0, 'hi', %s, 1)",
        (alice, (NOW - DAY).isoformat()),
    )

    metrics = (await period_stats(conn, "7d", "UTC", NOW)).metrics
    assert metrics["registrations"].comparison == compare(1, 1)
    assert metrics["downloads"].current[-1] == 2
    assert metrics["comments"].current[-2] == 1


async def test_the_queries_run_read_only(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []

    @asynccontextmanager
    async def watching(connection):  # noqa: ANN001, ANN202
        async with admin_data._read_only(connection):
            row = await (await connection.execute("SHOW transaction_read_only")).fetchone()
            seen.append(row["transaction_read_only"])
            yield

    monkeypatch.setattr(admin_stats, "_read_only", watching)
    await period_stats(conn, "90d", "UTC", NOW)
    assert seen == ["on"]


async def test_results_are_cached_for_a_short_while(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(admin_stats, "monotonic", lambda: clock[0])
    alice = await _user(conn, "alice")

    first = await admin_stats.cached_period_stats(conn, "24h", "UTC")
    await _read(conn, alice, datetime.now(UTC))
    assert await admin_stats.cached_period_stats(conn, "24h", "UTC") is first

    clock[0] += admin_stats.CACHE_SECONDS
    fresh = await admin_stats.cached_period_stats(conn, "24h", "UTC")
    assert fresh is not first
    assert fresh.metrics["chapters_read"].comparison.current == 1
    # Another period is computed on its own.
    assert await admin_stats.cached_period_stats(conn, "7d", "UTC") is not fresh


async def test_all_user_aggregates_can_use_the_kind_created_at_index(
    conn: psycopg.AsyncConnection,
) -> None:
    # Too few rows here for the planner to prefer an index on its own - forbid the
    # sequential scan and check the plan has an index to fall back on.
    async with conn.transaction():
        await conn.execute("SET LOCAL enable_seqscan = off")
        cursor = await conn.execute(
            "EXPLAIN SELECT COUNT(*) FROM activity_events "
            "WHERE kind = 'chapter_read' AND created_at >= %s AND created_at < %s",
            ((NOW - DAY).isoformat(), NOW.isoformat()),
        )
        plan = "\n".join(row["QUERY PLAN"] for row in await cursor.fetchall())
    assert "idx_activity_events_kind_created" in plan
