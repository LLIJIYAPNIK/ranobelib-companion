from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.db.downloads import (
    delete_entry,
    list_download_history,
    list_download_history_today,
    record_download,
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


async def test_record_download_stores_a_done_entry(conn: psycopg.AsyncConnection) -> None:
    await record_download(conn, 1, "6712--test-novel", "epub", "done", 42, None, job_id="job-1")

    entries = await list_download_history(conn, 1)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.user_id == 1
    assert entry.slug_url == "6712--test-novel"
    assert entry.fmt == "epub"
    assert entry.status == "done"
    assert entry.chapter_count == 42
    assert entry.error is None
    assert entry.finished_at is not None
    assert entry.job_id == "job-1"


async def test_record_download_job_id_defaults_to_none(conn: psycopg.AsyncConnection) -> None:
    await record_download(conn, 1, "6712--test-novel", "epub", "done", 42, None)

    assert (await list_download_history(conn, 1))[0].job_id is None


async def test_record_download_stores_an_error_entry(conn: psycopg.AsyncConnection) -> None:
    await record_download(conn, 1, "6712--test-novel", "epub", "error", None, "Внутренняя ошибка")

    entry = (await list_download_history(conn, 1))[0]
    assert entry.status == "error"
    assert entry.chapter_count is None
    assert entry.error == "Внутренняя ошибка"


async def test_list_download_history_most_recent_first(conn: psycopg.AsyncConnection) -> None:
    await record_download(conn, 1, "1--first", "epub", "done", 1, None)
    await record_download(conn, 1, "2--second", "epub", "done", 1, None)

    entries = await list_download_history(conn, 1)

    assert [entry.slug_url for entry in entries] == ["2--second", "1--first"]


async def test_list_download_history_only_returns_this_users_entries(
    conn: psycopg.AsyncConnection,
) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await record_download(conn, 1, "6712--test-novel", "epub", "done", 1, None)
    await record_download(conn, 2, "6712--test-novel", "epub", "done", 1, None)

    assert [entry.user_id for entry in await list_download_history(conn, 1)] == [1]


async def test_list_download_history_respects_limit(conn: psycopg.AsyncConnection) -> None:
    for i in range(5):
        await record_download(conn, 1, f"{i}--novel", "epub", "done", 1, None)

    assert len(await list_download_history(conn, 1, limit=3)) == 3


async def test_list_download_history_empty(conn: psycopg.AsyncConnection) -> None:
    assert await list_download_history(conn, 1) == []


async def test_list_download_history_today_includes_todays_entry(
    conn: psycopg.AsyncConnection,
) -> None:
    await record_download(conn, 1, "6712--test-novel", "epub", "done", 1, None)

    entries = await list_download_history_today(conn, 1)

    assert [entry.slug_url for entry in entries] == ["6712--test-novel"]


async def test_list_download_history_today_excludes_yesterday(
    conn: psycopg.AsyncConnection,
) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await conn.execute(
        "INSERT INTO download_history "
        "(user_id, slug_url, fmt, status, chapter_count, error, finished_at) "
        "VALUES (1, '6712--old-novel', 'epub', 'done', 1, NULL, %s)",
        (yesterday,),
    )

    assert await list_download_history_today(conn, 1) == []


async def test_list_download_history_today_excludes_other_users(
    conn: psycopg.AsyncConnection,
) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await record_download(conn, 2, "6712--test-novel", "epub", "done", 1, None)

    assert await list_download_history_today(conn, 1) == []


async def test_delete_entry_removes_this_users_entry(conn: psycopg.AsyncConnection) -> None:
    await record_download(conn, 1, "6712--test-novel", "epub", "done", 1, None)
    entry_id = (await list_download_history(conn, 1))[0].id

    deleted = await delete_entry(conn, entry_id, 1)

    assert deleted is True
    assert await list_download_history(conn, 1) == []


async def test_delete_entry_returns_false_for_unknown_id(conn: psycopg.AsyncConnection) -> None:
    assert await delete_entry(conn, 999, 1) is False


async def test_delete_entry_does_not_remove_another_users_entry(
    conn: psycopg.AsyncConnection,
) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await record_download(conn, 2, "6712--test-novel", "epub", "done", 1, None)
    entry_id = (await list_download_history(conn, 2))[0].id

    deleted = await delete_entry(conn, entry_id, 1)

    assert deleted is False
    assert len(await list_download_history(conn, 2)) == 1
