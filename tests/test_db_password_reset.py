"""PR 225: app/db/password_reset.py, the "Забыли пароль?" flow's own token table."""

import psycopg
import pytest

from app.db.migrate import run_migrations
from app.db.password_reset import create_token, get_valid_token, mark_token_used
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


async def test_create_token_returns_a_usable_raw_token(conn: psycopg.AsyncConnection) -> None:
    raw_token = await create_token(conn, user_id=1, ttl_seconds=3600)

    reset_token = await get_valid_token(conn, raw_token)

    assert reset_token is not None
    assert reset_token.user_id == 1
    assert reset_token.used_at is None


async def test_only_the_tokens_hash_is_stored(conn: psycopg.AsyncConnection) -> None:
    raw_token = await create_token(conn, user_id=1, ttl_seconds=3600)

    cursor = await conn.execute("SELECT token_hash FROM password_reset_tokens")
    row = await cursor.fetchone()

    assert row["token_hash"] != raw_token


async def test_get_valid_token_rejects_an_unknown_token(conn: psycopg.AsyncConnection) -> None:
    reset_token = await get_valid_token(conn, "not-a-real-token")

    assert reset_token is None


async def test_get_valid_token_rejects_an_expired_token(conn: psycopg.AsyncConnection) -> None:
    raw_token = await create_token(conn, user_id=1, ttl_seconds=-1)

    reset_token = await get_valid_token(conn, raw_token)

    assert reset_token is None


async def test_mark_token_used_makes_the_token_rejected_on_reuse(
    conn: psycopg.AsyncConnection,
) -> None:
    raw_token = await create_token(conn, user_id=1, ttl_seconds=3600)
    reset_token = await get_valid_token(conn, raw_token)
    assert reset_token is not None

    await mark_token_used(conn, reset_token.id)

    assert await get_valid_token(conn, raw_token) is None


async def test_each_created_token_is_independent(conn: psycopg.AsyncConnection) -> None:
    first_token = await create_token(conn, user_id=1, ttl_seconds=3600)
    second_token = await create_token(conn, user_id=1, ttl_seconds=3600)
    first = await get_valid_token(conn, first_token)
    assert first is not None

    await mark_token_used(conn, first.id)

    # Using the first (now-used) token doesn't invalidate the second, still-unused one.
    assert await get_valid_token(conn, second_token) is not None
