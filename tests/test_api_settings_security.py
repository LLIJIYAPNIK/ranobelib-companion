"""GET/POST /settings/security (PR 91) - changing the account's password."""

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
    response = client.get("/settings/security")

    assert response.status_code == 200
    assert 'class="locked-feature"' in response.text
    assert 'name="current_password"' not in response.text


def test_logged_in_visitor_sees_a_password_change_section_heading(
    client: TestClient,
) -> None:
    # PR 110: a heading over the form, same pattern as the other settings pages'
    # section titles, so a future addition to "Безопасность" has somewhere to attach.
    _register(client, "alice@example.com")

    response = client.get("/settings/security")

    assert response.status_code == 200
    assert '<h2 class="reader-settings-section__title">Изменение пароля</h2>' in response.text


def test_anonymous_post_is_redirected_to_login(client: TestClient) -> None:
    response = client.post(
        "/settings/security",
        data={
            "current_password": "hunter2pass",
            "new_password": "newpassword1",
            "new_password_confirm": "newpassword1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logged_in_visitor_sees_the_form(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/settings/security")

    assert response.status_code == 200
    assert 'name="current_password"' in response.text
    assert 'name="new_password"' in response.text
    assert 'name="new_password_confirm"' in response.text


def test_update_password_changes_the_password(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")

    response = client.post(
        "/settings/security",
        data={
            "current_password": "hunter2pass",
            "new_password": "newpassword1",
            "new_password_confirm": "newpassword1",
        },
    )

    assert response.status_code == 200
    assert "Пароль изменён" in response.text

    client.post("/logout")
    login_with_new_password = client.post(
        "/login", data={"email": "alice@example.com", "password": "newpassword1"}
    )
    assert login_with_new_password.status_code == 200
    assert "alice@example.com" in login_with_new_password.text


def test_update_password_rejects_a_wrong_current_password(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")

    response = client.post(
        "/settings/security",
        data={
            "current_password": "wrong-password",
            "new_password": "newpassword1",
            "new_password_confirm": "newpassword1",
        },
    )

    assert response.status_code == 400
    assert "Неверный текущий пароль" in response.text
    assert "Пароль изменён" not in response.text

    # The old password still works - nothing was changed.
    client.post("/logout")
    login_with_old_password = client.post(
        "/login", data={"email": "alice@example.com", "password": "hunter2pass"}
    )
    assert login_with_old_password.status_code == 200
    assert "alice@example.com" in login_with_old_password.text


def test_update_password_rejects_a_mismatched_confirmation(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")

    response = client.post(
        "/settings/security",
        data={
            "current_password": "hunter2pass",
            "new_password": "newpassword1",
            "new_password_confirm": "different-password",
        },
    )

    assert response.status_code == 400
    assert "Пароли не совпадают" in response.text
    assert "Пароль изменён" not in response.text

    # The old password still works - nothing was changed.
    client.post("/logout")
    login_with_old_password = client.post(
        "/login", data={"email": "alice@example.com", "password": "hunter2pass"}
    )
    assert login_with_old_password.status_code == 200
    assert "alice@example.com" in login_with_old_password.text
