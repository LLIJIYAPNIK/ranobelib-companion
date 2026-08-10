"""End-to-end registration/login/logout through the real ASGI app.

Each test gets its own on-disk SQLite file (`tmp_path`) and resets the process-wide
connection singleton (`app.db.connection._connection`) so accounts created by one test
never leak into another - `app.main`'s lifespan (which runs migrations) only fires when
`TestClient` is used as a context manager, so every test does that explicitly.
"""

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


def _register(client: TestClient, email: str, password: str = "hunter2pass") -> object:
    return client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password},
    )


def test_register_creates_session_and_redirects_home(client: TestClient) -> None:
    response = _register(client, "alice@example.com")

    assert response.status_code == 200  # followed the redirect
    assert response.history[0].status_code == 303
    assert response.history[0].headers["location"] == "/"
    assert "alice@example.com" in response.text


def test_register_duplicate_email_shows_form_error(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = _register(client, "alice@example.com")

    assert response.status_code == 400
    assert "уже зарегистрирован" in response.text


def test_register_password_mismatch_shows_form_error(client: TestClient) -> None:
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "password_confirm": "different",
        },
    )

    assert response.status_code == 400
    assert "не совпадают" in response.text


def test_login_wrong_password_shows_form_error(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "alice@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 400
    assert "Неверный email или пароль" in response.text


def test_login_unknown_email_shows_same_form_error(client: TestClient) -> None:
    response = client.post(
        "/login", data={"email": "nobody@example.com", "password": "whatever"}
    )

    assert response.status_code == 400
    assert "Неверный email или пароль" in response.text


def test_login_correct_credentials_establishes_session(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login", data={"email": "alice@example.com", "password": "hunter2pass"}
    )

    assert response.status_code == 200
    assert response.history[0].status_code == 303
    assert "alice@example.com" in response.text


def test_login_without_remember_me_uses_the_default_session_lifetime(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login",
        data={"email": "alice@example.com", "password": "hunter2pass"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert f"Max-Age={get_settings().session_max_age}" in response.headers["set-cookie"]


def test_login_with_remember_me_extends_the_session_lifetime(client: TestClient) -> None:
    _register(client, "alice@example.com", password="hunter2pass")
    client.post("/logout")

    response = client.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "remember_me": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        f"Max-Age={get_settings().session_remember_max_age}" in response.headers["set-cookie"]
    )


def test_logout_clears_session(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post("/logout")
    home = client.get("/")

    assert response.status_code in (200, 303)
    assert "alice@example.com" not in home.text
    assert 'href="/login"' in home.text
