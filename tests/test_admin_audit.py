"""PR 343: the admin action log - append-only storage, no secrets in it, the login events,
the record_change() helper for later admin actions, and retention."""

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

import app.db.admin_audit as admin_audit
from app.config import get_settings
from app.db.admin_data import MASK
from app.db.migrate import run_migrations
from tests.db_reset import TEST_DATABASE_URL, fresh_connection, run_async
from tests.test_admin_auth import PASSWORD, Clock, _client, _csrf, _login
from tests.test_admin_routes import _routes

# --- storage ------------------------------------------------------------------------------


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection = await fresh_connection()
    await run_migrations(connection)
    return connection


async def _insert_at(conn: psycopg.AsyncConnection, at: datetime, action: str = "old") -> None:
    await conn.execute(
        "INSERT INTO admin_audit_log (at, action) VALUES (%s, %s)", (at.isoformat(), action)
    )


async def test_an_entry_is_written_with_utc_time_and_the_admin_as_actor(
    conn: psycopg.AsyncConnection,
) -> None:
    entry_id = await admin_audit.record_admin_action(
        conn, "user_block", ip="203.0.113.5", entity="user", entity_id=42, entity_label="aster"
    )

    [entry] = await admin_audit.list_entries(conn)
    assert entry.id == entry_id
    assert (entry.actor, entry.action, entry.ip) == ("admin", "user_block", "203.0.113.5")
    assert (entry.entity, entry.entity_id, entry.entity_label) == ("user", "42", "aster")
    assert entry.details == {}
    at = datetime.fromisoformat(entry.at)
    assert at.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - at) < timedelta(minutes=1)


async def test_secrets_never_reach_details(conn: psycopg.AsyncConnection) -> None:
    await admin_audit.record_admin_action(
        conn,
        "user_edit",
        ip=None,
        details={
            "password": "hunter2",
            "csrf": "abc",
            "before": {"nickname": "a", "password_hash": "$2b$...", "session_version": 3},
            "rows": [{"token_hash": "f00", "email": "a@example.com"}],
            "nested": {"deeper": {"SESSION_SECRET_KEY": "k", "admin_password": "p"}},
        },
    )

    cursor = await conn.execute("SELECT details::text AS raw FROM admin_audit_log")
    raw = (await cursor.fetchone())["raw"]
    for secret in ("hunter2", "abc", "$2b$", "f00", '"k"', '"p"'):
        assert secret not in raw, secret
    details = json.loads(raw)
    assert details["password"] == MASK
    assert details["before"] == {"nickname": "a", "password_hash": MASK, "session_version": MASK}
    assert details["rows"] == [{"token_hash": MASK, "email": "a@example.com"}]
    assert details["nested"]["deeper"] == {"SESSION_SECRET_KEY": MASK, "admin_password": MASK}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE admin_audit_log SET action = 'nothing happened'",
        "UPDATE admin_audit_log SET details = '{}'::jsonb",
        "DELETE FROM admin_audit_log",
    ],
)
async def test_entries_cannot_be_edited_or_deleted(
    conn: psycopg.AsyncConnection, statement: str
) -> None:
    await admin_audit.record_admin_action(conn, "login", ip="198.51.100.1")

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        await conn.execute(statement)

    [entry] = await admin_audit.list_entries(conn)
    assert entry.action == "login"


def test_no_route_can_edit_or_delete_the_log() -> None:
    from app.main import app

    for route in _routes(app.routes):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if "audit" in path:
            assert methods <= {"GET", "HEAD"}, (path, methods)
        if path.startswith("/admin"):
            assert not methods & {"PUT", "PATCH", "DELETE"}, (path, methods)


# --- record_change() ----------------------------------------------------------------------


def test_diff_keeps_only_what_changed() -> None:
    before = {"nickname": "a", "blocked": False, "bio": "x"}
    after = {"nickname": "a", "blocked": True, "bio": "y"}

    assert admin_audit.diff(before, after) == {
        "before": {"blocked": False, "bio": "x"},
        "after": {"blocked": True, "bio": "y"},
    }
    assert admin_audit.diff({"a": 1}, {"a": 1}) == {"before": {}, "after": {}}
    assert admin_audit.diff({"a": 1}, {"b": 2}) == {
        "before": {"a": 1, "b": None},
        "after": {"a": None, "b": 2},
    }
    assert admin_audit.diff(None, {"a": 1}) == {"before": {}, "after": {"a": 1}}
    assert admin_audit.diff({"a": 1}, None) == {"before": {"a": 1}, "after": {}}


async def test_record_change_stores_the_diff_and_extra_context(
    conn: psycopg.AsyncConnection,
) -> None:
    await admin_audit.record_change(
        conn,
        "user_block",
        ip="203.0.113.5",
        entity="user",
        entity_id=7,
        entity_label="aster",
        before={"blocked": False, "nickname": "aster", "password_hash": "old"},
        after={"blocked": True, "nickname": "aster", "password_hash": "new"},
        extra={"reason": "спам"},
    )

    [entry] = await admin_audit.list_entries(conn)
    assert entry.details == {
        "before": {"blocked": False, "password_hash": MASK},
        "after": {"blocked": True, "password_hash": MASK},
        "reason": "спам",
    }
    assert (entry.entity, entry.entity_id) == ("user", "7")


# --- retention ----------------------------------------------------------------------------


async def test_purge_removes_only_what_is_past_the_retention(
    conn: psycopg.AsyncConnection,
) -> None:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    await _insert_at(conn, now - timedelta(days=400), "very old")
    await _insert_at(conn, now - timedelta(days=366), "just past")
    await _insert_at(conn, now - timedelta(days=364), "just inside")
    await _insert_at(conn, now - timedelta(hours=1), "fresh")

    removed = await admin_audit.purge_expired(conn, 365, now=now)

    assert removed == 2
    assert [entry.action for entry in await admin_audit.list_entries(conn)] == [
        "fresh",
        "just inside",
    ]
    # The cleanup's permission ended with its transaction.
    with pytest.raises(psycopg.errors.RaiseException):
        await conn.execute("DELETE FROM admin_audit_log")


async def test_retention_loop_cleans_up_at_once_and_then_daily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    @asynccontextmanager
    async def fake_connection():
        yield object()

    async def fake_purge(conn: object, days: int) -> int:
        calls.append(days)
        if len(calls) == 1:
            raise psycopg.OperationalError("db down")  # a failed round doesn't stop it
        return 3

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(admin_audit.db_connection, "connection", fake_connection)
    monkeypatch.setattr(admin_audit, "purge_expired", fake_purge)
    monkeypatch.setattr(admin_audit.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await admin_audit.retention_loop(30)

    assert calls == [30, 30]
    assert sleeps == [24 * 60 * 60, 24 * 60 * 60]


@pytest.mark.parametrize(
    ("value", "expected"), [("", 365), ("90", 90), ("0", 365), ("-5", 365), ("year", 365)]
)
def test_retention_setting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str, expected: int
) -> None:
    monkeypatch.setenv("ADMIN_AUDIT_RETENTION_DAYS", value)
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="app.config"):
            assert get_settings().admin_audit_retention_days == expected
    finally:
        get_settings.cache_clear()
    assert ("ADMIN_AUDIT_RETENTION_DAYS" in caplog.text) == (value not in ("", "90"))


# --- the login events, through the app ----------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        yield test_client
    get_settings.cache_clear()


def _log() -> list[dict]:
    async def fetch() -> list[dict]:
        async with await psycopg.AsyncConnection.connect(
            TEST_DATABASE_URL, row_factory=dict_row
        ) as conn:
            cursor = await conn.execute(
                "SELECT action, actor, ip, entity, details, details::text AS raw "
                "FROM admin_audit_log ORDER BY id"
            )
            return await cursor.fetchall()

    return run_async(fetch())


def test_a_login_and_logout_are_logged_with_the_ip(client: TestClient) -> None:
    assert _login(client).status_code == 303
    token = _csrf(client)
    client.post("/admin/logout", data={"csrf": token})

    entries = _log()
    assert [entry["action"] for entry in entries] == ["login", "logout"]
    for entry in entries:
        assert entry["actor"] == "admin"
        assert entry["ip"] == "testclient"  # TestClient's peer - what throttling counts
        assert entry["entity"] is None
        assert PASSWORD not in entry["raw"]


def test_failed_logins_are_logged_but_not_the_password_or_locked_attempts(
    client: TestClient,
) -> None:
    for attempt in range(4):
        _login(client, password=f"wrong-guess-{attempt}")
    _login(client, password="wrong-guess-while-locked")

    entries = _log()
    assert [entry["action"] for entry in entries] == ["login_failed"] * 4
    assert [entry["details"] for entry in entries[:3]] == [{}, {}, {}]
    assert entries[3]["details"] == {"locked_for_seconds": 15}
    assert not any("wrong-guess" in entry["raw"] for entry in entries)


def test_a_logout_without_an_admin_session_is_not_logged(client: TestClient) -> None:
    client.post("/admin/logout", data={"csrf": _csrf(client)})

    assert _log() == []


def test_the_login_works_when_the_log_cannot_be_written(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(*args: object, **kwargs: object) -> int:
        raise psycopg.OperationalError("database is down")

    monkeypatch.setattr(admin_audit, "record_admin_action", broken)

    with caplog.at_level(logging.ERROR, logger="app.api.admin"):
        assert _login(client).status_code == 303
    assert client.get("/admin/tables", follow_redirects=False).status_code == 200
    assert "Could not write the admin action log entry 'login'" in caplog.text
