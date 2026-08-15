import sqlite3

import pytest

from app.db.comments import (
    MAX_COMMENT_LENGTH,
    count_comments,
    count_comments_for_paragraph,
    create_comment,
    list_comments_for_paragraph,
)
from app.db.migrate import run_migrations


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    for user_id, email, nickname in (
        (1, "alice@example.com", "Alice"),
        (2, "bob@example.com", None),
    ):
        connection.execute(
            "INSERT INTO users (id, email, password_hash, created_at, nickname) "
            "VALUES (?, ?, 'hash', 'now', ?)",
            (user_id, email, nickname),
        )
    connection.commit()
    return connection


def test_create_comment_returns_it_with_the_authors_nickname(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "Отличная глава!")

    assert comment.author == "Alice"
    assert comment.body == "Отличная глава!"
    assert comment.parent_comment_id is None
    assert comment.replies == []


def test_create_comment_falls_back_to_email_without_a_nickname(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 2, "6712--test-novel", "1", "5", "", 0, "Согласен")

    assert comment.author == "bob@example.com"


def test_create_comment_strips_the_body(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "  hi  ")

    assert comment.body == "hi"


def test_create_comment_rejects_an_empty_body(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "   ")


def test_create_comment_rejects_a_body_over_the_length_limit(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError):
        create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "x" * (MAX_COMMENT_LENGTH + 1))


def test_create_comment_rejects_a_parent_from_a_different_paragraph(
    conn: sqlite3.Connection,
) -> None:
    root = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")

    with pytest.raises(ValueError):
        create_comment(
            conn, 2, "6712--test-novel", "1", "5", "", 3, "reply", parent_comment_id=root.id
        )


def test_create_comment_rejects_a_nonexistent_parent(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        create_comment(
            conn, 1, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=999
        )


def test_list_comments_nests_replies_under_their_parent(conn: sqlite3.Connection) -> None:
    root = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    reply = create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )
    create_comment(
        conn, 1, "6712--test-novel", "1", "5", "", 0, "reply to reply", parent_comment_id=reply.id
    )

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert len(roots) == 1
    assert roots[0].body == "root"
    assert len(roots[0].replies) == 1
    assert roots[0].replies[0].body == "reply"
    assert len(roots[0].replies[0].replies) == 1
    assert roots[0].replies[0].replies[0].body == "reply to reply"


def test_list_comments_is_scoped_to_paragraph_and_branch(conn: sqlite3.Connection) -> None:
    create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "here")
    create_comment(conn, 1, "6712--test-novel", "1", "5", "", 3, "elsewhere")
    create_comment(conn, 1, "6712--test-novel", "1", "5", "9", 0, "other branch")

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert [c.body for c in roots] == ["here"]


def test_count_comments_for_paragraph_includes_replies(conn: sqlite3.Connection) -> None:
    root = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )

    assert count_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0) == 2


def test_count_comments_groups_by_paragraph(conn: sqlite3.Connection) -> None:
    create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "a")
    create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "b")
    create_comment(conn, 1, "6712--test-novel", "1", "5", "", 3, "c")

    assert count_comments(conn, "6712--test-novel", "1", "5", "") == {0: 2, 3: 1}


def test_count_comments_is_empty_for_a_chapter_with_none(conn: sqlite3.Connection) -> None:
    assert count_comments(conn, "6712--test-novel", "1", "5", "") == {}
