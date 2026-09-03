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
def conn() -> psycopg.Connection:
    connection = fresh_connection()
    run_migrations(connection)
    for user_id, email in ((1, "alice@example.com"), (2, "bob@example.com")):
        connection.execute(
            "INSERT INTO users (id, email, password_hash, created_at) "
            "VALUES (%s, %s, 'hash', 'now')",
            (user_id, email),
        )
    return connection


def test_toggle_reaction_sets_it(conn: psycopg.Connection) -> None:
    result = toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    assert result == "👍"
    assert count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0) == {"👍": 1}


def test_toggle_reaction_same_emoji_again_removes_it(conn: psycopg.Connection) -> None:
    toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    result = toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    assert result is None
    assert count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0) == {}


def test_toggle_reaction_different_emoji_switches_instead_of_accumulating(
    conn: psycopg.Connection,
) -> None:
    toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    result = toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "🔥")

    assert result == "🔥"
    assert count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0) == {"🔥": 1}


def test_count_reactions_groups_by_paragraph_and_emoji(conn: psycopg.Connection) -> None:
    toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")
    toggle_reaction(conn, 2, "6712--test-novel", "1", "5", "", 0, "👍")
    toggle_reaction(conn, 2, "6712--test-novel", "1", "5", "", 3, "🔥")

    counts = count_reactions(conn, "6712--test-novel", "1", "5", "")

    assert counts == {0: {"👍": 2}, 3: {"🔥": 1}}


def test_count_reactions_is_scoped_to_branch_id(conn: psycopg.Connection) -> None:
    toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "1", 0, "👍")

    assert count_reactions(conn, "6712--test-novel", "1", "5", "2") == {}
    assert count_reactions(conn, "6712--test-novel", "1", "5", "1") == {0: {"👍": 1}}


def test_user_reactions_reports_each_users_own_pick(conn: psycopg.Connection) -> None:
    toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")
    toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 2, "🔥")
    toggle_reaction(conn, 2, "6712--test-novel", "1", "5", "", 0, "😂")

    assert user_reactions(conn, 1, "6712--test-novel", "1", "5", "") == {0: "👍", 2: "🔥"}
    assert user_reactions(conn, 2, "6712--test-novel", "1", "5", "") == {0: "😂"}


def test_reactions_do_not_leak_across_chapters(conn: psycopg.Connection) -> None:
    toggle_reaction(conn, 1, "6712--test-novel", "1", "5", "", 0, "👍")

    assert count_reactions(conn, "6712--test-novel", "1", "6", "") == {}
    assert count_reactions(conn, "6712--other-novel", "1", "5", "") == {}
