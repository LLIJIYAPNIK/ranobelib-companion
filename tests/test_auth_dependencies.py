import psycopg
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.dependencies import get_current_user, require_current_user
from app.db.migrate import run_migrations
from app.db.users import create_user
from tests.db_reset import fresh_connection


def _request_with_session(session: dict) -> Request:
    """A bare Request carrying `session` in its scope - what SessionMiddleware would set
    up, without needing a full app/middleware stack (see Request.session's own
    implementation: it just reads `scope["session"]`)."""
    return Request({"type": "http", "session": session})


@pytest.fixture
def conn(monkeypatch) -> psycopg.Connection:
    connection = fresh_connection()
    run_migrations(connection)
    monkeypatch.setattr("app.auth.dependencies.get_connection", lambda: connection)
    return connection


def test_get_current_user_no_session_returns_none(conn: psycopg.Connection) -> None:
    assert get_current_user(_request_with_session({})) is None


def test_get_current_user_valid_session_returns_user(conn: psycopg.Connection) -> None:
    user = create_user(conn, "alice@example.com", "hash1")

    found = get_current_user(_request_with_session({"user_id": user.id}))

    assert found == user


def test_get_current_user_stale_user_id_returns_none(conn: psycopg.Connection) -> None:
    assert get_current_user(_request_with_session({"user_id": 999})) is None


def test_require_current_user_passes_through_logged_in_user() -> None:
    from app.db.users import User

    user = User(id=1, email="alice@example.com", password_hash="h", created_at="now")

    assert require_current_user(user) is user


def test_require_current_user_redirects_anonymous_to_login() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_current_user(None)

    assert exc_info.value.status_code == 303
    assert exc_info.value.headers["Location"] == "/login"
