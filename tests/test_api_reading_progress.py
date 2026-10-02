"""POST /reading-progress/tick - the paragraph position reading-progress-tick.js sends while
a chapter is being read (see app/api/reading_progress.py)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.library import add_entry, get_entry
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

_TICK = {
    "slug_url": "6712--test-novel",
    "volume": "1",
    "number": "5",
    "paragraph": "50",
    "paragraph_total": "80",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def test_tick_requires_login(client: TestClient) -> None:
    response = client.post("/reading-progress/tick", data=_TICK, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_tick_records_the_paragraph_position(client: TestClient) -> None:
    register(client, "alice@example.com")  # user id 1
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")

    response = client.post("/reading-progress/tick", data=_TICK)

    assert response.status_code == 204
    async with connection() as conn:
        entry = await get_entry(conn, 1, "6712--test-novel")
    assert entry.last_read_volume == "1"
    assert entry.last_read_number == "5"
    assert entry.last_read_paragraph == 50
    assert entry.last_read_paragraph_total == 80


async def test_tick_overwrites_an_earlier_tick(client: TestClient) -> None:
    # Last write wins on the server - "whoever got further" is settled on page load
    # (tap-to-read.js/reader-progress.js), a step back in Tap Focus is a real position.
    register(client, "alice@example.com")
    async with connection() as conn:
        await add_entry(conn, 1, "6712--test-novel")

    client.post("/reading-progress/tick", data=_TICK)
    client.post("/reading-progress/tick", data={**_TICK, "paragraph": "49"})

    async with connection() as conn:
        entry = await get_entry(conn, 1, "6712--test-novel")
    assert entry.last_read_paragraph == 49


async def test_tick_outside_the_library_is_a_noop(client: TestClient) -> None:
    register(client, "alice@example.com")

    response = client.post("/reading-progress/tick", data=_TICK)

    assert response.status_code == 204
    async with connection() as conn:
        assert await get_entry(conn, 1, "6712--test-novel") is None


@pytest.mark.parametrize(
    ("paragraph", "paragraph_total"), [("0", "80"), ("81", "80"), ("1", "0"), ("x", "80")]
)
def test_tick_rejects_an_impossible_position(
    client: TestClient, paragraph: str, paragraph_total: str
) -> None:
    register(client, "alice@example.com")

    response = client.post(
        "/reading-progress/tick",
        data={**_TICK, "paragraph": paragraph, "paragraph_total": paragraph_total},
    )

    assert response.status_code == 422
