import psycopg
import pytest

from app.db.migrate import run_migrations
from app.db.reactions import (
    count_reactions,
    count_reactions_for_paragraph,
    toggle_reaction,
    user_reactions,
)
from tests.db_reset import fresh_connection


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    for user_id, email in ((1, "alice@example.com"), (2, "bob@example.com")):
        await connection.execute(
            "INSERT INTO users (id, email, password_hash, created_at) "
            "VALUES (%s, %s, 'hash', 'now')",
            (user_id, email),
        )
    return connection


async def test_toggle_reaction_sets_it(conn: psycopg.AsyncConnection) -> None:
    result = await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    assert result == "👍"
    counts = await count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert counts == {"👍": 1}


async def test_toggle_reaction_same_emoji_again_removes_it(conn: psycopg.AsyncConnection) -> None:
    await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    result = await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    assert result is None
    assert await count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0) == {}


async def test_toggle_reaction_different_emoji_switches_instead_of_accumulating(
    conn: psycopg.AsyncConnection,
) -> None:
    await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    result = await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "🔥")

    assert result == "🔥"
    counts = await count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert counts == {"🔥": 1}


async def test_count_reactions_groups_by_paragraph_and_emoji(conn: psycopg.AsyncConnection) -> None:
    await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")
    await toggle_reaction(conn, 2, "6712--test-novel", "1", "5", "", 0, "👍")
    await toggle_reaction(conn, 2, "6712--test-novel", "1", "5", "", 3, "🔥")

    counts = await count_reactions(conn, "6712--test-novel", "1", "5", "")

    assert counts == {0: {"👍": 2}, 3: {"🔥": 1}}


async def test_count_reactions_is_scoped_to_branch_id(conn: psycopg.AsyncConnection) -> None:
    await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "1", 0, "👍")

    assert await count_reactions(conn, "6712--test-novel", "1", "5", "2") == {}
    assert await count_reactions(conn, "6712--test-novel", "1", "5", "1") == {0: {"👍": 1}}


async def test_user_reactions_reports_each_users_own_pick(conn: psycopg.AsyncConnection) -> None:
    await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")
    await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 2, "🔥")
    await toggle_reaction(conn, 2, "6712--test-novel", "1", "5", "", 0, "😂")

    assert await user_reactions(conn, 1, "6712--test-novel", "1", "5", "") == {0: "👍", 2: "🔥"}
    assert await user_reactions(conn, 2, "6712--test-novel", "1", "5", "") == {0: "😂"}


async def test_reactions_do_not_leak_across_chapters(conn: psycopg.AsyncConnection) -> None:
    await toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    assert await count_reactions(conn, "6712--test-novel", "1", "6", "") == {}
    assert await count_reactions(conn, "6712--other-novel", "1", "5", "") == {}
