import sqlite3

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
    assert comment.user_id == 1
    assert comment.avatar_url is None
    assert comment.avatar_initials == "AL"


def test_create_comment_avatar_url_reflects_an_uploaded_avatar(
    conn: sqlite3.Connection,
) -> None:
    conn.execute("UPDATE users SET avatar_path = '1.png' WHERE id = 1")
    conn.commit()

    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    assert comment.avatar_url == "/avatars/1.png"


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


def test_create_comment_allows_an_empty_body_when_theres_an_attachment(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(
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


def test_create_comment_without_an_attachment_has_no_attachment_url(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "hi")

    assert comment.attachment_path is None
    assert comment.attachment_kind is None
    assert comment.attachment_url is None


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


def test_list_comments_includes_the_attachment(conn: sqlite3.Connection) -> None:
    create_comment(
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

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)

    assert roots[0].attachment_url == "/comment-attachments/abc123.mp4"
    assert roots[0].attachment_kind == "gif"


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


def test_count_comments_by_user_is_zero_for_a_user_with_none(
    conn: sqlite3.Connection,
) -> None:
    assert count_comments_by_user(conn, 1) == 0


def test_count_comments_by_user_counts_across_titles_and_paragraphs(
    conn: sqlite3.Connection,
) -> None:
    create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "a")
    create_comment(conn, 1, "6712--test-novel", "1", "5", "", 3, "b")
    create_comment(conn, 1, "9000--other-novel", "2", "1", "", 0, "c")
    create_comment(conn, 2, "6712--test-novel", "1", "5", "", 0, "not alice's")

    assert count_comments_by_user(conn, 1) == 3
    assert count_comments_by_user(conn, 2) == 1


def test_edit_comment_overwrites_the_body(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    assert edit_comment(conn, comment.id, 1, "updated") is True

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == "updated"
    assert roots[0].updated_at is not None


def test_edit_comment_strips_the_body(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    edit_comment(conn, comment.id, 1, "  updated  ")

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == "updated"


def test_edit_comment_rejects_someone_elses_comment(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    assert edit_comment(conn, comment.id, 2, "hijacked") is False

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == "original"


def test_edit_comment_reports_false_for_a_nonexistent_comment(
    conn: sqlite3.Connection,
) -> None:
    assert edit_comment(conn, 999, 1, "updated") is False


def test_edit_comment_rejects_an_empty_body_without_an_attachment(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    with pytest.raises(ValueError):
        edit_comment(conn, comment.id, 1, "   ")


def test_edit_comment_allows_an_empty_body_when_theres_an_attachment(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(
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

    assert edit_comment(conn, comment.id, 1, "   ") is True

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].body == ""


def test_edit_comment_rejects_a_body_over_the_length_limit(
    conn: sqlite3.Connection,
) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")

    with pytest.raises(ValueError):
        edit_comment(conn, comment.id, 1, "x" * (MAX_COMMENT_LENGTH + 1))


def test_edit_comment_rejects_an_already_deleted_comment(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "original")
    delete_comment(conn, comment.id, 1)

    assert edit_comment(conn, comment.id, 1, "resurrected") is False


def test_delete_comment_blanks_the_body_and_attachment(conn: sqlite3.Connection) -> None:
    comment = create_comment(
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

    assert delete_comment(conn, comment.id, 1) is True

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].is_deleted is True
    assert roots[0].body == ""
    assert roots[0].attachment_url is None
    assert roots[0].updated_at is not None


def test_delete_comment_rejects_someone_elses_comment(conn: sqlite3.Connection) -> None:
    comment = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "goodbye")

    assert delete_comment(conn, comment.id, 2) is False

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].is_deleted is False
    assert roots[0].body == "goodbye"


def test_delete_comment_reports_false_for_a_nonexistent_comment(
    conn: sqlite3.Connection,
) -> None:
    assert delete_comment(conn, 999, 1) is False


def test_delete_comment_keeps_its_replies_in_the_tree(conn: sqlite3.Connection) -> None:
    root = create_comment(conn, 1, "6712--test-novel", "1", "5", "", 0, "root")
    create_comment(
        conn, 2, "6712--test-novel", "1", "5", "", 0, "reply", parent_comment_id=root.id
    )

    delete_comment(conn, root.id, 1)

    roots = list_comments_for_paragraph(conn, "6712--test-novel", "1", "5", "", 0)
    assert roots[0].is_deleted is True
    assert len(roots[0].replies) == 1
    assert roots[0].replies[0].body == "reply"
