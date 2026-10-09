"""POST /activity/heartbeat - the tick app/static/js/activity-heartbeat.js sends while a
chapter page stays open and visible (see app/api/activity.py)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.activity import total_active_seconds_today
from app.db.connection import connection
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


def _register(client: TestClient, email: str = "alice@example.com") -> None:
    register(client, email)


def test_heartbeat_requires_login(client: TestClient) -> None:
    response = client.post(
        "/activity/heartbeat",
        data={"slug_url": "6712--test-novel", "seconds": "30"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_heartbeat_records_seconds(client: TestClient) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "30"}
    )

    assert response.status_code == 204
    async with connection() as conn:
        assert await total_active_seconds_today(conn, 1) == 30


async def test_heartbeat_accumulates_across_ticks(client: TestClient) -> None:
    _register(client)  # user id 1

    client.post("/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "30"})
    client.post("/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "30"})

    async with connection() as conn:
        assert await total_active_seconds_today(conn, 1) == 60


async def test_heartbeat_rejects_seconds_above_the_interval_clamp(client: TestClient) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "9999"}
    )

    assert response.status_code == 422
    async with connection() as conn:
        assert await total_active_seconds_today(conn, 1) == 0


def test_heartbeat_rejects_non_positive_seconds(client: TestClient) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "0"}
    )

    assert response.status_code == 422


# PR 332: heartbeats read offline come later from the device's queue (sync-queue.js).


async def test_a_resent_heartbeat_is_not_counted_twice(client: TestClient) -> None:
    # The first attempt reached the server but its answer was lost - the queue sends the
    # same event again.
    _register(client)  # user id 1
    tick = {"slug_url": "6712--test-novel", "seconds": "30", "event_id": "3f6c1a52-0b7e-4c1d"}

    first = client.post("/activity/heartbeat", data=tick)
    again = client.post("/activity/heartbeat", data=tick)

    assert first.status_code == again.status_code == 204
    async with connection() as conn:
        assert await total_active_seconds_today(conn, 1) == 30


async def test_heartbeats_with_different_ids_all_count(client: TestClient) -> None:
    _register(client)  # user id 1

    for event_id in ("event-0001", "event-0002", "event-0003"):
        client.post(
            "/activity/heartbeat",
            data={"slug_url": "6712--test-novel", "seconds": "30", "event_id": event_id},
        )

    async with connection() as conn:
        assert await total_active_seconds_today(conn, 1) == 90


async def test_the_same_event_id_from_another_account_still_counts(client: TestClient) -> None:
    tick = {"slug_url": "6712--test-novel", "seconds": "30", "event_id": "event-0001"}
    _register(client, "alice@example.com")  # user id 1
    client.post("/activity/heartbeat", data=tick)
    client.post("/logout")
    _register(client, "bob@example.com")  # user id 2

    client.post("/activity/heartbeat", data=tick)

    async with connection() as conn:
        assert await total_active_seconds_today(conn, 2) == 30


async def test_a_late_heartbeat_lands_on_the_day_it_was_read(client: TestClient) -> None:
    _register(client)  # user id 1
    two_days_ms = 2 * 24 * 60 * 60 * 1000

    response = client.post(
        "/activity/heartbeat",
        data={"slug_url": "6712--test-novel", "seconds": "30", "age_ms": str(two_days_ms)},
    )

    assert response.status_code == 204
    async with connection() as conn:
        assert await total_active_seconds_today(conn, 1) == 0
        cursor = await conn.execute("SELECT created_at FROM activity_events WHERE user_id = 1")
        (row,) = await cursor.fetchall()
    read_at = datetime.fromisoformat(row["created_at"])
    assert abs(datetime.now(UTC) - timedelta(days=2) - read_at) < timedelta(minutes=1)


def test_a_heartbeat_older_than_the_queue_keeps_is_refused(client: TestClient) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat",
        data={"slug_url": "6712--test-novel", "seconds": "30", "age_ms": "2592000001"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("event_id", ["short", "has spaces in it", "x" * 65, "<script>x</script>"])
def test_a_malformed_event_id_is_refused(client: TestClient, event_id: str) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat",
        data={"slug_url": "6712--test-novel", "seconds": "30", "event_id": event_id},
    )

    assert response.status_code == 422
