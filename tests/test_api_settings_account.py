"""GET/POST /settings/account (PR 90) - editing the account's nickname/email/bio."""

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
    response = client.get("/settings/account")

    assert response.status_code == 200
    assert 'class="locked-feature"' in response.text
    assert 'name="email"' not in response.text


def test_anonymous_post_is_redirected_to_login(client: TestClient) -> None:
    response = client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "", "bio": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logged_in_visitor_sees_the_form_prefilled_with_their_email(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")

    response = client.get("/settings/account")

    assert response.status_code == 200
    assert 'name="email"' in response.text
    assert 'value="alice@example.com"' in response.text


def test_update_account_saves_nickname_and_bio(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "Alice Wong", "bio": "Hi there"},
    )

    assert response.status_code == 200
    assert 'value="Alice Wong"' in response.text
    assert ">Hi there</textarea>" in response.text


def test_update_account_can_change_email(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account",
        data={"email": "alice2@example.com", "nickname": "", "bio": ""},
    )

    assert response.status_code == 200
    assert 'value="alice2@example.com"' in response.text
