"""app/db/email_verification.py (PR 246) - link token and 6-digit code for one row."""

import psycopg
import pytest

from app.db.email_verification import (
    MAX_FAILED_CODE_ATTEMPTS,
    check_code,
    create_token,
    get_valid_token,
    mark_token_used,
)
from app.db.migrate import run_migrations
from app.db.users import create_user
from tests.db_reset import fresh_connection

TTL = 3600.0


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    return connection


@pytest.fixture
async def user_id(conn: psycopg.AsyncConnection) -> int:
    return (await create_user(conn, "alice@example.com", "hash")).id


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


async def test_issued_code_is_six_digits(conn: psycopg.AsyncConnection, user_id: int) -> None:
    issued = await create_token(conn, user_id, TTL)

    assert len(issued.code) == 6 and issued.code.isdigit()


async def test_link_token_resolves_to_its_user(conn: psycopg.AsyncConnection, user_id: int) -> None:
    issued = await create_token(conn, user_id, TTL)

    token = await get_valid_token(conn, issued.token)

    assert token is not None and token.user_id == user_id


async def test_only_hashes_are_stored(conn: psycopg.AsyncConnection, user_id: int) -> None:
    issued = await create_token(conn, user_id, TTL)

    cursor = await conn.execute("SELECT token_hash, code_hash FROM email_verification_tokens")
    row = await cursor.fetchone()
    assert row is not None
    assert issued.token not in row.values()
    assert issued.code not in row.values()


async def test_unknown_link_token_is_rejected(conn: psycopg.AsyncConnection, user_id: int) -> None:
    await create_token(conn, user_id, TTL)

    assert await get_valid_token(conn, "not-a-real-token") is None


async def test_expired_token_is_rejected_by_link_and_code(
    conn: psycopg.AsyncConnection, user_id: int
) -> None:
    issued = await create_token(conn, user_id, -1)

    assert await get_valid_token(conn, issued.token) is None
    assert await check_code(conn, user_id, issued.code) is None


async def test_correct_code_resolves_and_ignores_spaces(
    conn: psycopg.AsyncConnection, user_id: int
) -> None:
    issued = await create_token(conn, user_id, TTL)

    token = await check_code(conn, user_id, f" {issued.code[:3]} {issued.code[3:]} ")

    assert token is not None and token.user_id == user_id


async def test_code_only_works_for_its_own_user(
    conn: psycopg.AsyncConnection, user_id: int
) -> None:
    other_id = (await create_user(conn, "bob@example.com", "hash")).id
    issued = await create_token(conn, user_id, TTL)

    assert await check_code(conn, other_id, issued.code) is None


async def test_using_the_code_also_consumes_the_link(
    conn: psycopg.AsyncConnection, user_id: int
) -> None:
    issued = await create_token(conn, user_id, TTL)
    token = await check_code(conn, user_id, issued.code)
    assert token is not None

    await mark_token_used(conn, token.id)

    assert await get_valid_token(conn, issued.token) is None
    assert await check_code(conn, user_id, issued.code) is None


async def test_using_the_link_also_consumes_the_code(
    conn: psycopg.AsyncConnection, user_id: int
) -> None:
    issued = await create_token(conn, user_id, TTL)
    token = await get_valid_token(conn, issued.token)
    assert token is not None

    await mark_token_used(conn, token.id)

    assert await check_code(conn, user_id, issued.code) is None


async def test_code_locks_after_too_many_wrong_attempts(
    conn: psycopg.AsyncConnection, user_id: int
) -> None:
    issued = await create_token(conn, user_id, TTL)
    for _ in range(MAX_FAILED_CODE_ATTEMPTS):
        assert await check_code(conn, user_id, _wrong(issued.code)) is None

    # Even the right code no longer works - a new email is needed.
    assert await check_code(conn, user_id, issued.code) is None
    # The link is a long random token, not guessable - it stays valid.
    assert await get_valid_token(conn, issued.token) is not None


async def test_new_email_invalidates_the_previous_one(
    conn: psycopg.AsyncConnection, user_id: int
) -> None:
    first = await create_token(conn, user_id, TTL)
    second = await create_token(conn, user_id, TTL)

    assert await get_valid_token(conn, first.token) is None
    assert await check_code(conn, user_id, first.code) is None
    assert await check_code(conn, user_id, second.code) is not None
