import sqlite3

import pytest

from app.db.comments import create_comment
from app.db.migrate import run_migrations
from app.db.notifications import (
    KIND_COMMENT_REACTION,
    list_notifications_page,
    notify_comment_reaction,
)


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


def _notifications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM notifications").fetchall()


def test_notify_comment_reaction_creates_a_row_for_the_author(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    notify_comment_reaction(conn, comment.id, actor_user_id=2)

    rows = _notifications(conn)
    assert len(rows) == 1
    assert rows[0]["user_id"] == 1
    assert rows[0]["actor_user_id"] == 2
    assert rows[0]["comment_id"] == comment.id
    assert rows[0]["kind"] == KIND_COMMENT_REACTION
    assert rows[0]["is_read"] == 0


def test_notify_comment_reaction_skips_reacting_to_your_own_comment(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    notify_comment_reaction(conn, comment.id, actor_user_id=1)

    assert _notifications(conn) == []


def test_notify_comment_reaction_is_a_noop_for_a_missing_comment(
    conn: sqlite3.Connection,
) -> None:
    notify_comment_reaction(conn, 999, actor_user_id=2)

    assert _notifications(conn) == []


def test_notify_comment_reaction_updates_instead_of_duplicating_while_unread(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    first_created_at = _notifications(conn)[0]["created_at"]
    notify_comment_reaction(conn, comment.id, actor_user_id=2)

    rows = _notifications(conn)
    assert len(rows) == 1
    assert rows[0]["created_at"] >= first_created_at


def test_notify_comment_reaction_creates_a_new_row_once_the_old_one_is_read(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    conn.execute("UPDATE notifications SET is_read = 1")
    conn.commit()

    notify_comment_reaction(conn, comment.id, actor_user_id=2)

    assert len(_notifications(conn)) == 2


def test_list_notifications_page_paginates_newest_first(conn: sqlite3.Connection) -> None:
    comments = [
        create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, f"comment {i}")
        for i in range(3)
    ]
    for comment in comments:
        notify_comment_reaction(conn, comment.id, actor_user_id=2)

    page, has_next_page = list_notifications_page(conn, 1, page=1, page_size=2)

    assert [n.comment_id for n in page] == [comments[2].id, comments[1].id]
    assert has_next_page is True


def test_list_notifications_page_reports_no_next_page_on_the_last_page(
    conn: sqlite3.Connection,
) -> None:
    comments = [
        create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, f"comment {i}")
        for i in range(3)
    ]
    for comment in comments:
        notify_comment_reaction(conn, comment.id, actor_user_id=2)

    page, has_next_page = list_notifications_page(conn, 1, page=2, page_size=2)

    assert [n.comment_id for n in page] == [comments[0].id]
    assert has_next_page is False


def test_list_notifications_page_is_empty_with_none(conn: sqlite3.Connection) -> None:
    page, has_next_page = list_notifications_page(conn, 1, page=1, page_size=2)

    assert page == []
    assert has_next_page is False
