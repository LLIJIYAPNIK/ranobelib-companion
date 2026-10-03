import shutil
from pathlib import Path

import psycopg
import pytest

import app.db.migrate
from app.db.library import (
    add_entry,
    get_currently_reading_entries,
    get_entry,
    library_slugs,
    list_entries,
    record_progress,
    remove_entry,
    set_default_translation_index,
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
    assert entry.default_translation_index is None
    assert entry.last_read_paragraph is None
    assert entry.last_read_paragraph_total is None


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


async def test_library_slugs_lists_only_this_users_titles(
    conn: psycopg.AsyncConnection,
) -> None:
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (2, 'bob@example.com', 'hash', 'now')"
    )
    assert await library_slugs(conn, 1) == set()
    await add_entry(conn, 1, "1--first")
    await add_entry(conn, 1, "2--second")
    await add_entry(conn, 2, "3--third")

    assert await library_slugs(conn, 1) == {"1--first", "2--second"}
    assert await library_slugs(conn, 2) == {"3--third"}


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


async def test_record_progress_stores_the_paragraph_position(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "6712--test-novel")

    await record_progress(conn, 1, "6712--test-novel", "2", "10", paragraph=50, paragraph_total=80)

    entry = await get_entry(conn, 1, "6712--test-novel")
    assert entry.last_read_paragraph == 50
    assert entry.last_read_paragraph_total == 80


async def test_record_progress_without_a_paragraph_keeps_it_for_the_same_chapter(
    conn: psycopg.AsyncConnection,
) -> None:
    # Reopening the chapter (GET, no paragraph known) must not wipe another device's
    # saved position in it.
    await add_entry(conn, 1, "6712--test-novel")
    await record_progress(conn, 1, "6712--test-novel", "2", "10", paragraph=50, paragraph_total=80)

    await record_progress(conn, 1, "6712--test-novel", "2", "10")

    entry = await get_entry(conn, 1, "6712--test-novel")
    assert entry.last_read_paragraph == 50
    assert entry.last_read_paragraph_total == 80


async def test_record_progress_without_a_paragraph_clears_it_for_another_chapter(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "6712--test-novel")
    await record_progress(conn, 1, "6712--test-novel", "2", "10", paragraph=50, paragraph_total=80)

    await record_progress(conn, 1, "6712--test-novel", "2", "11")

    entry = await get_entry(conn, 1, "6712--test-novel")
    assert entry.last_read_number == "11"
    assert entry.last_read_paragraph is None
    assert entry.last_read_paragraph_total is None


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


# --- PR 205: set_default_translation_index (перевод по умолчанию для тайтла) -----------


async def test_set_default_translation_index_stores_the_choice(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "6712--test-novel")

    await set_default_translation_index(conn, 1, "6712--test-novel", 2)

    assert (await get_entry(conn, 1, "6712--test-novel")).default_translation_index == 2


async def test_set_default_translation_index_none_clears_it(
    conn: psycopg.AsyncConnection,
) -> None:
    await add_entry(conn, 1, "6712--test-novel")
    await set_default_translation_index(conn, 1, "6712--test-novel", 2)

    await set_default_translation_index(conn, 1, "6712--test-novel", None)

    assert (await get_entry(conn, 1, "6712--test-novel")).default_translation_index is None


async def test_set_default_translation_index_outside_library_is_a_noop(
    conn: psycopg.AsyncConnection,
) -> None:
    await set_default_translation_index(conn, 1, "6712--test-novel", 2)  # must not raise

    assert await get_entry(conn, 1, "6712--test-novel") is None


async def test_set_default_translation_index_does_not_affect_other_users(
    conn: psycopg.AsyncConnection,
) -> None:
    await _add_user(conn, 2, "bob@example.com")
    await add_entry(conn, 1, "6712--test-novel")
    await add_entry(conn, 2, "6712--test-novel")

    await set_default_translation_index(conn, 1, "6712--test-novel", 2)

    assert (await get_entry(conn, 1, "6712--test-novel")).default_translation_index == 2
    assert (await get_entry(conn, 2, "6712--test-novel")).default_translation_index is None


async def test_read_paragraph_migration_keeps_existing_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A library row saved before 0024 must survive it untouched, with no paragraph
    # position - apply every migration up to 0023, insert a read entry, then 0024 on top.
    connection = await fresh_connection()
    source = Path(app.db.migrate.__file__).parent / "migrations"
    for path in sorted(source.glob("*.sql")):
        if path.name < "0024":
            shutil.copy(path, tmp_path / path.name)
    monkeypatch.setattr(app.db.migrate, "_MIGRATIONS_DIR", tmp_path)
    await run_migrations(connection)
    await connection.execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (1, 'alice@example.com', 'hash', 'now')"
    )
    # Raw SQL, not add_entry()/record_progress() - those read rows back through
    # _row_to_entry(), which already expects the columns 0024 hasn't added yet.
    await connection.execute(
        "INSERT INTO library_entries "
        "(user_id, slug_url, added_at, last_read_volume, last_read_number, last_read_at) "
        "VALUES (1, '6712--test-novel', 'then', '1', '5', 'then')"
    )

    shutil.copy(source / "0024_library_entries_read_paragraph.sql", tmp_path)
    await run_migrations(connection)

    entry = await get_entry(connection, 1, "6712--test-novel")
    assert entry is not None
    assert entry.last_read_volume == "1"
    assert entry.last_read_number == "5"
    assert entry.last_read_paragraph is None
    assert entry.last_read_paragraph_total is None
