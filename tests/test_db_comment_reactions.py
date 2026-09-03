import psycopg
import pytest

from app.db.comment_reactions import (
    count_reactions_for_comment,
    count_reactions_for_paragraph,
    toggle_comment_reaction,
    user_reactions_for_paragraph,
)
from app.db.comments import create_comment
from app.db.migrate import run_migrations
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


async def test_toggle_comment_reaction_sets_it(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    result = await toggle_comment_reaction(conn, 2, comment.id, 1)

    assert result == 1
    assert await count_reactions_for_comment(conn, comment.id) == {"like": 1, "dislike": 0}


async def test_toggle_comment_reaction_same_value_again_removes_it(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await toggle_comment_reaction(conn, 2, comment.id, 1)

    result = await toggle_comment_reaction(conn, 2, comment.id, 1)

    assert result is None
    assert await count_reactions_for_comment(conn, comment.id) == {"like": 0, "dislike": 0}


async def test_toggle_comment_reaction_opposite_value_switches_instead_of_accumulating(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await toggle_comment_reaction(conn, 2, comment.id, 1)

    result = await toggle_comment_reaction(conn, 2, comment.id, -1)

    assert result == -1
    assert await count_reactions_for_comment(conn, comment.id) == {"like": 0, "dislike": 1}


async def test_toggle_comment_reaction_rejects_a_nonexistent_comment(
    conn: psycopg.AsyncConnection,
) -> None:
    with pytest.raises(ValueError):
        await toggle_comment_reaction(conn, 1, 999, 1)


async def test_count_reactions_for_comment_is_empty_for_none(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    assert await count_reactions_for_comment(conn, comment.id) == {"like": 0, "dislike": 0}


async def test_count_reactions_for_paragraph_groups_by_comment(
    conn: psycopg.AsyncConnection,
) -> None:
    first = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "first")
    second = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "second")
    await toggle_comment_reaction(conn, 1, first.id, 1)
    await toggle_comment_reaction(conn, 2, first.id, 1)
    await toggle_comment_reaction(conn, 1, second.id, -1)

    counts = await count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert counts == {
        first.id: {"like": 2, "dislike": 0},
        second.id: {"like": 0, "dislike": 1},
    }


async def test_count_reactions_for_paragraph_includes_reply_comments(
    conn: psycopg.AsyncConnection,
) -> None:
    root = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    reply = await create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )
    await toggle_comment_reaction(conn, 1, reply.id, -1)

    counts = await count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert counts[reply.id] == {"like": 0, "dislike": 1}


async def test_user_reactions_for_paragraph_reports_each_users_own_pick(
    conn: psycopg.AsyncConnection,
) -> None:
    first = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "first")
    second = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "second")
    await toggle_comment_reaction(conn, 1, first.id, 1)
    await toggle_comment_reaction(conn, 1, second.id, -1)
    await toggle_comment_reaction(conn, 2, first.id, -1)

    assert await user_reactions_for_paragraph(conn, 1, "6712--test-novel", "1", "5", "", 0) == {
        first.id: 1,
        second.id: -1,
    }
    assert await user_reactions_for_paragraph(conn, 2, "6712--test-novel", "1", "5", "", 0) == {
        first.id: -1,
    }
