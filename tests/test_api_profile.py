"""GET /profile (PR 92) - the read-only account profile page.

PR 122 turned it into a public page (GET /profile/{user_id}) with two extra sections -
"Читает сейчас" and "Библиотека" - covered further down.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Cover, Label, Title

import app.db.connection as db_connection
from app.config import get_settings
from app.db.connection import get_connection
from app.db.library import record_progress
from app.db.users import get_user_by_email


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


def _user_id(email: str) -> int:
    user = get_user_by_email(get_connection(), email)
    assert user is not None
    return user.id


def _fake_title(slug_url: str = "6712--test-novel") -> Title:
    return Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url=slug_url,
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _FakeClient:
    def __init__(self, title: Title) -> None:
        self._title = title

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

    async def get_table_of_contents(self) -> list:
        # library_items_for_user() only calls this once a library entry has a recorded
        # position - no volumes needed for these tests, which don't assert on the
        # resulting reading-progress percentage.
        return []


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


# --- PR 122: /profile/{user_id} - the public profile page ---------------------------


def test_public_profile_by_id_shows_the_owners_info(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "alice@example.com" in response.text


def test_public_profile_unknown_user_id_is_404(client: TestClient) -> None:
    response = client.get("/profile/999")

    assert response.status_code == 404


def test_public_profile_is_viewable_while_logged_out(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    client.post("/logout")

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "alice@example.com" in response.text


def test_public_profile_of_another_user_has_no_edit_link(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    _register(client, "bob@example.com")  # switches the session to Bob

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Редактировать" not in response.text


def test_public_profile_shows_currently_reading_when_a_position_is_recorded(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    record_progress(
        get_connection(), user_id=alice_id, slug_url="6712--test-novel", volume="1", number="5"
    )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert 'class="profile-section__title"' in response.text
    assert "Читает сейчас" in response.text
    assert '<a class="profile-current-read__name" href="/titles/6712--test-novel">' in (
        response.text
    )
    assert "Test Novel" in response.text
    assert "Том 1, глава 5" in response.text


def test_public_profile_omits_currently_reading_for_a_never_opened_entry(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Читает сейчас" not in response.text


def test_public_profile_shows_the_library_grid(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert '<h2 class="profile-section__title">Библиотека</h2>' in response.text
    assert 'class="title-card-grid"' in response.text
    assert "Test Novel" in response.text


def test_public_profile_omits_both_new_sections_when_the_library_is_empty(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert 'class="profile-section"' not in response.text
    assert "Читает сейчас" not in response.text
    assert 'class="title-card-grid"' not in response.text


def test_public_profile_is_the_same_for_the_owner_and_a_different_visitor(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    record_progress(
        get_connection(), user_id=alice_id, slug_url="6712--test-novel", volume="1", number="5"
    )
    _register(client, "bob@example.com")  # now viewing as a different, logged-in user

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Читает сейчас" in response.text
    assert '<h2 class="profile-section__title">Библиотека</h2>' in response.text
    assert "Том 1, глава 5" in response.text
