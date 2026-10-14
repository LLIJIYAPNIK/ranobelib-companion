"""PR 348: «Пользователи» - the list (search, filters, sort, pages), the drawer, and the
three actions: each needs the admin login and the CSRF token, does what it says with the
site's own functions, and leaves an entry in the action log - nothing when it's refused."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.admin_users import safe_back
from app.config import get_settings
from app.db import admin_audit
from app.db.admin_users import PAGE_SIZE
from app.db.connection import connection
from app.db.users import get_user_by_email
from tests.auth_helpers import register, sent_emails
from tests.test_admin_auth import PASSWORD, Clock, _client, _csrf, _login

SECRET_HASH = "SECRET-HASH-users-7f3a"


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


def _user_client() -> TestClient:
    """A second browser on the same app - the site user's own cookies."""
    from app.main import app

    return TestClient(app)


async def _add_user(
    email: str,
    nickname: str | None = None,
    verified: bool = True,
    days_ago: float = 30,
    read_hours_ago: float | None = None,
    library: int = 0,
) -> int:
    at = datetime.now(UTC) - timedelta(days=days_ago)
    async with connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO users (email, password_hash, created_at, nickname, email_verified_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (email, SECRET_HASH, at.isoformat(), nickname, at.isoformat() if verified else None),
        )
        user_id = (await cursor.fetchone())["id"]
        if read_hours_ago is not None:
            read_at = datetime.now(UTC) - timedelta(hours=read_hours_ago)
            await conn.execute(
                "INSERT INTO activity_events (user_id, kind, slug_url, seconds, created_at) "
                "VALUES (%s, 'heartbeat', 't', 600, %s)",
                (user_id, read_at.isoformat()),
            )
        for i in range(library):
            await conn.execute(
                "INSERT INTO library_entries (user_id, slug_url, added_at) VALUES (%s, %s, %s)",
                (user_id, f"{i}--t", at.isoformat()),
            )
    return user_id


def _emails(html: str) -> list[str]:
    return re.findall(r'data-label="Email"[^>]*>([^<]+@example\.com)<', html)


async def _log() -> list[admin_audit.AuditEntry]:
    async with connection() as conn:
        return await admin_audit.list_entries(conn)


# --- the list -----------------------------------------------------------------------------


async def test_search_by_email_nickname_or_id(client: TestClient) -> None:
    alice = await _add_user("alice@example.com", "aster")
    await _add_user("bob@example.com", "bramble")
    _login(client)

    assert _emails(client.get("/admin/users", params={"q": "ALICE"}).text) == ["alice@example.com"]
    assert _emails(client.get("/admin/users", params={"q": "bram"}).text) == ["bob@example.com"]
    assert "alice@example.com" in _emails(client.get("/admin/users", params={"q": str(alice)}).text)
    # Nothing in the search acts as a wildcard.
    none = client.get("/admin/users", params={"q": "%"}).text
    assert _emails(none) == [] and "Ничего не найдено" in none


async def test_filters(client: TestClient) -> None:
    await _add_user("reader@example.com", read_hours_ago=2, library=2)
    await _add_user("idle@example.com", verified=False)
    _login(client)

    def emails(**params: str) -> list[str]:
        return _emails(client.get("/admin/users", params=params).text)

    assert emails(verified="no") == ["idle@example.com"]
    assert emails(verified="yes") == ["reader@example.com"]
    assert emails(active="yes") == ["reader@example.com"]
    assert emails(active="no") == ["idle@example.com"]
    assert emails(library="yes") == ["reader@example.com"]
    assert emails(library="no") == ["idle@example.com"]
    assert sorted(emails(verified="maybe")) == ["idle@example.com", "reader@example.com"]


async def test_sort_whitelist_and_pages(client: TestClient) -> None:
    for i in range(PAGE_SIZE + 2):
        await _add_user(f"u{i:02d}@example.com", days_ago=i + 1)
    _login(client)

    first = _emails(client.get("/admin/users").text)  # newest id first
    second = _emails(client.get("/admin/users", params={"page": 2}).text)
    by_email = _emails(client.get("/admin/users", params={"sort": "email", "order": "asc"}).text)
    evil = client.get("/admin/users", params={"sort": "password_hash", "order": "asc"})

    assert len(first) == PAGE_SIZE and len(second) == 2
    assert first[0] == f"u{PAGE_SIZE + 1:02d}@example.com"
    assert by_email[0] == "u00@example.com"
    assert evil.status_code == 200 and SECRET_HASH not in evil.text


async def test_no_secrets_on_the_page(client: TestClient) -> None:
    user = await _add_user("alice@example.com")
    _login(client)

    html = client.get("/admin/users", params={"user": user}).text

    assert "alice@example.com" in html
    assert SECRET_HASH not in html
    assert "session_version" not in html


# --- the drawer ---------------------------------------------------------------------------


async def test_the_drawer(client: TestClient) -> None:
    user = await _add_user("alice@example.com", "aster", read_hours_ago=1, library=3)
    _login(client)

    html = client.get("/admin/users", params={"user": user, "q": "ali"}).text

    assert 'id="admin-user"' in html and "data-kit-autoopen" in html
    assert 'data-kit-close-href="/admin/users?q=ali"' in html
    assert re.search(r"<dt>В библиотеке</dt><dd>3</dd>", html)
    assert re.search(r"<dt>Время чтения</dt><dd>10 мин</dd>", html)
    assert "Подтвердить email вручную" not in html  # already confirmed
    # The actions come back to the same list with the same drawer open.
    back = re.search(r'name="back" value="([^"]+)"', html).group(1)
    assert back.startswith("/admin/users?") and f"user={user}" in back and "q=ali" in back


async def test_an_unknown_user_in_the_address(client: TestClient) -> None:
    _login(client)

    html = client.get("/admin/users", params={"user": 999999}).text

    assert "Пользователь не найден" in html and "data-kit-autoopen" not in html


# --- the actions --------------------------------------------------------------------------


def _post(client: TestClient, path: str, **data: str):
    return client.post(path, data=data, follow_redirects=False)


async def test_ending_sessions_signs_the_user_out_everywhere(client: TestClient) -> None:
    user_browser = _user_client()
    register(user_browser, "alice@example.com", "hunter2pass", nickname="aster")
    assert "alice@example.com" in user_browser.get("/settings/account").text
    async with connection() as conn:
        alice = await get_user_by_email(conn, "alice@example.com")
    _login(client)

    response = _post(
        client,
        f"/admin/users/{alice.id}/sessions",
        csrf=_csrf(client, "/admin/users"),
        back=f"/admin/users?user={alice.id}",
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/users?user={alice.id}"
    assert "alice@example.com" not in user_browser.get("/settings/account").text
    [entry] = [e for e in await _log() if e.action == admin_audit.USER_END_SESSIONS]
    assert (entry.entity, entry.entity_id, entry.entity_label) == ("user", str(alice.id), "aster")
    assert entry.ip
    assert "Все сессии «aster» завершены" in client.get("/admin/users").text  # the toast


async def test_confirming_an_email_by_hand(client: TestClient) -> None:
    user = await _add_user("late@example.com", verified=False)
    _login(client)
    csrf = _csrf(client, "/admin/users")

    _post(client, f"/admin/users/{user}/verify-email", csrf=csrf)
    again = _post(client, f"/admin/users/{user}/verify-email", csrf=csrf)

    async with connection() as conn:
        assert (await get_user_by_email(conn, "late@example.com")).email_verified_at
    entries = [e for e in await _log() if e.action == admin_audit.USER_CONFIRM_EMAIL]
    assert len(entries) == 1  # the second time changed nothing and logged nothing
    assert entries[0].details["before"] == {"email_verified_at": None}
    assert entries[0].details["after"]["email_verified_at"]
    assert again.status_code == 303
    assert "уже подтверждён" in client.get("/admin/users").text


async def test_sending_a_password_reset(client: TestClient) -> None:
    user = await _add_user("alice@example.com")
    _login(client)
    sent_emails.clear()

    response = _post(
        client, f"/admin/users/{user}/password-reset", csrf=_csrf(client, "/admin/users")
    )

    [letter] = sent_emails
    assert letter.to == "alice@example.com" and "/password-reset/" in letter.body
    token = re.search(r"/password-reset/(\S+)", letter.body).group(1)
    [entry] = [e for e in await _log() if e.action == admin_audit.USER_SEND_PASSWORD_RESET]
    assert entry.details["sent_to"] == "alice@example.com"
    assert token not in str(entry.details)
    page = client.get(response.headers["location"]).text
    assert token not in page and "Письмо для сброса пароля отправлено" in page
    async with connection() as conn:  # the password itself is untouched
        assert (await get_user_by_email(conn, "alice@example.com")).password_hash == SECRET_HASH


@pytest.mark.parametrize("action", ["sessions", "verify-email", "password-reset"])
async def test_actions_need_the_csrf_token(client: TestClient, action: str) -> None:
    user = await _add_user("alice@example.com", verified=False)
    _login(client)
    sent_emails.clear()

    response = _post(client, f"/admin/users/{user}/{action}", csrf="forged")

    assert response.status_code == 403
    assert sent_emails == []
    assert [e for e in await _log() if e.action.startswith("user_")] == []
    async with connection() as conn:
        alice = await get_user_by_email(conn, "alice@example.com")
    assert alice.email_verified_at is None and alice.session_version == 1


@pytest.mark.parametrize("action", ["sessions", "verify-email", "password-reset"])
async def test_actions_need_the_admin(client: TestClient, action: str) -> None:
    """Not logged in as admin - even as a signed-in site user - nothing happens."""
    user = await _add_user("alice@example.com", verified=False)
    register(client, "mallory@example.com", "hunter2pass")
    sent_emails.clear()

    response = _post(client, f"/admin/users/{user}/{action}", csrf="whatever")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert sent_emails == []
    assert [e for e in await _log() if e.action.startswith("user_")] == []


async def test_an_unknown_user_is_a_404(client: TestClient) -> None:
    _login(client)

    response = _post(client, "/admin/users/999999/sessions", csrf=_csrf(client, "/admin/users"))

    assert response.status_code == 404


async def test_back_never_leaves_the_users_list(client: TestClient) -> None:
    user = await _add_user("alice@example.com")
    _login(client)

    response = _post(
        client,
        f"/admin/users/{user}/sessions",
        csrf=_csrf(client, "/admin/users"),
        back="https://evil.example/phish",
    )

    assert response.headers["location"] == "/admin/users"


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("/admin/users?user=3&q=a", "/admin/users?user=3&q=a"),
        ("/admin/users", "/admin/users"),
        ("/admin/tables", "/admin/users"),
        ("//evil.example/admin/users", "/admin/users"),
        ("/admin/users?next=//evil.example", "/admin/users"),
        ("/admin/usersX", "/admin/users"),
        ("", "/admin/users"),
        (None, "/admin/users"),
    ],
)
def test_safe_back(raw: str | None, safe: str) -> None:
    assert safe_back(raw) == safe
