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


async def _notifications(conn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    cursor = await conn.execute("SELECT * FROM notifications")
    return await cursor.fetchall()


async def test_notify_comment_reaction_creates_a_row_for_the_author(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    await notify_comment_reaction(conn, comment.id, actor_user_id=2)

    rows = await _notifications(conn)
    assert len(rows) == 1
    assert rows[0]["user_id"] == 1
    assert rows[0]["actor_user_id"] == 2
    assert rows[0]["comment_id"] == comment.id
    assert rows[0]["kind"] == KIND_COMMENT_REACTION
    assert rows[0]["is_read"] == 0


async def test_notify_comment_reaction_skips_reacting_to_your_own_comment(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    await notify_comment_reaction(conn, comment.id, actor_user_id=1)

    assert await _notifications(conn) == []


async def test_notify_comment_reaction_is_a_noop_for_a_missing_comment(
    conn: psycopg.AsyncConnection,
) -> None:
    await notify_comment_reaction(conn, 999, actor_user_id=2)

    assert await _notifications(conn) == []


async def test_notify_comment_reaction_updates_instead_of_duplicating_while_unread(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    await notify_comment_reaction(conn, comment.id, actor_user_id=2)
    first_created_at = (await _notifications(conn))[0]["created_at"]
    await notify_comment_reaction(conn, comment.id, actor_user_id=2)

    rows = await _notifications(conn)
    assert len(rows) == 1
    assert rows[0]["created_at"] >= first_created_at


async def test_notify_comment_reaction_creates_a_new_row_once_the_old_one_is_read(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await notify_comment_reaction(conn, comment.id, actor_user_id=2)
    await conn.execute("UPDATE notifications SET is_read = 1")

    await notify_comment_reaction(conn, comment.id, actor_user_id=2)

    assert len(await _notifications(conn)) == 2


async def test_list_notifications_page_paginates_newest_first(
    conn: psycopg.AsyncConnection,
) -> None:
    comments = [
        await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, f"comment {i}")
        for i in range(3)
    ]
    for comment in comments:
        await notify_comment_reaction(conn, comment.id, actor_user_id=2)

    page, has_next_page = await list_notifications_page(conn, 1, page=1, page_size=2)

    assert [n.comment_id for n in page] == [comments[2].id, comments[1].id]
    assert has_next_page is True


async def test_list_notifications_page_reports_no_next_page_on_the_last_page(
    conn: psycopg.AsyncConnection,
) -> None:
    comments = [
        await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, f"comment {i}")
        for i in range(3)
    ]
    for comment in comments:
        await notify_comment_reaction(conn, comment.id, actor_user_id=2)

    page, has_next_page = await list_notifications_page(conn, 1, page=2, page_size=2)

    assert [n.comment_id for n in page] == [comments[0].id]
    assert has_next_page is False


async def test_list_notifications_page_is_empty_with_none(conn: psycopg.AsyncConnection) -> None:
    page, has_next_page = await list_notifications_page(conn, 1, page=1, page_size=2)

    assert page == []
    assert has_next_page is False


async def test_mark_notification_read_flips_the_flag(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = (await _notifications(conn))[0]["id"]

    result = await mark_notification_read(conn, notification_id, user_id=1)

    assert result is True
    assert (await _notifications(conn))[0]["is_read"] == 1


async def test_mark_notification_read_is_a_noop_if_already_read(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = (await _notifications(conn))[0]["id"]
    await mark_notification_read(conn, notification_id, user_id=1)

    result = await mark_notification_read(conn, notification_id, user_id=1)

    assert result is True


async def test_mark_notification_read_rejects_someone_elses_notification(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = (await _notifications(conn))[0]["id"]

    result = await mark_notification_read(conn, notification_id, user_id=2)

    assert result is False
    assert (await _notifications(conn))[0]["is_read"] == 0


async def test_mark_notification_read_reports_false_for_a_missing_id(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await mark_notification_read(conn, 999, user_id=1) is False


async def test_delete_notification_removes_the_row(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = (await _notifications(conn))[0]["id"]

    result = await delete_notification(conn, notification_id, user_id=1)

    assert result is True
    assert await _notifications(conn) == []


async def test_delete_notification_rejects_someone_elses_notification(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")
    await notify_comment_reaction(conn, comment.id, actor_user_id=2)
    notification_id = (await _notifications(conn))[0]["id"]

    result = await delete_notification(conn, notification_id, user_id=2)

    assert result is False
    assert len(await _notifications(conn)) == 1


async def test_delete_notification_reports_false_for_a_missing_id(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await delete_notification(conn, 999, user_id=1) is False
