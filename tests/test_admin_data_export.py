"""PR 352: «Данные БД» on the UI kit - the CSV export keeps every guarantee of the page
(the schema whitelist, secrets masked, literal search, whitelisted sort, read-only with a
timeout), defuses spreadsheet formulas, is logged, and the user-id columns come from the
database's foreign keys."""

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.admin_tables import csv_filename, csv_href, table_csv
from app.config import get_settings
from app.db import admin_audit
from app.db.admin_data import (
    MASK,
    TableExport,
    browse_table,
    export_table,
    table_columns,
    user_id_columns,
)
from app.db.connection import connection
from tests.test_admin_auth import PASSWORD, Clock, _client
from tests.test_admin_data import SECRET_HASH, _admin, _seed


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


def _rows(body: bytes) -> list[list[str]]:
    assert body.startswith("﻿".encode())  # a BOM, so Excel reads UTF-8
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


async def test_the_export_masks_secrets_like_the_page(client: TestClient) -> None:
    await _seed(client)
    _admin(client)

    users = client.get("/admin/tables/users/csv")
    tokens = client.get("/admin/tables/password_reset_tokens/csv")

    assert users.status_code == 200 and users.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="users-' in users.headers["content-disposition"]
    for response in (users, tokens):
        assert SECRET_HASH not in response.content.decode("utf-8-sig")
    header, *rows = _rows(users.content)
    assert rows[0][header.index("email")] == "alice@example.com"
    assert rows[0][header.index("password_hash")] == MASK
    assert rows[0][header.index("session_version")] == MASK


async def test_the_export_is_the_current_selection(client: TestClient) -> None:
    user_id = await _seed(client)
    async with connection() as conn:
        for slug in ("bbb-title", "aaa-title", "other"):
            await conn.execute(
                "INSERT INTO activity_events (user_id, kind, slug_url, created_at) "
                "VALUES (%s, 'chapter_read', %s, 'now')",
                (user_id, slug),
            )
    _admin(client)

    response = client.get(
        "/admin/tables/activity_events/csv",
        params={"q": "-TITLE", "sort": "slug_url", "order": "asc", "page": 9},
    )

    header, *rows = _rows(response.content)
    assert [row[header.index("slug_url")] for row in rows] == ["aaa-title", "bbb-title"]


@pytest.mark.parametrize("q", [SECRET_HASH[:12], "' OR 1=1 --", "%"])
async def test_the_export_search_is_literal_and_skips_secrets(client: TestClient, q: str) -> None:
    await _seed(client)
    _admin(client)

    response = client.get("/admin/tables/users/csv", params={"q": q})

    assert _rows(response.content)[1:] == []


@pytest.mark.parametrize("sort", ["password_hash", "id; DROP TABLE users--", "(SELECT 1)"])
async def test_the_export_sort_is_whitelisted(client: TestClient, sort: str) -> None:
    await _seed(client)
    _admin(client)

    response = client.get("/admin/tables/users/csv", params={"sort": sort, "order": "asc; --"})

    assert response.status_code == 200
    assert len(_rows(response.content)) == 2  # the header and alice, sorted by id instead


@pytest.mark.parametrize("name", ["users;DROP TABLE users", "pg_catalog.pg_authid", "nope"])
async def test_the_export_knows_only_schema_tables(client: TestClient, name: str) -> None:
    await _seed(client)
    _admin(client)

    assert client.get(f"/admin/tables/{name}/csv").status_code == 404


async def test_the_export_needs_the_admin(client: TestClient) -> None:
    await _seed(client)

    response = client.get("/admin/tables/users/csv", follow_redirects=False)

    assert response.headers["location"] == "/admin/login"


async def test_each_export_is_logged(client: TestClient) -> None:
    await _seed(client)
    _admin(client)

    client.get("/admin/tables/users/csv", params={"q": "alice", "sort": "email"})

    async with connection() as conn:
        [entry] = [
            e for e in await admin_audit.list_entries(conn) if e.action == admin_audit.DATA_EXPORT
        ]
    assert (entry.entity, entry.entity_id) == ("table", "users")
    assert entry.details == {
        "rows": 1,
        "matching": 1,
        "query": "alice",
        "sort": "email",
        "order": "desc",
    }


async def test_the_export_stops_at_its_limit(client: TestClient) -> None:
    user_id = await _seed(client)
    async with connection() as conn:
        for i in range(5):
            await conn.execute(
                "INSERT INTO activity_events (user_id, kind, slug_url, created_at) "
                "VALUES (%s, 'chapter_read', %s, 'now')",
                (user_id, f"s{i}"),
            )
        columns = (await table_columns(conn))["activity_events"]
        export = await export_table(conn, "activity_events", columns, limit=2)

    assert (len(export.rows), export.total, export.truncated) == (2, 5, True)


def test_formulas_arrive_as_text() -> None:
    export = TableExport(
        columns=["id", "nickname", "password_hash"],
        secret={"password_hash"},
        rows=[
            {"id": "1", "nickname": '=HYPERLINK("http://x")', "password_hash": None},
            {"id": "2", "nickname": "-1+2", "password_hash": None},
            {"id": "3", "nickname": None, "password_hash": None},
        ],
        total=3,
    )

    header, *rows = _rows(table_csv(export, MASK))

    assert header == ["id", "nickname", "password_hash"]
    assert rows == [
        ["1", '\'=HYPERLINK("http://x")', MASK],
        ["2", "'-1+2", MASK],
        ["3", "", MASK],
    ]


def test_the_export_link_keeps_search_and_sort_not_the_page() -> None:
    href = csv_href("users", {"q": "al ice", "sort": "email", "order": "asc", "page": "3"})

    assert href == "/admin/tables/users/csv?q=al+ice&sort=email&order=asc"
    assert csv_href("users", {}) == "/admin/tables/users/csv"
    assert csv_filename("users", datetime(2026, 10, 15, 9, 5)) == "users-20261015-0905.csv"


async def test_user_id_columns_come_from_foreign_keys(client: TestClient) -> None:
    await _seed(client)
    async with connection() as conn:
        links = await user_id_columns(conn)

    assert links["users"] == {"id"}
    assert links["activity_events"] == {"user_id"}
    assert {"user_id", "actor_user_id"} <= links["notifications"]
    assert "slug_url" not in set().union(*links.values())


async def test_secret_values_never_leave_the_database(client: TestClient) -> None:
    # The page and the CSV mask them again on top - this checks the first line: the
    # SELECT itself puts NULL where a secret is, for the page's query and the export's.
    await _seed(client)
    async with connection() as conn:
        columns = (await table_columns(conn))["users"]
        page = await browse_table(conn, "users", columns)
        export = await export_table(conn, "users", columns)

    for rows in (page.rows, export.rows):
        assert rows[0]["password_hash"] is None and rows[0]["session_version"] is None
        assert rows[0]["email"] == "alice@example.com"
