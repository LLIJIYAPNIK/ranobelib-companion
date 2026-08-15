import sqlite3

import pytest

from app.db.library import (
    add_entry,
    get_entry,
    get_favorite_entry,
    list_entries,
    record_progress,
    remove_entry,
    set_favorite,
    unset_favorite,
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


def test_add_entry_returns_a_new_entry(conn: sqlite3.Connection) -> None:
    entry = add_entry(conn, 1, "6712--test-novel")

    assert entry.user_id == 1
    assert entry.slug_url == "6712--test-novel"
    assert entry.last_read_volume is None
    assert entry.last_read_number is None
    assert entry.last_read_at is None
    assert entry.is_favorite is False


def test_add_entry_is_idempotent(conn: sqlite3.Connection) -> None:
    first = add_entry(conn, 1, "6712--test-novel")
    second = add_entry(conn, 1, "6712--test-novel")

    assert first.id == second.id
    assert len(list_entries(conn, 1)) == 1


def test_remove_entry_deletes_it(conn: sqlite3.Connection) -> None:
    add_entry(conn, 1, "6712--test-novel")

    remove_entry(conn, 1, "6712--test-novel")

    assert get_entry(conn, 1, "6712--test-novel") is None


def test_remove_entry_missing_does_not_raise(conn: sqlite3.Connection) -> None:
    remove_entry(conn, 1, "does-not-exist")  # must not raise


def test_get_entry_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert get_entry(conn, 1, "does-not-exist") is None


def test_list_entries_orders_most_recent_first(conn: sqlite3.Connection) -> None:
    add_entry(conn, 1, "1--first")
    add_entry(conn, 1, "2--second")
    record_progress(conn, 1, "1--first", volume="1", number="5")  # read after adding both

    entries = list_entries(conn, 1)

    assert [entry.slug_url for entry in entries] == ["1--first", "2--second"]


def test_list_entries_only_returns_this_users_entries(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    add_entry(conn, 1, "6712--test-novel")
    add_entry(conn, 2, "6712--test-novel")

    assert [entry.user_id for entry in list_entries(conn, 1)] == [1]


def test_record_progress_updates_existing_entry(conn: sqlite3.Connection) -> None:
    add_entry(conn, 1, "6712--test-novel")

    record_progress(conn, 1, "6712--test-novel", volume="2", number="10")

    entry = get_entry(conn, 1, "6712--test-novel")
    assert entry.last_read_volume == "2"
    assert entry.last_read_number == "10"
    assert entry.last_read_at is not None


def test_record_progress_outside_library_is_a_noop(conn: sqlite3.Connection) -> None:
    record_progress(conn, 1, "6712--test-novel", volume="2", number="10")

    assert get_entry(conn, 1, "6712--test-novel") is None


def test_set_favorite_marks_the_entry(conn: sqlite3.Connection) -> None:
    add_entry(conn, 1, "6712--test-novel")

    set_favorite(conn, 1, "6712--test-novel")

    assert get_entry(conn, 1, "6712--test-novel").is_favorite is True


def test_set_favorite_clears_the_previous_favorite(conn: sqlite3.Connection) -> None:
    add_entry(conn, 1, "1--first")
    add_entry(conn, 1, "2--second")
    set_favorite(conn, 1, "1--first")

    set_favorite(conn, 1, "2--second")

    assert get_entry(conn, 1, "1--first").is_favorite is False
    assert get_entry(conn, 1, "2--second").is_favorite is True


def test_set_favorite_does_not_affect_other_users(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    add_entry(conn, 1, "6712--test-novel")
    add_entry(conn, 2, "6712--test-novel")
    set_favorite(conn, 1, "6712--test-novel")

    set_favorite(conn, 2, "6712--test-novel")

    assert get_entry(conn, 1, "6712--test-novel").is_favorite is True
    assert get_entry(conn, 2, "6712--test-novel").is_favorite is True


def test_unset_favorite_clears_it(conn: sqlite3.Connection) -> None:
    add_entry(conn, 1, "6712--test-novel")
    set_favorite(conn, 1, "6712--test-novel")

    unset_favorite(conn, 1, "6712--test-novel")

    assert get_entry(conn, 1, "6712--test-novel").is_favorite is False


def test_unset_favorite_missing_entry_does_not_raise(conn: sqlite3.Connection) -> None:
    unset_favorite(conn, 1, "does-not-exist")  # must not raise


def test_get_favorite_entry_returns_none_when_nothing_is_favorited(
    conn: sqlite3.Connection,
) -> None:
    add_entry(conn, 1, "6712--test-novel")

    assert get_favorite_entry(conn, 1) is None


def test_get_favorite_entry_returns_the_favorited_entry(conn: sqlite3.Connection) -> None:
    add_entry(conn, 1, "1--first")
    add_entry(conn, 1, "2--second")
    set_favorite(conn, 1, "2--second")

    favorite = get_favorite_entry(conn, 1)

    assert favorite is not None
    assert favorite.slug_url == "2--second"
