"""PR 336: GET /continue - «Продолжить чтение», the app icon's shortcut.

A 302 to the chapter the home page's hero continues (the most recently read library
entry), or to the catalog for a guest and for a library with nothing read yet. Never
stored: where it leads changes with every chapter read. Same per-test database as
tests/test_api_library.py.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.library import add_entry, record_progress
from tests.auth_helpers import register
from tests.db_reset import reset_app_database


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _continue(client: TestClient) -> tuple[str, str]:
    response = client.get("/continue", follow_redirects=False)
    assert response.status_code == 302
    return response.headers["location"], response.headers["cache-control"]


def test_a_guest_goes_to_the_catalog(client: TestClient) -> None:
    location, cache_control = _continue(client)

    assert location == "/catalog"
    assert cache_control == "no-store"


def test_an_empty_library_goes_to_the_catalog(client: TestClient) -> None:
    register(client, "alice@example.com")

    assert _continue(client)[0] == "/catalog"


async def test_titles_added_but_never_read_go_to_the_catalog(client: TestClient) -> None:
    register(client, "alice@example.com")
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")

    assert _continue(client)[0] == "/catalog"


async def test_leads_to_the_chapter_read_last(client: TestClient) -> None:
    register(client, "alice@example.com")
    async with connection() as conn:
        for slug in ("1--first", "2--second", "3--added-later"):
            await add_entry(conn, 1, slug)
        await record_progress(conn, 1, "2--second", "1", "40")
        await record_progress(conn, 1, "1--first", "2", "17.5")
        # Added after the last read, never opened: the hero skips it, so does this.
        await add_entry(conn, 1, "4--added-last")

    location, cache_control = _continue(client)

    assert location == "/titles/1--first/chapters/2/17.5"
    assert cache_control == "no-store"


async def test_follows_the_account_not_the_device(client: TestClient) -> None:
    """Another account on the same browser gets its own chapter, not the previous one's."""
    register(client, "alice@example.com")
    async with connection() as conn:
        await add_entry(conn, 1, "1--alices")
        await record_progress(conn, 1, "1--alices", "1", "3")
    assert _continue(client)[0] == "/titles/1--alices/chapters/1/3"

    client.post("/logout")
    assert _continue(client)[0] == "/catalog"

    register(client, "bob@example.com")
    async with connection() as conn:
        await add_entry(conn, 2, "2--bobs")
        await record_progress(conn, 2, "2--bobs", "4", "1")
    assert _continue(client)[0] == "/titles/2--bobs/chapters/4/1"
