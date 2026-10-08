"""PR 326: admin queries are read-only, time-limited and size-limited."""

import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.db.admin_data as admin_data
from app.config import get_settings
from app.db.connection import connection
from app.db.migrate import run_migrations
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.db_reset import fresh_connection
from tests.test_admin_auth import PASSWORD, Clock, _client, _login


@pytest.fixture
async def conn() -> psycopg.AsyncConnection:
    connection_ = await fresh_connection()
    await run_migrations(connection_)
    return connection_


async def test_admin_queries_run_read_only_with_a_timeout(conn: psycopg.AsyncConnection) -> None:
    async with admin_data._read_only(conn):
        timeout = await (await conn.execute("SHOW statement_timeout")).fetchone()
        read_only = await (await conn.execute("SHOW transaction_read_only")).fetchone()
    assert timeout["statement_timeout"] == f"{admin_data.QUERY_TIMEOUT_MS // 1000}s"
    assert read_only["transaction_read_only"] == "on"

    # Scoped to that transaction - the rest of the site's queries are untouched.
    after = await (await conn.execute("SHOW statement_timeout")).fetchone()
    assert after["statement_timeout"] == "0"


async def test_nothing_can_be_written_through_them(conn: psycopg.AsyncConnection) -> None:
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        async with admin_data._read_only(conn):
            await conn.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES ('x', 'y', 'z')"
            )
    count = await (await conn.execute("SELECT COUNT(*) AS n FROM users")).fetchone()
    assert count["n"] == 0


async def test_a_slow_query_is_cancelled(
    conn: psycopg.AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin_data, "QUERY_TIMEOUT_MS", 50)

    with pytest.raises(psycopg.errors.QueryCanceled):
        async with admin_data._read_only(conn):
            await conn.execute("SELECT pg_sleep(1)")


# --- through the pages -------------------------------------------------------------------


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


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/admin", "overview"),
        ("/admin/tables", "list_tables"),
        ("/admin/tables/users", "browse_table"),
    ],
)
def test_a_timeout_is_an_error_page_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, target: str
) -> None:
    async def cancelled(*args, **kwargs):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    import app.api.admin as admin_api

    monkeypatch.setattr(admin_api, target, cancelled)
    _login(client)

    response = client.get(path)

    assert response.status_code == 503
    assert "Запрос остановлен" in response.text
    assert "statement timeout" not in response.text  # no internals in the page


async def test_long_cells_are_cut_in_the_query(client: TestClient) -> None:
    register(client, "alice@example.com", "hunter2pass")
    async with connection() as conn:
        user = await get_user_by_email(conn, "alice@example.com")
        await conn.execute(
            "UPDATE users SET bio = %s WHERE id = %s", ("Б" * 5000 + "END-MARKER", user.id)
        )
    _login(client)

    html = client.get("/admin/tables/users").text

    assert "END-MARKER" not in html
    longest = max(len(m) for m in re.findall(r"Б+", html))
    assert longest <= admin_data.CELL_LIMIT


async def test_numeric_columns_still_sort_as_numbers(client: TestClient) -> None:
    async with connection() as conn:
        for i in range(12):
            await conn.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (%s, 'h', 'now')",
                (f"u{i}@example.com",),
            )
    _login(client)

    html = client.get("/admin/tables/users", params={"sort": "id", "order": "desc"}).text
    ids = [int(n) for n in re.findall(r"<tr>\s*<td>(\d+)</td>", html)]

    assert ids == sorted(ids, reverse=True)
    assert ids[0] >= 10  # 12 rows: "12" first, not "9" (a text sort)
