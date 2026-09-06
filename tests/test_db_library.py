import psycopg
import pytest

from app.db.library import (
    add_entry,
    get_currently_reading_entries,
    get_entry,
    get_favorite_entry,
    list_entries,
    record_progress,
    remove_entry,
    set_favorite,
    unset_favorite,
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


async def test_add_entry_returns_a_new_entry(conn: psycopg.AsyncConnection) -> None:
    entry = await add_entry(conn, 1, "6712--test-novel")

    assert entry.user_id == 1
    assert entry.slug_url == "6712--test-novel"
    assert entry.last_read_volume is None
    assert entry.last_read_number is None
    assert entry.last_read_at is None
    assert entry.is_favorite is False


async def test_add_entry_is_idempotent(conn: psycopg.AsyncConnection) -> None:
    first = await add_entry(conn, 1, "6712--test-novel")
    second = await add_entry(conn, 1, "6712--test-novel")

    assert first.id == second.id
    assert len(await list_entries(conn, 1)) == 1


async def test_remove_entry_deletes_it(conn: psycopg.AsyncConnection) -> None:
    await add_entry(conn, 1, "6712--test-novel")

    await remove_entry(conn, 1, "6712--test-novel")

    assert await get_entry(conn, 1, "6712--test-novel") is None


async def test_remove_entry_missing_does_not_raise(conn: psycopg.AsyncConnection) -> None:
    await remove_entry(conn, 1, "does-not-exist")  # must not raise


async def test_get_entry_missing_returns_none(conn: psycopg.AsyncConnection) -> None:
    assert await get_entry(conn, 1, "does-not-exist") is None


async def test_list_entries_orders_most_recent_first(conn: psycopg.AsyncConnection) -> None:
    await add_entry(conn, 1, "1--first")
    await add_entry(conn, 1, "2--second")
    await record_progress(conn, 1, "1--first", volume="1", number="5")  # read after adding both

    entries = await list_entries(conn, 1)

    assert [entry.slug_url for entry in entries] == ["1--first", "2--second"]


async def test_list_entries_only_returns_this_users_entries(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await add_entry(conn, 1, "6712--test-novel")
    await add_entry(conn, 2, "6712--test-novel")

    assert [entry.user_id for entry in await list_entries(conn, 1)] == [1]


async def test_record_progress_updates_existing_entry(conn: psycopg.AsyncConnection) -> None:
    await add_entry(conn, 1, "6712--test-novel")

    await record_progress(conn, 1, "6712--test-novel", volume="2", number="10")

    entry = await get_entry(conn, 1, "6712--test-novel")
    assert entry.last_read_volume == "2"
    assert entry.last_read_number == "10"
    assert entry.last_read_at is not None


async def test_record_progress_outside_library_is_a_noop(conn: psycopg.AsyncConnection) -> None:
    await record_progress(conn, 1, "6712--test-novel", volume="2", number="10")

    assert await get_entry(conn, 1, "6712--test-novel") is None


async def test_set_favorite_marks_the_entry(conn: psycopg.AsyncConnection) -> None:
    await add_entry(conn, 1, "6712--test-novel")

    await set_favorite(conn, 1, "6712--test-novel")

    assert (await get_entry(conn, 1, "6712--test-novel")).is_favorite is True


async def test_set_favorite_clears_the_previous_favorite(conn: psycopg.AsyncConnection) -> None:
    await add_entry(conn, 1, "1--first")
    await add_entry(conn, 1, "2--second")
    await set_favorite(conn, 1, "1--first")

    await set_favorite(conn, 1, "2--second")

    assert (await get_entry(conn, 1, "1--first")).is_favorite is False
    assert (await get_entry(conn, 1, "2--second")).is_favorite is True


async def test_set_favorite_does_not_affect_other_users(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    await add_entry(conn, 1, "6712--test-novel")
    await add_entry(conn, 2, "6712--test-novel")
    await set_favorite(conn, 1, "6712--test-novel")

    await set_favorite(conn, 2, "6712--test-novel")

    assert (await get_entry(conn, 1, "6712--test-novel")).is_favorite is True
    assert (await get_entry(conn, 2, "6712--test-novel")).is_favorite is True


async def test_unset_favorite_clears_it(conn: psycopg.AsyncConnection) -> None:
    await add_entry(conn, 1, "6712--test-novel")
    await set_favorite(conn, 1, "6712--test-novel")

    await unset_favorite(conn, 1, "6712--test-novel")

    assert (await get_entry(conn, 1, "6712--test-novel")).is_favorite is False


async def test_unset_favorite_missing_entry_does_not_raise(conn: psycopg.AsyncConnection) -> None:
    await unset_favorite(conn, 1, "does-not-exist")  # must not raise


async def test_get_favorite_entry_returns_none_when_nothing_is_favorited(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "6712--test-novel")

    assert await get_favorite_entry(conn, 1) is None


async def test_get_favorite_entry_returns_the_favorited_entry(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "1--first")
    await add_entry(conn, 1, "2--second")
    await set_favorite(conn, 1, "2--second")

    favorite = await get_favorite_entry(conn, 1)

    assert favorite is not None
    assert favorite.slug_url == "2--second"


# --- PR 200: get_currently_reading_entries (batched, friend-activity feed) --------------


async def _add_user(conn: psycopg.AsyncConnection, user_id: int, email: str) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) VALUES (%s, %s, 'hash', 'now')",
        (user_id, email),
    )


async def test_get_currently_reading_entries_empty_for_empty_input(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await get_currently_reading_entries(conn, []) == {}


async def test_get_currently_reading_entries_excludes_never_read_entries(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "6712--test-novel")  # added, never opened

    assert await get_currently_reading_entries(conn, [1]) == {}


async def test_get_currently_reading_entries_includes_a_read_entry(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "6712--test-novel")
    await record_progress(conn, 1, "6712--test-novel", volume="1", number="3")

    entries = await get_currently_reading_entries(conn, [1])

    assert entries[1].slug_url == "6712--test-novel"


async def test_get_currently_reading_entries_picks_the_most_recently_touched_entry(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "1--first")
    await record_progress(conn, 1, "1--first", volume="1", number="1")
    await add_entry(conn, 1, "2--second")
    await record_progress(conn, 1, "2--second", volume="1", number="1")  # read most recently

    entries = await get_currently_reading_entries(conn, [1])

    assert entries[1].slug_url == "2--second"


async def test_get_currently_reading_entries_omits_a_user_whose_top_entry_is_unread(
    conn: psycopg.AsyncConnection,
) -> None:
    """The top entry overall (most recently *added*, PR 200's own rule matching
    app/api/profile.py's currently_reading) has never been read - an older, already-read
    entry further down must not be surfaced instead."""
    await add_entry(conn, 1, "1--first")
    await record_progress(conn, 1, "1--first", volume="1", number="1")
    await add_entry(conn, 1, "2--second")  # added after, never opened

    assert await get_currently_reading_entries(conn, [1]) == {}


async def test_get_currently_reading_entries_covers_several_users_in_one_call(
    conn: psycopg.AsyncConnection,
) -> None:
    await _add_user(conn, 2, "bob@example.com")
    await add_entry(conn, 1, "1--first")
    await record_progress(conn, 1, "1--first", volume="1", number="1")
    await add_entry(conn, 2, "2--second")
    await record_progress(conn, 2, "2--second", volume="1", number="1")

    entries = await get_currently_reading_entries(conn, [1, 2])

    assert entries[1].slug_url == "1--first"
    assert entries[2].slug_url == "2--second"


async def test_get_currently_reading_entries_ignores_a_user_not_in_the_list(
    conn: psycopg.AsyncConnection,
) -> None:
    await _add_user(conn, 2, "bob@example.com")
    await add_entry(conn, 2, "2--second")
    await record_progress(conn, 2, "2--second", volume="1", number="1")

    assert await get_currently_reading_entries(conn, [1]) == {}
