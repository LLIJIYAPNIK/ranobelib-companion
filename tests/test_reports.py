"""PR 356: readers' reports - storage (one open report per reporter and object, a closed
one doesn't block a new one, the 24-hour count) and POST /reports: who may report (a
guest, a blocked account and an unconfirmed email are refused in plain words), what (not
their own comment or profile, not a deleted, hidden or missing one), a repeat updating
instead of duplicating, and the daily limit."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.bans import ban_user
from app.db.comments import create_comment, delete_comment, hide_comment
from app.db.connection import connection
from app.db.migrate import run_migrations
from app.db.reports import file_report, open_report, reports_filed_last_day
from app.db.users import get_user_by_email
from app.reports import DAILY_LIMIT, DETAILS_LIMIT, REPORT_REASONS
from tests.auth_helpers import register
from tests.db_reset import fresh_connection, reset_app_database

NOW = datetime(2026, 3, 18, 12, tzinfo=UTC)

# --- storage ------------------------------------------------------------------------------


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection_ = await fresh_connection()
    await run_migrations(connection_)
    return connection_


async def _user(conn: psycopg.AsyncConnection, email: str) -> int:
    cursor = await conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (%s, 'x', %s) RETURNING id",
        (email, NOW.isoformat()),
    )
    return (await cursor.fetchone())["id"]


async def test_a_repeat_updates_the_open_report(conn: psycopg.AsyncConnection) -> None:
    alice, bob = await _user(conn, "alice@example.com"), await _user(conn, "bob@example.com")

    first, created = await file_report(conn, alice, "profile", bob, reason="spam", now=NOW)
    assert created and first.status == "open" and first.details is None
    again, created = await file_report(
        conn, alice, "profile", bob, reason="abuse", details="грубит", now=NOW + timedelta(1)
    )

    assert not created
    assert (again.id, again.reason, again.details) == (first.id, "abuse", "грубит")
    assert again.created_at == NOW.isoformat()
    cursor = await conn.execute("SELECT COUNT(*) AS n FROM reports")
    assert (await cursor.fetchone())["n"] == 1


async def test_a_closed_report_doesnt_block_a_new_one(conn: psycopg.AsyncConnection) -> None:
    alice, bob = await _user(conn, "alice@example.com"), await _user(conn, "bob@example.com")
    first, _ = await file_report(conn, alice, "profile", bob, reason="spam", now=NOW)
    await conn.execute("UPDATE reports SET status = 'dismissed' WHERE id = %s", (first.id,))

    assert await open_report(conn, alice, "profile", bob) is None
    second, created = await file_report(conn, alice, "profile", bob, reason="spam", now=NOW)
    assert created and second.id != first.id


async def test_reports_of_different_people_and_objects_are_separate(
    conn: psycopg.AsyncConnection,
) -> None:
    alice, bob = await _user(conn, "alice@example.com"), await _user(conn, "bob@example.com")
    carol = await _user(conn, "carol@example.com")

    assert (await file_report(conn, alice, "profile", carol, reason="spam"))[1]
    assert (await file_report(conn, bob, "profile", carol, reason="spam"))[1]
    assert (await file_report(conn, alice, "comment", carol, reason="spam"))[1]


async def test_the_daily_count_covers_the_last_24_hours(conn: psycopg.AsyncConnection) -> None:
    alice, bob = await _user(conn, "alice@example.com"), await _user(conn, "bob@example.com")
    await file_report(conn, alice, "profile", bob, reason="spam", now=NOW - timedelta(days=1))
    report, _ = await file_report(conn, alice, "comment", 1, reason="spam", now=NOW)
    # Closed by the admin - still filed today.
    await conn.execute("UPDATE reports SET status = 'dismissed' WHERE id = %s", (report.id,))
    await file_report(conn, bob, "profile", alice, reason="spam", now=NOW)

    assert await reports_filed_last_day(conn, alice, NOW) == 1
    assert await reports_filed_last_day(conn, alice, NOW + timedelta(days=1)) == 0


def test_the_reasons() -> None:
    assert list(REPORT_REASONS.values()) == [
        "Спам или реклама",
        "Оскорбления",
        "Спойлер без пометки",
        "Нарушение авторских прав",
        "Другое",
    ]


# --- POST /reports ------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


async def _id(email: str) -> int:
    async with connection() as conn:
        user = await get_user_by_email(conn, email)
    assert user is not None
    return user.id


async def _comment(user_id: int, body: str = "текст") -> int:
    async with connection() as conn:
        comment = await create_comment(conn, user_id, "some-title", "1", "1", "", 0, body)
    return comment.id


def _report(client: TestClient, target_type: str, target_id: int, /, **fields: str):
    data = {"target_type": target_type, "target_id": str(target_id), "reason": "spam"}
    return client.post("/reports", data=data | fields)


async def _rows() -> list[dict]:
    async with connection() as conn:
        cursor = await conn.execute(
            "SELECT reporter_user_id, target_type, target_id, reason, details, status "
            "FROM reports ORDER BY id"
        )
        return await cursor.fetchall()


async def test_reporting_a_comment(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    comment = await _comment(bob)
    register(client, "alice@example.com")  # switches the session to Alice
    alice = await _id("alice@example.com")

    response = _report(client, "comment", comment, reason="abuse", details="  грубит  ")

    assert response.status_code == 201
    assert response.json() == {"status": "sent", "message": "Жалоба отправлена"}
    assert await _rows() == [
        {
            "reporter_user_id": alice,
            "target_type": "comment",
            "target_id": comment,
            "reason": "abuse",
            "details": "грубит",
            "status": "open",
        }
    ]


async def test_reporting_a_profile_never_names_the_reporter(client: TestClient) -> None:
    register(client, "bob@example.com", nickname="bob")
    bob = await _id("bob@example.com")
    register(client, "alice@example.com", nickname="alice")

    response = _report(client, "profile", bob)

    assert response.status_code == 201
    assert "alice" not in response.text
    assert len(await _rows()) == 1


async def test_a_repeat_is_not_a_duplicate(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    register(client, "alice@example.com")
    _report(client, "profile", bob)

    response = _report(client, "profile", bob, reason="other", details="подробнее")

    assert response.status_code == 200
    assert response.json() == {"status": "already_sent", "message": "Вы уже отправили жалобу"}
    rows = await _rows()
    assert [(row["reason"], row["details"]) for row in rows] == [("other", "подробнее")]


async def test_not_on_your_own_comment_or_profile(client: TestClient) -> None:
    register(client, "alice@example.com")
    alice = await _id("alice@example.com")
    comment = await _comment(alice)

    on_comment = _report(client, "comment", comment)
    on_profile = _report(client, "profile", alice)

    assert on_comment.status_code == on_profile.status_code == 400
    assert on_comment.json()["detail"] == "Нельзя пожаловаться на свой комментарий"
    assert on_profile.json()["detail"] == "Нельзя пожаловаться на свой профиль"
    assert await _rows() == []


async def test_not_on_a_hidden_deleted_or_missing_object(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    hidden, deleted = await _comment(bob), await _comment(bob)
    async with connection() as conn:
        await hide_comment(conn, hidden)
        await delete_comment(conn, deleted, bob)
    register(client, "alice@example.com")

    assert _report(client, "comment", hidden).status_code == 409
    assert _report(client, "comment", hidden).json()["detail"] == "Комментарий уже скрыт"
    assert _report(client, "comment", deleted).status_code == 404
    assert _report(client, "comment", 999_999).status_code == 404
    missing = _report(client, "profile", 999_999)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Пользователь не найден"
    assert await _rows() == []


async def test_a_guest_is_told_to_log_in(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    client.post("/logout")
    client.cookies.clear()

    response = _report(client, "profile", bob)

    assert response.status_code == 401
    assert response.json()["detail"] == "Войдите, чтобы отправить жалобу"
    assert await _rows() == []


async def test_an_unconfirmed_email_is_refused(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    register(client, "alice@example.com")
    async with connection() as conn:
        await conn.execute(
            "UPDATE users SET email_verified_at = NULL WHERE email = 'alice@example.com'"
        )

    response = _report(client, "profile", bob)

    assert response.status_code == 403
    assert response.json()["detail"] == "Подтвердите email, чтобы отправлять жалобы"
    assert await _rows() == []


async def test_a_blocked_account_is_refused(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    register(client, "alice@example.com")
    async with connection() as conn:
        await ban_user(conn, await _id("alice@example.com"), term="1d")

    response = _report(client, "profile", bob)

    assert response.status_code == 403
    assert response.json()["detail"] == "Аккаунт заблокирован"
    assert await _rows() == []


@pytest.mark.parametrize(
    ("fields", "detail"),
    [
        ({"reason": "boring"}, "Выберите причину жалобы"),
        ({"details": "я" * (DETAILS_LIMIT + 1)}, f"Пояснение длиннее {DETAILS_LIMIT} символов"),
    ],
)
async def test_an_unknown_reason_or_long_details(
    client: TestClient, fields: dict[str, str], detail: str
) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    register(client, "alice@example.com")

    response = _report(client, "profile", bob, **fields)

    assert response.status_code == 400
    assert response.json()["detail"] == detail
    assert await _rows() == []


async def test_details_at_the_limit_are_kept(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    register(client, "alice@example.com")

    response = _report(client, "profile", bob, details="я" * DETAILS_LIMIT)

    assert response.status_code == 201
    assert (await _rows())[0]["details"] == "я" * DETAILS_LIMIT


@pytest.mark.parametrize(
    "fields",
    [{"target_type": "title"}, {"target_id": "0"}, {"target_id": "abc"}],
)
async def test_a_malformed_target_is_refused(client: TestClient, fields: dict[str, str]) -> None:
    register(client, "alice@example.com")

    assert _report(client, "profile", 1, **fields).status_code == 422
    assert await _rows() == []


async def test_the_daily_limit(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    comments = [await _comment(bob) for _ in range(DAILY_LIMIT + 1)]
    register(client, "alice@example.com")

    for comment in comments[:DAILY_LIMIT]:
        assert _report(client, "comment", comment).status_code == 201
    over = _report(client, "comment", comments[DAILY_LIMIT])

    assert over.status_code == 429
    assert over.json()["detail"] == "Слишком много жалоб за сутки - попробуйте завтра"
    assert len(await _rows()) == DAILY_LIMIT
    # Changing the reason of one already open is still allowed.
    assert _report(client, "comment", comments[0], reason="other").status_code == 200
    # A day later the limit is free again.
    async with connection() as conn:
        await conn.execute(
            "UPDATE reports SET created_at = %s",
            ((datetime.now(UTC) - timedelta(days=1, seconds=1)).isoformat(),),
        )
    assert _report(client, "comment", comments[DAILY_LIMIT]).status_code == 201
