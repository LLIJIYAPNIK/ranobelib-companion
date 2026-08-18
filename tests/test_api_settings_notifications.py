"""GET/POST /settings/notifications (PR 171) - "Показывать уведомления"/"Не беспокоить"."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
from app.config import get_settings


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


def _register(client: TestClient, email: str, password: str = "hunter2pass") -> None:
    client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password},
    )


def test_anonymous_visitor_sees_a_locked_screen_instead_of_the_form(
    client: TestClient,
) -> None:
    response = client.get("/settings/notifications")

    assert response.status_code == 200
    assert 'class="locked-feature"' in response.text
    assert 'name="notifications_enabled"' not in response.text


def test_anonymous_post_is_redirected_to_login(client: TestClient) -> None:
    response = client.post("/settings/notifications", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logged_in_visitor_sees_both_toggles_checked_by_default(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/settings/notifications")

    assert response.status_code == 200
    assert (
        '<input type="checkbox" name="notifications_enabled" checked>' in response.text
    )
    assert '<input type="checkbox" name="do_not_disturb" >' in response.text


def test_update_notifications_saves_both_flags(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/notifications", data={"notifications_enabled": "on", "do_not_disturb": "on"}
    )

    assert response.status_code == 200
    assert "Настройки уведомлений сохранены" in response.text
    assert '<input type="checkbox" name="notifications_enabled" checked>' in response.text
    assert '<input type="checkbox" name="do_not_disturb" checked>' in response.text


def test_unchecking_notifications_enabled_persists(client: TestClient) -> None:
    _register(client, "alice@example.com")

    # An unchecked checkbox isn't sent by the browser at all - omitting the field
    # entirely is what a real unchecked submit looks like.
    client.post("/settings/notifications", data={})
    response = client.get("/settings/notifications")

    assert '<input type="checkbox" name="notifications_enabled" >' in response.text


def test_notifications_nav_link_sits_between_security_and_reading(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/settings/notifications")

    security_pos = response.text.index('href="/settings/security"')
    notifications_pos = response.text.index('href="/settings/notifications"')
    reading_pos = response.text.index('href="/settings/reading"')
    assert security_pos < notifications_pos < reading_pos
