"""End-to-end friend requests/relationships (PR 199) through the real ASGI app - the
"Добавить в друзья" button states on the public profile page, and the three POST actions
(request/accept/remove) it drives.

Same isolation strategy as tests/test_api_profile.py: the shared test Postgres database is
wiped and re-migrated per test, and `_register()` on an already-logged-in client switches
the session to the newly registered user.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.users import get_user_by_email
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


def _register(client: TestClient, email: str, password: str = "hunter2pass") -> None:
    client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password},
    )


async def _user_id(email: str) -> int:
    async with connection() as conn:
        user = await get_user_by_email(conn, email)
    assert user is not None
    return user.id


async def test_profile_button_shows_add_friend_with_no_relationship(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")  # switches the session to Bob

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Добавить в друзья" in response.text


async def test_profile_button_is_absent_on_your_own_profile(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert "Добавить в друзья" not in response.text


async def test_sending_a_request_shows_sent_state_on_the_requesters_view(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")

    response = client.post(
        f"/friends/{alice_id}/request", data={"next": f"/profile/{alice_id}"}
    )
    assert response.status_code == 200  # redirected (TestClient follows by default)
    assert "Заявка отправлена" in response.text

    _register(client, "carol@example.com")  # anyone else still sees "Добавить в друзья"
    response = client.get(f"/profile/{bob_id}")
    assert "Добавить в друзья" in response.text


async def test_recipient_sees_accept_and_decline_on_their_profile(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    client.post(f"/friends/{alice_id}/request")

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})

    response = client.get(f"/profile/{bob_id}")

    assert "Принять заявку" in response.text
    assert "Отклонить" in response.text


async def test_accepting_a_request_makes_both_sides_see_friends_state(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    client.post(f"/friends/{alice_id}/request")

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    client.post(f"/friends/{bob_id}/accept")

    response = client.get(f"/profile/{bob_id}")
    assert "Вы в друзьях" in response.text

    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "hunter2pass"})
    response = client.get(f"/profile/{alice_id}")
    assert "Вы в друзьях" in response.text


async def test_declining_a_request_returns_to_no_relationship_state(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    client.post(f"/friends/{alice_id}/request")

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    client.post(f"/friends/{bob_id}/remove")

    response = client.get(f"/profile/{bob_id}")
    assert "Добавить в друзья" in response.text


async def test_request_rejects_adding_yourself(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")

    response = client.post(f"/friends/{alice_id}/request")

    assert response.status_code == 400


async def test_request_404s_for_an_unknown_user(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post("/friends/999/request")

    assert response.status_code == 404


async def test_request_requires_login(client: TestClient) -> None:
    response = client.post("/friends/1/request", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_sending_a_friend_request_notifies_the_recipient(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")

    client.post(f"/friends/{alice_id}/request")

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    response = client.get("/notifications/unread-count")

    assert response.json() == {"unread_count": 1}


async def test_accepting_a_request_notifies_the_original_requester(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    client.post(f"/friends/{alice_id}/request")

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    client.post(f"/friends/{bob_id}/accept")

    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "hunter2pass"})
    response = client.get("/notifications/unread-count")

    assert response.json() == {"unread_count": 1}


async def test_repeat_request_click_does_not_duplicate_the_notification(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")

    client.post(f"/friends/{alice_id}/request")
    client.post(f"/friends/{alice_id}/request")  # repeat click

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    response = client.get("/notifications/unread-count")

    assert response.json() == {"unread_count": 1}
