"""PR 349: blocking an account - storage (one active block, terms that run out, lifting,
history), login refused, an open session turned into a guest with a notice, no way back
in by registering the same email again, and the admin actions with their log entries."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.ban_notice import ban_message
from app.config import get_settings
from app.db import admin_audit
from app.db.bans import (
    REASON_LIMIT,
    AlreadyBanned,
    active_ban,
    ban_history,
    ban_user,
    lift_ban,
)
from app.db.connection import connection
from app.db.migrate import run_migrations
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.db_reset import fresh_connection
from tests.test_admin_auth import PASSWORD, Clock, _client, _csrf, _login

NOW = datetime(2026, 3, 18, 12, tzinfo=UTC)

# --- storage ------------------------------------------------------------------------------


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection_ = await fresh_connection()
    await run_migrations(connection_)
    return connection_


async def _user(conn: psycopg.AsyncConnection, email: str = "alice@example.com") -> int:
    cursor = await conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (%s, 'x', %s) RETURNING id",
        (email, NOW.isoformat()),
    )
    return (await cursor.fetchone())["id"]


async def test_a_term_runs_out_by_itself(conn: psycopg.AsyncConnection) -> None:
    user = await _user(conn)
    ban = await ban_user(conn, user, term="7d", reason="спам", now=NOW)

    assert ban.expires_at == (NOW + timedelta(days=7)).isoformat()
    assert await active_ban(conn, user, NOW + timedelta(days=7) - timedelta(seconds=1))
    assert await active_ban(conn, user, NOW + timedelta(days=7)) is None
    # Ran out - lifting it is a no-op, and a new block can be set.
    assert await lift_ban(conn, user, now=NOW + timedelta(days=8)) is None
    assert await ban_user(conn, user, term="1d", now=NOW + timedelta(days=8))


async def test_forever_lasts_until_lifted(conn: psycopg.AsyncConnection) -> None:
    user = await _user(conn)
    ban = await ban_user(conn, user, term="forever", now=NOW)

    assert ban.permanent and ban.reason is None
    assert await active_ban(conn, user, NOW + timedelta(days=3650))
    lifted = await lift_ban(conn, user, reason="  ошибка  ", now=NOW + timedelta(days=1))
    assert (lifted.lifted_at, lifted.lifted_reason) == (
        (NOW + timedelta(days=1)).isoformat(),
        "ошибка",
    )
    assert await active_ban(conn, user, NOW + timedelta(days=2)) is None


async def test_one_active_block_at_a_time(conn: psycopg.AsyncConnection) -> None:
    user, other = await _user(conn), await _user(conn, "bob@example.com")
    await ban_user(conn, user, term="30d", now=NOW)

    with pytest.raises(AlreadyBanned):
        await ban_user(conn, user, term="forever", now=NOW)
    with pytest.raises(KeyError):
        await ban_user(conn, other, term="365d", now=NOW)
    assert await active_ban(conn, other, NOW) is None  # another account is untouched


async def test_history_keeps_every_block(conn: psycopg.AsyncConnection) -> None:
    user = await _user(conn)
    await ban_user(conn, user, term="1d", reason="первая", now=NOW)
    await lift_ban(conn, user, now=NOW + timedelta(hours=1))
    await ban_user(conn, user, term="7d", reason="x" * (REASON_LIMIT + 50), now=NOW)

    history = await ban_history(conn, user)

    assert [len(b.reason) for b in history] == [REASON_LIMIT, len("первая")]  # newest first
    assert history[1].lifted_at is not None


def test_the_message_says_term_and_reason_on_the_persons_clock() -> None:
    from app.db.bans import Ban

    ban = Ban(1, 1, "Спам в комментариях", NOW.isoformat(), "2026-03-25T09:00:00+00:00", None, None)
    forever = Ban(2, 1, None, NOW.isoformat(), None, None, None)

    assert ban_message(ban, "Europe/Moscow") == (
        "Аккаунт заблокирован до 25 марта 2026, 12:00 (Europe/Moscow). "
        "Причина: Спам в комментариях."
    )
    assert ban_message(ban, None).startswith("Аккаунт заблокирован до 25 марта 2026, 09:00 (UTC)")
    assert ban_message(forever, None) == "Аккаунт заблокирован бессрочно."


# --- the site -----------------------------------------------------------------------------


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


def _browser() -> TestClient:
    from app.main import app

    return TestClient(app)


async def _alice() -> int:
    async with connection() as conn:
        return (await get_user_by_email(conn, "alice@example.com")).id


async def _ban(user: int, term: str = "7d", reason: str | None = "спам", **kw) -> None:
    async with connection() as conn:
        await ban_user(conn, user, term=term, reason=reason, **kw)


def _log_in(browser: TestClient, password: str = "hunter2pass"):
    return browser.post(
        "/login",
        data={"email": "alice@example.com", "password": password},
        follow_redirects=False,
    )


async def test_a_blocked_account_cant_log_in(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    client.post("/logout")
    await _ban(await _alice(), reason="Спам")

    refused = _log_in(client)
    wrong = _log_in(client, "not-the-password")

    assert refused.status_code == 403
    assert "Аккаунт заблокирован до" in refused.text and "Причина: Спам." in refused.text
    # A wrong password gets the usual neutral answer - nothing about the block.
    assert wrong.status_code == 400 and "заблокирован" not in wrong.text
    assert "alice@example.com" not in client.get("/settings/account").text


async def test_an_open_session_becomes_a_guest_with_a_notice(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    assert "alice@example.com" in client.get("/settings/account", follow_redirects=False).text
    await _ban(await _alice(), term="forever", reason=None)

    needs_login = client.get("/notifications", follow_redirects=False)
    notice = client.get("/account-blocked")
    home = client.get("/")

    assert needs_login.status_code == 303
    assert needs_login.headers["location"] == "/account-blocked"
    assert notice.status_code == 403 and "Аккаунт заблокирован бессрочно." in notice.text
    assert 'class="ban-strip"' in home.text and "alice@example.com" not in home.text
    # Logging out still works and ends the notice.
    client.post("/logout")
    assert 'class="ban-strip"' not in client.get("/").text
    assert client.get("/account-blocked", follow_redirects=False).status_code == 303


async def test_after_lifting_or_running_out_the_same_session_is_back(
    client: TestClient,
) -> None:
    register(client, "alice@example.com", "hunter2pass")
    alice = await _alice()
    await _ban(alice)
    assert client.get("/notifications", follow_redirects=False).status_code == 303

    async with connection() as conn:
        await lift_ban(conn, alice)
    assert "alice@example.com" in client.get("/settings/account").text

    # A block whose term is already over doesn't count at all.
    await _ban(alice, term="1d", now=datetime.now(UTC) - timedelta(days=2))
    assert "alice@example.com" in client.get("/settings/account").text
    client.post("/logout")
    assert _log_in(client).status_code == 303


async def test_the_same_email_cant_register_again(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    client.post("/logout")
    await _ban(await _alice(), term="forever")

    response = _browser().post(
        "/register",
        data={
            "email": "Alice@Example.com",
            "password": "another-pass1",
            "password_confirm": "another-pass1",
        },
    )

    assert "Этот email уже зарегистрирован" in response.text
    async with connection() as conn:
        count = await conn.execute("SELECT COUNT(*) AS n FROM users")
        assert (await count.fetchone())["n"] == 1


# --- the admin actions --------------------------------------------------------------------


def _admin_post(client: TestClient, path: str, **data: str):
    return client.post(path, data=data, follow_redirects=False)


async def _entries(action: str) -> list[admin_audit.AuditEntry]:
    async with connection() as conn:
        return [e for e in await admin_audit.list_entries(conn) if e.action == action]


async def test_blocking_from_the_admin(client: TestClient) -> None:
    user_browser = _browser()
    register(user_browser, "alice@example.com", "hunter2pass", nickname="aster")
    alice = await _alice()
    _login(client)
    csrf = _csrf(client, "/admin/users")

    first = _admin_post(
        client, f"/admin/users/{alice}/ban", csrf=csrf, term="30d", reason="Спам", back=""
    )
    again = _admin_post(client, f"/admin/users/{alice}/ban", csrf=csrf, term="1d")

    assert first.status_code == again.status_code == 303
    assert (
        user_browser.get("/notifications", follow_redirects=False).headers["location"]
        == "/account-blocked"
    )
    [entry] = await _entries(admin_audit.USER_BAN)  # the second one changed nothing
    assert (entry.entity_id, entry.entity_label) == (str(alice), "aster")
    assert entry.details["before"] == {"banned": False, "expires_at": None}
    assert entry.details["after"]["banned"] is True and entry.details["after"]["expires_at"]
    assert (entry.details["term"], entry.details["reason"]) == ("30d", "Спам")
    drawer = client.get("/admin/users", params={"user": alice}).text
    assert "Снять блокировку" in drawer and "Причина: Спам" in drawer
    assert "заблокирован" in client.get("/admin/users").text


async def test_lifting_from_the_admin(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    client.post("/logout")
    alice = await _alice()
    await _ban(alice, term="forever")
    _login(client)
    csrf = _csrf(client, "/admin/users")

    _admin_post(client, f"/admin/users/{alice}/unban", csrf=csrf, reason="Ошиблись")
    _admin_post(client, f"/admin/users/{alice}/unban", csrf=csrf)  # nothing left to lift

    [entry] = await _entries(admin_audit.USER_UNBAN)
    assert entry.details["before"]["banned"] is True and entry.details["after"] == {"banned": False}
    assert entry.details["reason"] == "Ошиблись"
    assert _log_in(_browser()).status_code == 303
    assert "не заблокирован" in client.get("/admin/users").text  # the toast


async def test_an_unknown_term_blocks_nobody(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    alice = await _alice()
    _login(client)

    _admin_post(
        client, f"/admin/users/{alice}/ban", csrf=_csrf(client, "/admin/users"), term="365d"
    )

    async with connection() as conn:
        assert await active_ban(conn, alice) is None
    assert await _entries(admin_audit.USER_BAN) == []


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_blocking_needs_csrf_and_the_admin(client: TestClient, action: str) -> None:
    register(client, "alice@example.com", "hunter2pass")
    alice = await _alice()
    if action == "unban":
        await _ban(alice)

    as_site_user = _admin_post(client, f"/admin/users/{alice}/{action}", csrf="x", term="7d")
    _login(client)
    forged = _admin_post(client, f"/admin/users/{alice}/{action}", csrf="forged", term="7d")

    assert as_site_user.headers["location"] == "/admin/login"
    assert forged.status_code == 403
    async with connection() as conn:
        assert (await active_ban(conn, alice) is not None) == (action == "unban")
    assert not [e for e in await _entries(f"user_{action}")]
    assert re.search(r"user_(un)?ban", str(await _entries(admin_audit.LOGIN))) is None
