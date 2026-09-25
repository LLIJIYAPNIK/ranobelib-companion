import contextlib

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
    implementation: it just reads `scope["session"]`). Also seeds `request.state` (a
    plain SimpleNamespace-like object on the scope, same as Starlette's own Request.state)
    since get_current_user() writes to it directly."""
    request = Request({"type": "http", "session": session, "state": {}})
    return request


@pytest.fixture
async def conn(monkeypatch: pytest.MonkeyPatch) -> psycopg.AsyncConnection:
    """`get_current_user()` checks out its own connection via `connection()`
    (app/db/connection.py) rather than taking one as a parameter (see its own docstring on
    why) - patched here to hand back this fixture's already-migrated connection instead of
    checking one out of the real pool, the same isolation `fresh_connection()` gives every
    other db-layer unit test."""
    connection_obj = await fresh_connection()
    await run_migrations(connection_obj)

    @contextlib.asynccontextmanager
    async def fake_connection():
        yield connection_obj

    monkeypatch.setattr("app.auth.dependencies.connection", fake_connection)
    return connection_obj


async def test_get_current_user_no_session_returns_none(conn: psycopg.AsyncConnection) -> None:
    assert await get_current_user(_request_with_session({})) is None


async def test_get_current_user_valid_session_returns_user(conn: psycopg.AsyncConnection) -> None:
    user = await create_user(conn, "alice@example.com", "hash1")

    # A real session always carries both keys together (see app/api/auth.py's
    # login()/register(), which set them in the same two lines) - this is what a genuine
    # post-login session looks like, not just `user_id` on its own.
    found = await get_current_user(
        _request_with_session({"user_id": user.id, "session_version": user.session_version})
    )

    assert found == user


async def test_get_current_user_stale_user_id_returns_none(conn: psycopg.AsyncConnection) -> None:
    assert await get_current_user(_request_with_session({"user_id": 999})) is None


# --- PR 225: a session issued before a password reset is no longer honored -------------


async def test_get_current_user_stale_session_version_returns_none(
    conn: psycopg.AsyncConnection,
) -> None:
    """Simulates a session that was already open when the account's password got reset -
    its stashed `session_version` (still 1, the account's original value at the time it
    was issued) no longer matches the account's current one (bumped past 1 by the reset,
    see app/db/users.py's `update_user_password_from_reset()`)."""
    user = await create_user(conn, "alice@example.com", "hash1")

    found = await get_current_user(
        _request_with_session({"user_id": user.id, "session_version": user.session_version + 1})
    )

    assert found is None


async def test_get_current_user_missing_session_version_returns_none(
    conn: psycopg.AsyncConnection,
) -> None:
    """A session with no `session_version` key at all - e.g. one issued before this PR -
    is treated the same as a mismatched one, not specially trusted."""
    user = await create_user(conn, "alice@example.com", "hash1")

    found = await get_current_user(_request_with_session({"user_id": user.id}))

    assert found is None


async def test_require_current_user_passes_through_logged_in_user() -> None:
    from app.db.users import User

    user = User(id=1, email="alice@example.com", password_hash="h", created_at="now")

    assert require_current_user(user) is user


async def test_require_current_user_redirects_anonymous_to_login() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_current_user(None)

    assert exc_info.value.status_code == 303
    assert exc_info.value.headers["Location"] == "/login"
