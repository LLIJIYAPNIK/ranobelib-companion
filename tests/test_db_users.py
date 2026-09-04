import psycopg
import pytest

from app.db.migrate import run_migrations
from app.db.users import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    update_notification_settings,
    update_privacy_settings,
    update_user_account,
    update_user_avatar,
)
from tests.db_reset import fresh_connection


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


async def test_update_user_account_nickname_need_not_be_unique(
    conn: psycopg.AsyncConnection,
) -> None:
    alice = await create_user(conn, "alice@example.com", "hash1")
    bob = await create_user(conn, "bob@example.com", "hash2")

    await update_user_account(conn, alice.id, email=alice.email, nickname="Same", bio=None)
    updated_bob = await update_user_account(
        conn, bob.id, email=bob.email, nickname="Same", bio=None
    )

    assert updated_bob.nickname == "Same"


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
