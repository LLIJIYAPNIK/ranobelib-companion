from typing import Any

import psycopg
import pytest

from app.db.comments import create_comment
from app.db.migrate import run_migrations
from app.db.notifications import (
    KIND_COMMENT_REACTION,
    delete_notification,
    list_notifications_page,
    mark_notification_read,
    notify_comment_reaction,
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


def _notifications(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute("SELECT * FROM notifications").fetchall()


def test_notify_comment_reaction_creates_a_row_for_the_author(
    conn: psycopg.Connection,
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
    conn: psycopg.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    notify_comment_reaction(conn, comment.id, actor_user_id=1)

    assert _notifications(conn) == []


def test_notify_comment_reaction_is_a_noop_for_a_missing_comment(
    conn: psycopg.Connection,
) -> None:
    notify_comment_reaction(conn, 999, actor_user_id=2)

    assert _notifications(conn) == []


def test_notify_comment_reaction_updates_instead_of_duplicating_while_unread(
    conn: psycopg.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    first_created_at = _notifications(conn)[0]["created_at"]
    notify_comment_reaction(conn, comment.id, actor_user_id=2)

    rows = _notifications(conn)
    assert len(rows) == 1
    assert rows[0]["created_at"] >= first_created_at


def test_notify_comment_reaction_creates_a_new_row_once_the_old_one_is_read(
    conn: psycopg.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    conn.execute("UPDATE notifications SET is_read = 1")

    notify_comment_reaction(conn, comment.id, actor_user_id=2)

    assert len(_notifications(conn)) == 2


def test_list_notifications_page_paginates_newest_first(conn: psycopg.Connection) -> None:
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
    conn: psycopg.Connection,
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


def test_list_notifications_page_is_empty_with_none(conn: psycopg.Connection) -> None:
    page, has_next_page = list_notifications_page(conn, 1, page=1, page_size=2)

    assert page == []
    assert has_next_page is False


def test_mark_notification_read_flips_the_flag(conn: psycopg.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = _notifications(conn)[0]["id"]

    result = mark_notification_read(conn, notification_id, user_id=1)

    assert result is True
    assert _notifications(conn)[0]["is_read"] == 1


def test_mark_notification_read_is_a_noop_if_already_read(conn: psycopg.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = _notifications(conn)[0]["id"]
    mark_notification_read(conn, notification_id, user_id=1)

    result = mark_notification_read(conn, notification_id, user_id=1)

    assert result is True


def test_mark_notification_read_rejects_someone_elses_notification(
    conn: psycopg.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = _notifications(conn)[0]["id"]

    result = mark_notification_read(conn, notification_id, user_id=2)

    assert result is False
    assert _notifications(conn)[0]["is_read"] == 0


def test_mark_notification_read_reports_false_for_a_missing_id(
    conn: psycopg.Connection,
) -> None:
    assert mark_notification_read(conn, 999, user_id=1) is False


def test_delete_notification_removes_the_row(conn: psycopg.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = _notifications(conn)[0]["id"]

    result = delete_notification(conn, notification_id, user_id=1)

    assert result is True
    assert _notifications(conn) == []


def test_delete_notification_rejects_someone_elses_notification(
    conn: psycopg.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = _notifications(conn)[0]["id"]

    result = delete_notification(conn, notification_id, user_id=2)

    assert result is False
    assert len(_notifications(conn)) == 1


def test_delete_notification_reports_false_for_a_missing_id(
    conn: psycopg.Connection,
) -> None:
    assert delete_notification(conn, 999, user_id=1) is False
