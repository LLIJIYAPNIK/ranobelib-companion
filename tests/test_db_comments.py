import psycopg
import pytest

from app.db.comments import (
    MAX_COMMENT_LENGTH,
    count_comments,
    count_comments_by_user,
    count_comments_for_paragraph,
    create_comment,
    delete_comment,
    edit_comment,
    list_comments_for_paragraph,
)
from app.db.migrate import run_migrations
from tests.db_reset import fresh_connection


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    for user_id, email, nickname in (
        (1, "alice@example.com", "Alice"),
        (2, "bob@example.com", None),
    ):
        await connection.execute(
            "INSERT INTO users (id, email, password_hash, created_at, nickname) "
            "VALUES (%s, %s, 'hash', 'now', %s)",
            (user_id, email, nickname),
        )
    return connection


async def test_create_comment_returns_it_with_the_authors_nickname(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "Отличная глава!")

    assert comment.author == "Alice"
    assert comment.body == "Отличная глава!"
    assert comment.parent_comment_id is None
    assert comment.replies == []
    assert comment.user_id == 1
    assert comment.avatar_url is None
    assert comment.avatar_initials == "AL"


async def test_create_comment_avatar_url_reflects_an_uploaded_avatar(
    conn: psycopg.AsyncConnection,
) -> None:
    await conn.execute("UPDATE users SET avatar_path = '1.png' WHERE id = 1")

    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    assert comment.avatar_url == "/avatars/1.png"


async def test_create_comment_falls_back_to_email_without_a_nickname(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 2, "6712--test-novel", "1", "5", "", 0, "Согласен")

    assert comment.author == "bob@example.com"


async def test_create_comment_strips_the_body(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "  hi  ")

    assert comment.body == "hi"


async def test_create_comment_rejects_an_empty_body(conn: psycopg.AsyncConnection) -> None:
    with pytest.raises(ValueError):
        await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "   ")


async def test_create_comment_allows_an_empty_body_when_theres_an_attachment(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(
        conn,
        1,
        "6712--test-novel",
        "1",
        "5",
        "",
        0,
        "   ",
        attachment_path="abc123.mp4",
        attachment_kind="gif",
    )

    assert comment.body == ""
    assert comment.attachment_path == "abc123.mp4"
    assert comment.attachment_kind == "gif"
    assert comment.attachment_url == "/comment-attachments/abc123.mp4"


async def test_create_comment_without_an_attachment_has_no_attachment_url(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    assert comment.attachment_path is None
    assert comment.attachment_kind is None
    assert comment.attachment_url is None


async def test_create_comment_rejects_a_body_over_the_length_limit(
    conn: psycopg.AsyncConnection,
) -> None:
    with pytest.raises(ValueError):
        await create_comment(
            conn, 1, "6712--test-novel", "1", "5", "", 0, "x" * (MAX_COMMENT_LENGTH + 1)
        )


async def test_create_comment_rejects_a_parent_from_a_different_paragraph(
    conn: psycopg.AsyncConnection,
) -> None:
    root = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")

    with pytest.raises(ValueError):
        await create_comment(
            conn, 2, "6712--test-novel", "1", "5", "", 3, "reply", parent_comment_id=root.id
        )


async def test_create_comment_rejects_a_nonexistent_parent(conn: psycopg.AsyncConnection) -> None:
    with pytest.raises(ValueError):
        await create_comment(
            conn, 1, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=999
        )


async def test_list_comments_nests_replies_under_their_parent(
    conn: psycopg.AsyncConnection,
) -> None:
    root = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    reply = await create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )
    await create_comment(
        conn, 1, "6712--test-novel", "1", "5", "", 0, "reply to reply", parent_comment_id=reply.id
    )

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert len(roots) == 1
    assert roots[0].body == "root"
    assert len(roots[0].replies) == 1
    assert roots[0].replies[0].body == "reply"
    assert len(roots[0].replies[0].replies) == 1
    assert roots[0].replies[0].replies[0].body == "reply to reply"


async def test_list_comments_includes_the_attachment(conn: psycopg.AsyncConnection) -> None:
    await create_comment(
        conn,
        1,
        "6712--test-novel",
        "1",
        "5",
        "",
        0,
        "look at this",
        attachment_path="abc123.mp4",
        attachment_kind="gif",
    )

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert roots[0].attachment_url == "/comment-attachments/abc123.mp4"
    assert roots[0].attachment_kind == "gif"


async def test_list_comments_is_scoped_to_paragraph_and_branch(
    conn: psycopg.AsyncConnection,
) -> None:
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "here")
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 3, "elsewhere")
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "9", 0, "other branch")

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert [c.body for c in roots] == ["here"]


async def test_count_comments_for_paragraph_includes_replies(conn: psycopg.AsyncConnection) -> None:
    root = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    await create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )

    assert await count_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0) == 2


async def test_count_comments_groups_by_paragraph(conn: psycopg.AsyncConnection) -> None:
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "a")
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "b")
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 3, "c")

    assert await count_comments(conn, "6712--test-novel", "1", "5", "") == {0: 2, 3: 1}


async def test_count_comments_is_empty_for_a_chapter_with_none(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await count_comments(conn, "6712--test-novel", "1", "5", "") == {}


async def test_count_comments_by_user_is_zero_for_a_user_with_none(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await count_comments_by_user(conn, 1) == 0


async def test_count_comments_by_user_counts_across_titles_and_paragraphs(
    conn: psycopg.AsyncConnection,
) -> None:
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "a")
    await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 3, "b")
    await create_comment(conn, 1, "9000--other-novel", "2", "1", "", 0, "c")
    await create_comment(conn, 2, "6712--test-novel", "1", "5", "", 0, "not alice's")

    assert await count_comments_by_user(conn, 1) == 3
    assert await count_comments_by_user(conn, 2) == 1


async def test_edit_comment_overwrites_the_body(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    assert await edit_comment(conn, comment.id, 1, "updated") is True

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == "updated"
    assert roots[0].updated_at is not None


async def test_edit_comment_strips_the_body(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    await edit_comment(conn, comment.id, 1, "  updated  ")

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == "updated"


async def test_edit_comment_rejects_someone_elses_comment(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    assert await edit_comment(conn, comment.id, 2, "hijacked") is False

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == "original"


async def test_edit_comment_reports_false_for_a_nonexistent_comment(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await edit_comment(conn, 999, 1, "updated") is False


async def test_edit_comment_rejects_an_empty_body_without_an_attachment(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    with pytest.raises(ValueError):
        await edit_comment(conn, comment.id, 1, "   ")


async def test_edit_comment_allows_an_empty_body_when_theres_an_attachment(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(
        conn,
        1,
        "6712--test-novel",
        "1",
        "5",
        "",
        0,
        "caption",
        attachment_path="abc123.mp4",
        attachment_kind="gif",
    )

    assert await edit_comment(conn, comment.id, 1, "   ") is True

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == ""


async def test_edit_comment_rejects_a_body_over_the_length_limit(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    with pytest.raises(ValueError):
        await edit_comment(conn, comment.id, 1, "x" * (MAX_COMMENT_LENGTH + 1))


async def test_edit_comment_rejects_an_already_deleted_comment(
    conn: psycopg.AsyncConnection,
) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")
    await delete_comment(conn, comment.id, 1)

    assert await edit_comment(conn, comment.id, 1, "resurrected") is False


async def test_delete_comment_blanks_the_body_and_attachment(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(
        conn,
        1,
        "6712--test-novel",
        "1",
        "5",
        "",
        0,
        "goodbye",
        attachment_path="abc123.mp4",
        attachment_kind="gif",
    )

    assert await delete_comment(conn, comment.id, 1) is True

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].is_deleted is True
    assert roots[0].body == ""
    assert roots[0].attachment_url is None
    assert roots[0].updated_at is not None


async def test_delete_comment_rejects_someone_elses_comment(conn: psycopg.AsyncConnection) -> None:
    comment = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "goodbye")

    assert await delete_comment(conn, comment.id, 2) is False

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].is_deleted is False
    assert roots[0].body == "goodbye"


async def test_delete_comment_reports_false_for_a_nonexistent_comment(
    conn: psycopg.AsyncConnection,
) -> None:
    assert await delete_comment(conn, 999, 1) is False


async def test_delete_comment_keeps_its_replies_in_the_tree(conn: psycopg.AsyncConnection) -> None:
    root = await create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    await create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )

    await delete_comment(conn, root.id, 1)

    roots = await list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].is_deleted is True
    assert len(roots[0].replies) == 1
    assert roots[0].replies[0].body == "reply"
