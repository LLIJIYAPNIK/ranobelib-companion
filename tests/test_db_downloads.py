import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.db.downloads import list_download_history, list_download_history_today, record_download
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


def test_record_download_stores_a_done_entry(conn: sqlite3.Connection) -> None:
    record_download(conn, 1, "6712--test-novel", "epub", "done", 42, None)

    entries = list_download_history(conn, 1)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.user_id == 1
    assert entry.slug_url == "6712--test-novel"
    assert entry.fmt == "epub"
    assert entry.status == "done"
    assert entry.chapter_count == 42
    assert entry.error is None
    assert entry.finished_at is not None


def test_record_download_stores_an_error_entry(conn: sqlite3.Connection) -> None:
    record_download(conn, 1, "6712--test-novel", "epub", "error", None, "Внутренняя ошибка")

    entry = list_download_history(conn, 1)[0]
    assert entry.status == "error"
    assert entry.chapter_count is None
    assert entry.error == "Внутренняя ошибка"


def test_list_download_history_most_recent_first(conn: sqlite3.Connection) -> None:
    record_download(conn, 1, "1--first", "epub", "done", 1, None)
    record_download(conn, 1, "2--second", "epub", "done", 1, None)

    entries = list_download_history(conn, 1)

    assert [entry.slug_url for entry in entries] == ["2--second", "1--first"]


def test_list_download_history_only_returns_this_users_entries(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    record_download(conn, 1, "6712--test-novel", "epub", "done", 1, None)
    record_download(conn, 2, "6712--test-novel", "epub", "done", 1, None)

    assert [entry.user_id for entry in list_download_history(conn, 1)] == [1]


def test_list_download_history_respects_limit(conn: sqlite3.Connection) -> None:
    for i in range(5):
        record_download(conn, 1, f"{i}--novel", "epub", "done", 1, None)

    assert len(list_download_history(conn, 1, limit=3)) == 3


def test_list_download_history_empty(conn: sqlite3.Connection) -> None:
    assert list_download_history(conn, 1) == []


def test_list_download_history_today_includes_todays_entry(conn: sqlite3.Connection) -> None:
    record_download(conn, 1, "6712--test-novel", "epub", "done", 1, None)

    entries = list_download_history_today(conn, 1)

    assert [entry.slug_url for entry in entries] == ["6712--test-novel"]


def test_list_download_history_today_excludes_yesterday(conn: sqlite3.Connection) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO download_history "
        "(user_id, slug_url, fmt, status, chapter_count, error, finished_at) "
        "VALUES (1, '6712--old-novel', 'epub', 'done', 1, NULL, ?)",
        (yesterday,),
    )
    conn.commit()

    assert list_download_history_today(conn, 1) == []


def test_list_download_history_today_excludes_other_users(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    record_download(conn, 2, "6712--test-novel", "epub", "done", 1, None)

    assert list_download_history_today(conn, 1) == []
