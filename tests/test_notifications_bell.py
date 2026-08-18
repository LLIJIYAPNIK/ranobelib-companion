"""The sidebar notifications bell (PR 168) - a global element in base.html, same
"gated to logged-in visitors, driven by a poll script loaded once" shape as the downloads
badge (see tests/test_downloads_badge.py)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
from app.config import get_settings
from app.main import app

client = TestClient(app)


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

    with TestClient(app) as test_client:
        test_client.post(
            "/register",
            data={
                "email": "alice@example.com",
                "password": "hunter2pass",
                "password_confirm": "hunter2pass",
            },
        )
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def test_anonymous_visitor_gets_no_notifications_bell() -> None:
    response = client.get("/")

    assert 'data-role="notifications-trigger"' not in response.text
    assert "static/js/notifications-panel.js" not in response.text


def test_logged_in_visitor_gets_the_notifications_bell_on_any_page(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert 'data-role="notifications-trigger"' in response.text
    assert 'data-role="notifications-panel"' in response.text
    assert "static/js/notifications-panel.js" in response.text
    assert "static/js/notifications-actions.js" in response.text


def test_notifications_badge_starts_hidden_with_no_unread(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.get("/")

    assert '<span class="sidebar__badge" data-role="notifications-badge" hidden>0</span>' in (
        response.text
    )
