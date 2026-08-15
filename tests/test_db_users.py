import sqlite3

import pytest

from app.db.migrate import run_migrations
from app.db.users import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    update_privacy_settings,
    update_user_account,
    update_user_avatar,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    return connection


def test_run_migrations_is_idempotent(conn: sqlite3.Connection) -> None:
    run_migrations(conn)  # applied again by the fixture's own call - must not raise

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert "users" in tables


def test_create_user_returns_the_stored_user(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hashed-password")

    assert user.id is not None
    assert user.email == "alice@example.com"
    assert user.password_hash == "hashed-password"


def test_create_user_normalizes_email(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "  Alice@Example.COM  ", "hashed-password")

    assert user.email == "alice@example.com"


def test_create_user_duplicate_email_raises(conn: sqlite3.Connection) -> None:
    create_user(conn, "alice@example.com", "hash1")

    with pytest.raises(sqlite3.IntegrityError):
        create_user(conn, "alice@example.com", "hash2")


def test_create_user_duplicate_email_case_insensitive(conn: sqlite3.Connection) -> None:
    create_user(conn, "alice@example.com", "hash1")

    with pytest.raises(sqlite3.IntegrityError):
        create_user(conn, "ALICE@EXAMPLE.COM", "hash2")


def test_get_user_by_email_found(conn: sqlite3.Connection) -> None:
    created = create_user(conn, "alice@example.com", "hash1")

    assert get_user_by_email(conn, "Alice@Example.com") == created


def test_get_user_by_email_missing(conn: sqlite3.Connection) -> None:
    assert get_user_by_email(conn, "nobody@example.com") is None


def test_get_user_by_id_found(conn: sqlite3.Connection) -> None:
    created = create_user(conn, "alice@example.com", "hash1")

    assert get_user_by_id(conn, created.id) == created


def test_get_user_by_id_missing(conn: sqlite3.Connection) -> None:
    assert get_user_by_id(conn, 999) is None


def test_create_user_has_no_nickname_or_bio_by_default(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    assert user.nickname is None
    assert user.bio is None


def test_update_user_account_sets_nickname_and_bio(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    updated = update_user_account(
        conn, user.id, email="alice@example.com", nickname="Alice", bio="Hello there"
    )

    assert updated.nickname == "Alice"
    assert updated.bio == "Hello there"
    assert get_user_by_id(conn, user.id) == updated


def test_update_user_account_changes_email(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    updated = update_user_account(
        conn, user.id, email="alice2@example.com", nickname=None, bio=None
    )

    assert updated.email == "alice2@example.com"


def test_update_user_account_normalizes_email(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    updated = update_user_account(
        conn, user.id, email="  Alice2@Example.COM  ", nickname=None, bio=None
    )

    assert updated.email == "alice2@example.com"


def test_update_user_account_duplicate_email_raises(conn: sqlite3.Connection) -> None:
    create_user(conn, "alice@example.com", "hash1")
    bob = create_user(conn, "bob@example.com", "hash2")

    with pytest.raises(sqlite3.IntegrityError):
        update_user_account(conn, bob.id, email="alice@example.com", nickname=None, bio=None)


def test_update_user_account_nickname_need_not_be_unique(conn: sqlite3.Connection) -> None:
    alice = create_user(conn, "alice@example.com", "hash1")
    bob = create_user(conn, "bob@example.com", "hash2")

    update_user_account(conn, alice.id, email=alice.email, nickname="Same", bio=None)
    updated_bob = update_user_account(conn, bob.id, email=bob.email, nickname="Same", bio=None)

    assert updated_bob.nickname == "Same"


def test_create_user_has_no_avatar_by_default(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    assert user.avatar_path is None


def test_update_user_avatar_sets_the_path(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    updated = update_user_avatar(conn, user.id, "1.png")

    assert updated.avatar_path == "1.png"
    assert get_user_by_id(conn, user.id) == updated


def test_update_user_avatar_leaves_other_fields_untouched(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")
    update_user_account(conn, user.id, email=user.email, nickname="Alice", bio="Hi")

    updated = update_user_avatar(conn, user.id, "1.png")

    assert updated.nickname == "Alice"
    assert updated.bio == "Hi"


def test_create_user_shows_everything_by_default(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    assert user.show_currently_reading is True
    assert user.show_favorite is True
    assert user.show_library is True


def test_update_privacy_settings_sets_all_three_flags(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    updated = update_privacy_settings(
        conn,
        user.id,
        show_currently_reading=False,
        show_favorite=False,
        show_library=False,
    )

    assert updated.show_currently_reading is False
    assert updated.show_favorite is False
    assert updated.show_library is False
    assert get_user_by_id(conn, user.id) == updated


def test_update_privacy_settings_flags_are_independent(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    updated = update_privacy_settings(
        conn, user.id, show_currently_reading=False, show_favorite=True, show_library=True
    )

    assert updated.show_currently_reading is False
    assert updated.show_favorite is True
    assert updated.show_library is True


def test_update_privacy_settings_leaves_other_fields_untouched(conn: sqlite3.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")
    update_user_account(conn, user.id, email=user.email, nickname="Alice", bio="Hi")

    updated = update_privacy_settings(
        conn, user.id, show_currently_reading=False, show_favorite=False, show_library=False
    )

    assert updated.nickname == "Alice"
    assert updated.bio == "Hi"
