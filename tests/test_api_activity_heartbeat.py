"""POST /activity/heartbeat - the tick app/static/js/activity-heartbeat.js sends while a
chapter page stays open and visible (see app/api/activity.py)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
from app.config import get_settings
from app.db.activity import total_active_seconds_today
from app.db.connection import get_connection


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def _register(client: TestClient, email: str = "alice@example.com") -> None:
    client.post(
        "/register",
        data={"email": email, "password": "hunter2pass", "password_confirm": "hunter2pass"},
    )


def test_heartbeat_requires_login(client: TestClient) -> None:
    response = client.post(
        "/activity/heartbeat",
        data={"slug_url": "6712--test-novel", "seconds": "30"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_heartbeat_records_seconds(client: TestClient) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "30"}
    )

    assert response.status_code == 204
    assert total_active_seconds_today(get_connection(), 1) == 30


def test_heartbeat_accumulates_across_ticks(client: TestClient) -> None:
    _register(client)  # user id 1

    client.post("/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "30"})
    client.post("/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "30"})

    assert total_active_seconds_today(get_connection(), 1) == 60


def test_heartbeat_rejects_seconds_above_the_interval_clamp(client: TestClient) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "9999"}
    )

    assert response.status_code == 422
    assert total_active_seconds_today(get_connection(), 1) == 0


def test_heartbeat_rejects_non_positive_seconds(client: TestClient) -> None:
    _register(client)  # user id 1

    response = client.post(
        "/activity/heartbeat", data={"slug_url": "6712--test-novel", "seconds": "0"}
    )

    assert response.status_code == 422
