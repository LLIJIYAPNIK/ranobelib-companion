"""PR 325: the /admin overview and read-only table browser - whitelisted identifiers, no
SQL injection through the table/sort/search inputs, secrets never shown."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.admin_data import MASK, PAGE_SIZE, is_secret_column
from app.db.connection import connection
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.test_admin_auth import PASSWORD, Clock, _client, _login

SECRET_HASH = "SECRET-HASH-c0ffee-0123456789"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


async def _seed(client: TestClient) -> int:
    register(client, "alice@example.com", "hunter2pass")
    client.post("/logout")
    async with connection() as conn:
        user = await get_user_by_email(conn, "alice@example.com")
        assert user is not None
        await conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s", (SECRET_HASH, user.id)
        )
        await conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) "
            "VALUES (%s, %s, 'later', 'now')",
            (user.id, SECRET_HASH + "-reset"),
        )
        await conn.execute(
            "INSERT INTO download_history (user_id, slug_url, fmt, status, error, finished_at) "
            "VALUES (%s, '6712--test-novel', 'epub', 'error', 'ranobelib timed out', "
            "'2026-10-08T10:00:00+00:00')",
            (user.id,),
        )
    return user.id


def _admin(client: TestClient) -> TestClient:
    assert _login(client).status_code == 303
    return client


# --- access ------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/admin", "/admin/tables", "/admin/tables/users"])
def test_data_pages_need_the_admin_login(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


# --- overview / list ---------------------------------------------------------------------


async def test_overview_counts_and_recent_rows(client: TestClient) -> None:
    await _seed(client)

    html = _admin(client).get("/admin").text

    assert "Обзор" in html
    assert re.search(r'stat-value">1</span><span class="wn-admin__stat-label">Пользователи', html)
    assert "alice@example.com" in html
    assert "ranobelib timed out" in html
    assert SECRET_HASH not in html


async def test_table_list_comes_from_the_database_schema(client: TestClient) -> None:
    await _seed(client)

    html = _admin(client).get("/admin/tables").text

    for table in ("users", "library_entries", "comments", "download_history", "activity_events"):
        assert f'href="/admin/tables/{table}"' in html


# --- secrets ------------------------------------------------------------------------------


async def test_secret_columns_are_masked(client: TestClient) -> None:
    await _seed(client)
    _admin(client)

    users = client.get("/admin/tables/users").text
    tokens = client.get("/admin/tables/password_reset_tokens").text

    assert "alice@example.com" in users  # the admin owns the data - email is visible
    assert SECRET_HASH not in users
    assert SECRET_HASH not in tokens
    assert MASK in users and MASK in tokens


async def test_secrets_cannot_be_searched_or_sorted_on(client: TestClient) -> None:
    await _seed(client)
    _admin(client)

    found = client.get("/admin/tables/users", params={"q": SECRET_HASH[:12]}).text
    sorted_ = client.get("/admin/tables/users", params={"sort": "password_hash"})

    assert "Найдено строк: 0" in found
    assert sorted_.status_code == 200
    # fell back to id
    assert re.search(r'aria-sort="descending">\s*<a href="\?sort=id&', sorted_.text)
    assert SECRET_HASH not in sorted_.text


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("users", "password_hash"),
        ("users", "session_version"),
        ("password_reset_tokens", "token_hash"),
        ("email_verification_tokens", "code_hash"),
        ("future_table", "api_token"),
        ("future_table", "refresh_token_hash"),
        ("future_table", "client_secret"),
    ],
)
def test_secret_column_rule(table: str, column: str) -> None:
    assert is_secret_column(table, column)


@pytest.mark.parametrize("column", ["email", "nickname", "created_at", "failed_code_attempts"])
def test_ordinary_columns_stay_visible(column: str) -> None:
    assert not is_secret_column("users", column)


# --- injection ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "users;DROP TABLE users",
        'users"',
        "pg_catalog.pg_authid",
        "information_schema.tables",
        "nope",
    ],
)
async def test_unknown_table_names_are_404(client: TestClient, name: str) -> None:
    await _seed(client)
    _admin(client)

    assert client.get(f"/admin/tables/{name}").status_code == 404
    assert "alice@example.com" in client.get("/admin/tables/users").text  # still there


@pytest.mark.parametrize(
    "sort", ["id; DROP TABLE users--", 'email" DESC; --', "1", "(SELECT 1)", "email, password_hash"]
)
async def test_sort_takes_only_a_whitelisted_column(client: TestClient, sort: str) -> None:
    await _seed(client)
    _admin(client)

    response = client.get("/admin/tables/users", params={"sort": sort, "order": "asc; --"})

    assert response.status_code == 200
    assert 'aria-sort="ascending"' not in response.text  # bogus order -> plain descending
    assert "alice@example.com" in client.get("/admin/tables/users").text


@pytest.mark.parametrize("q", ["' OR 1=1 --", "%", "_", "\\"])
async def test_search_text_is_a_literal(client: TestClient, q: str) -> None:
    await _seed(client)
    _admin(client)

    response = client.get("/admin/tables/users", params={"q": q})

    assert response.status_code == 200
    assert "Найдено строк: 0" in response.text


# --- paging -------------------------------------------------------------------------------


async def test_pages_of_fifty_and_page_numbers_clamp(client: TestClient) -> None:
    user_id = await _seed(client)
    async with connection() as conn:
        for i in range(PAGE_SIZE * 2 + 20):
            await conn.execute(
                "INSERT INTO activity_events (user_id, kind, slug_url, created_at) "
                "VALUES (%s, 'chapter_read', %s, 'now')",
                (user_id, f"slug-{i:03d}"),
            )
    _admin(client)

    first = client.get("/admin/tables/activity_events").text
    last = client.get("/admin/tables/activity_events", params={"page": 3}).text
    beyond = client.get("/admin/tables/activity_events", params={"page": 999}).text

    assert first.count("<tr>") - 1 == PAGE_SIZE  # minus the header row
    assert last.count("<tr>") - 1 == 20
    assert "страница 3 из 3" in beyond
    assert "slug-119" in first  # newest id first
    assert client.get("/admin/tables/activity_events", params={"page": 0}).status_code == 422


async def test_search_finds_rows_case_insensitively(client: TestClient) -> None:
    await _seed(client)
    _admin(client)

    html = client.get("/admin/tables/users", params={"q": "ALICE@"}).text

    assert "Найдено строк: 1" in html
    assert "alice@example.com" in html
