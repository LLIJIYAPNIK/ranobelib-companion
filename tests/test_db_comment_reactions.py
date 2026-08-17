import sqlite3

import pytest

from app.db.comment_reactions import (
    count_reactions_for_comment,
    count_reactions_for_paragraph,
    toggle_comment_reaction,
    user_reactions_for_paragraph,
)
from app.db.comments import create_comment
from app.db.migrate import run_migrations


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    for user_id, email in ((1, "alice@example.com"), (2, "bob@example.com")):
        connection.execute(
            "INSERT INTO users (id, email, password_hash, created_at) "
            "VALUES (?, ?, 'hash', 'now')",
            (user_id, email),
        )
    connection.commit()
    return connection


def test_toggle_comment_reaction_sets_it(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    result = toggle_comment_reaction(conn, 2, comment.id, 1)

    assert result == 1
    assert count_reactions_for_comment(conn, comment.id) == {"like": 1, "dislike": 0}


def test_toggle_comment_reaction_same_value_again_removes_it(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    toggle_comment_reaction(conn, 2, comment.id, 1)

    result = toggle_comment_reaction(conn, 2, comment.id, 1)

    assert result is None
    assert count_reactions_for_comment(conn, comment.id) == {"like": 0, "dislike": 0}


def test_toggle_comment_reaction_opposite_value_switches_instead_of_accumulating(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    toggle_comment_reaction(conn, 2, comment.id, 1)

    result = toggle_comment_reaction(conn, 2, comment.id, -1)

    assert result == -1
    assert count_reactions_for_comment(conn, comment.id) == {"like": 0, "dislike": 1}


def test_toggle_comment_reaction_rejects_a_nonexistent_comment(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError):
        toggle_comment_reaction(conn, 1, 999, 1)


def test_count_reactions_for_comment_is_empty_for_none(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    assert count_reactions_for_comment(conn, comment.id) == {"like": 0, "dislike": 0}


def test_count_reactions_for_paragraph_groups_by_comment(conn: sqlite3.Connection) -> None:
    first = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "first")
    second = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "second")
    toggle_comment_reaction(conn, 1, first.id, 1)
    toggle_comment_reaction(conn, 2, first.id, 1)
    toggle_comment_reaction(conn, 1, second.id, -1)

    counts = count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert counts == {
        first.id: {"like": 2, "dislike": 0},
        second.id: {"like": 0, "dislike": 1},
    }


def test_count_reactions_for_paragraph_includes_reply_comments(
    conn: sqlite3.Connection,
) -> None:
    root = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    reply = create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )
    toggle_comment_reaction(conn, 1, reply.id, -1)

    counts = count_reactions_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert counts[reply.id] == {"like": 0, "dislike": 1}


def test_user_reactions_for_paragraph_reports_each_users_own_pick(
    conn: sqlite3.Connection,
) -> None:
    first = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "first")
    second = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "second")
    toggle_comment_reaction(conn, 1, first.id, 1)
    toggle_comment_reaction(conn, 1, second.id, -1)
    toggle_comment_reaction(conn, 2, first.id, -1)

    assert user_reactions_for_paragraph(conn, 1, "6712--test-novel", "1", "5", "", 0) == {
        first.id: 1,
        second.id: -1,
    }
    assert user_reactions_for_paragraph(conn, 2, "6712--test-novel", "1", "5", "", 0) == {
        first.id: -1,
    }
