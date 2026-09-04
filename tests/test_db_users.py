import asyncio

import psycopg
import pytest
from psycopg.rows import dict_row

from app.db.migrate import run_migrations
from app.db.users import (
    User,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_nickname,
    update_notification_settings,
    update_privacy_settings,
    update_user_account,
    update_user_avatar,
)
from tests.db_reset import TEST_DATABASE_URL, fresh_connection


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    return connection


async def test_run_migrations_is_idempotent(conn: psycopg.AsyncConnection) -> None:
    await run_migrations(conn)  # applied again by the fixture's own call - must not raise

    cursor = await conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    tables = {row["table_name"] for row in await cursor.fetchall()}
    assert "users" in tables


async def test_create_user_returns_the_stored_user(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hashed-password")

    assert user.id is not None
    assert user.email == "alice@example.com"
    assert user.password_hash == "hashed-password"


async def test_create_user_normalizes_email(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "  Alice@Example.COM  ", "hashed-password")

    assert user.email == "alice@example.com"


async def test_create_user_duplicate_email_raises(conn: psycopg.AsyncConnection) -> None:
    await create_user(conn, "alice@example.com", "hash1")

    with pytest.raises(psycopg.errors.UniqueViolation):
        await create_user(conn, "alice@example.com", "hash2")


async def test_create_user_duplicate_email_case_insensitive(conn: psycopg.AsyncConnection) -> None:
    await create_user(conn, "alice@example.com", "hash1")

    with pytest.raises(psycopg.errors.UniqueViolation):
        await create_user(conn, "ALICE@EXAMPLE.COM", "hash2")


async def test_get_user_by_email_found(conn: psycopg.AsyncConnection) -> None:
    created = await create_user(conn, "alice@example.com", "hash1")

    assert await get_user_by_email(conn, "Alice@Example.com") == created


async def test_get_user_by_email_missing(conn: psycopg.AsyncConnection) -> None:
    assert await get_user_by_email(conn, "nobody@example.com") is None


async def test_get_user_by_id_found(conn: psycopg.AsyncConnection) -> None:
    created = await create_user(conn, "alice@example.com", "hash1")

    assert await get_user_by_id(conn, created.id) == created


async def test_get_user_by_id_missing(conn: psycopg.AsyncConnection) -> None:
    assert await get_user_by_id(conn, 999) is None


async def test_create_user_has_no_nickname_or_bio_by_default(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    assert user.nickname is None
    assert user.bio is None


# --- PR 194: nickname uniqueness ---------------------------------------------------------


async def test_create_user_duplicate_nickname_raises(conn: psycopg.AsyncConnection) -> None:
    await create_user(conn, "alice@example.com", "hash1", "Nick")

    with pytest.raises(psycopg.errors.UniqueViolation):
        await create_user(conn, "bob@example.com", "hash2", "Nick")


async def test_create_user_nickname_race_exactly_one_succeeds(
    conn: psycopg.AsyncConnection,
) -> None:
    # Real concurrency (two separate connections, not two sequential calls on one) - the
    # unique index must be the actual source of truth, not just the pre-check both
    # registration routes run before calling create_user(). `conn` (already migrated by
    # the fixture) is one of the two; a second connection to the same test database
    # stands in for the second, concurrent registration request.
    conn2 = await psycopg.AsyncConnection.connect(
        TEST_DATABASE_URL, row_factory=dict_row, autocommit=True
    )
    try:
        results = await asyncio.gather(
            create_user(conn, "alice@example.com", "hash1", "Same"),
            create_user(conn2, "bob@example.com", "hash2", "Same"),
            return_exceptions=True,
        )
        successes = [r for r in results if isinstance(r, User)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], psycopg.errors.UniqueViolation)
    finally:
        await conn2.close()


async def test_get_user_by_nickname_found(conn: psycopg.AsyncConnection) -> None:
    created = await create_user(conn, "alice@example.com", "hash1", "Nick")

    assert await get_user_by_nickname(conn, "Nick") == created


async def test_get_user_by_nickname_missing(conn: psycopg.AsyncConnection) -> None:
    assert await get_user_by_nickname(conn, "Nobody") is None


async def test_update_user_account_sets_nickname_and_bio(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_user_account(
        conn, user.id, email="alice@example.com", nickname="Alice", bio="Hello there"
    )

    assert updated.nickname == "Alice"
    assert updated.bio == "Hello there"
    assert await get_user_by_id(conn, user.id) == updated


async def test_update_user_account_changes_email(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_user_account(
        conn, user.id, email="alice2@example.com", nickname=None, bio=None
    )

    assert updated.email == "alice2@example.com"


async def test_update_user_account_normalizes_email(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_user_account(
        conn, user.id, email="  Alice2@Example.COM  ", nickname=None, bio=None
    )

    assert updated.email == "alice2@example.com"


async def test_update_user_account_duplicate_email_raises(conn: psycopg.AsyncConnection) -> None:
    await create_user(conn, "alice@example.com", "hash1")
    bob = await create_user(conn, "bob@example.com", "hash2")

    with pytest.raises(psycopg.errors.UniqueViolation):
        await update_user_account(conn, bob.id, email="alice@example.com", nickname=None, bio=None)


async def test_update_user_account_duplicate_nickname_raises(
    conn: psycopg.AsyncConnection,
) -> None:
    # PR 194: nickname used to be freely reusable across accounts (see git history for
    # this test's previous version) - now a public-facing identity (profile/comments),
    # unlike a duplicate would let one account impersonate another's signature.
    alice = await create_user(conn, "alice@example.com", "hash1")
    bob = await create_user(conn, "bob@example.com", "hash2")
    await update_user_account(conn, alice.id, email=alice.email, nickname="Same", bio=None)

    with pytest.raises(psycopg.errors.UniqueViolation):
        await update_user_account(conn, bob.id, email=bob.email, nickname="Same", bio=None)


async def test_create_user_has_no_avatar_by_default(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    assert user.avatar_path is None


async def test_update_user_avatar_sets_the_path(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_user_avatar(conn, user.id, "1.png")

    assert updated.avatar_path == "1.png"
    assert await get_user_by_id(conn, user.id) == updated


async def test_update_user_avatar_leaves_other_fields_untouched(
    conn: psycopg.AsyncConnection,
) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")
    await update_user_account(conn, user.id, email=user.email, nickname="Alice", bio="Hi")

    updated = await update_user_avatar(conn, user.id, "1.png")

    assert updated.nickname == "Alice"
    assert updated.bio == "Hi"


async def test_create_user_shows_everything_by_default(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    assert user.show_currently_reading is True
    assert user.show_favorite is True
    assert user.show_library is True


async def test_update_privacy_settings_sets_all_three_flags(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_privacy_settings(
        conn,
        user.id,
        show_currently_reading=False,
        show_favorite=False,
        show_library=False,
    )

    assert updated.show_currently_reading is False
    assert updated.show_favorite is False
    assert updated.show_library is False
    assert await get_user_by_id(conn, user.id) == updated


async def test_update_privacy_settings_flags_are_independent(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_privacy_settings(
        conn, user.id, show_currently_reading=False, show_favorite=True, show_library=True
    )

    assert updated.show_currently_reading is False
    assert updated.show_favorite is True
    assert updated.show_library is True


async def test_update_privacy_settings_leaves_other_fields_untouched(
    conn: psycopg.AsyncConnection,
) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")
    await update_user_account(conn, user.id, email=user.email, nickname="Alice", bio="Hi")

    updated = await update_privacy_settings(
        conn, user.id, show_currently_reading=False, show_favorite=False, show_library=False
    )

    assert updated.nickname == "Alice"
    assert updated.bio == "Hi"


async def test_new_user_defaults_to_notifications_enabled_and_not_do_not_disturb(
    conn: psycopg.AsyncConnection,
) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    assert user.notifications_enabled is True
    assert user.do_not_disturb is False


async def test_update_notification_settings_sets_both_flags(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_notification_settings(
        conn, user.id, notifications_enabled=False, do_not_disturb=True
    )

    assert updated.notifications_enabled is False
    assert updated.do_not_disturb is True
    assert await get_user_by_id(conn, user.id) == updated


async def test_update_notification_settings_flags_are_independent(
    conn: psycopg.AsyncConnection,
) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    updated = await update_notification_settings(
        conn, user.id, notifications_enabled=True, do_not_disturb=True
    )

    assert updated.notifications_enabled is True
    assert updated.do_not_disturb is True
