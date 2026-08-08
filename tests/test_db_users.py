import sqlite3

import pytest

from app.db.migrate import run_migrations
from app.db.users import create_user, get_user_by_email, get_user_by_id


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
