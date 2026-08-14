"""GET /profile (PR 92) - the read-only account profile page."""

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
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
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


def test_anonymous_visitor_sees_a_locked_screen_instead_of_the_profile(
    client: TestClient,
) -> None:
    response = client.get("/profile")

    assert response.status_code == 200
    assert 'class="locked-feature"' in response.text
    assert 'class="profile__avatar"' not in response.text


def test_profile_shows_the_avatar_and_email_when_no_nickname_is_set(
    client: TestClient,
) -> None:
    _register(client, "alice.wong@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert 'class="profile__avatar"' in response.text
    assert ">AW</div>" in response.text
    assert "alice.wong@example.com" in response.text


def test_profile_shows_the_uploaded_avatar_image_over_initials(client: TestClient) -> None:
    _register(client, "alice.wong@example.com")
    client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")},
    )

    response = client.get("/profile")

    assert response.status_code == 200
    assert '<img class="avatar-img" src="/avatars/' in response.text
    assert ">AW</div>" not in response.text


def test_profile_prefers_the_nickname_over_the_email(client: TestClient) -> None:
    _register(client, "alice.wong@example.com")
    client.post(
        "/settings/account",
        data={"email": "alice.wong@example.com", "nickname": "Bob Carter", "bio": ""},
    )

    response = client.get("/profile")

    assert response.status_code == 200
    assert ">Bob Carter</p>" in response.text
    assert ">BC</div>" in response.text


def test_profile_shows_the_bio_when_set(client: TestClient) -> None:
    _register(client, "alice@example.com")
    client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "", "bio": "Hello there"},
    )

    response = client.get("/profile")

    assert response.status_code == 200
    assert 'class="profile__bio"' in response.text
    assert "Hello there" in response.text


def test_profile_shows_an_empty_state_when_no_bio_is_set(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert "не рассказал о себе" in response.text


def test_profile_shows_the_registration_date(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert "На сайте с" in response.text


def test_profile_has_an_edit_link_to_settings_account(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert '<a class="btn btn--secondary" href="/settings/account">Редактировать</a>' in (
        response.text
    )
