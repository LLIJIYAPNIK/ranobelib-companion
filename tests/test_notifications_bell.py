"""The sidebar notifications bell (PR 168) - a global element in base.html, same
"gated to logged-in visitors, driven by a poll script loaded once" shape as the downloads
badge (see tests/test_downloads_badge.py)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.db_reset import reset_app_database

client = TestClient(app)


@pytest.fixture
def logged_in_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

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
