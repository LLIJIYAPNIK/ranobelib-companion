"""End-to-end friend requests/relationships (PR 199) through the real ASGI app - the
"Добавить в друзья" button states on the public profile page, the /friends list page, and
the three POST actions (request/accept/remove) both surfaces use.

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


def _register(
    client: TestClient,
    email: str,
    password: str = "hunter2pass",
    nickname: str | None = None,
) -> None:
    data = {"email": email, "password": password, "password_confirm": password}
    if nickname is not None:
        data["nickname"] = nickname
    client.post("/register", data=data)


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


async def test_friends_page_requires_login_shows_locked_state(client: TestClient) -> None:
    response = client.get("/friends")

    assert response.status_code == 200
    assert "Друзья скрыты" in response.text


async def test_friends_page_lists_incoming_outgoing_and_accepted(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    _register(client, "carol@example.com")
    client.post(f"/friends/{bob_id}/request")  # Carol -> Bob, pending
    _register(client, "dave@example.com")
    dave_id = await _user_id("dave@example.com")
    client.post(f"/friends/{bob_id}/request")  # Dave -> Bob, then accepted below

    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "hunter2pass"})
    client.post(f"/friends/{alice_id}/request")  # Bob -> Alice, pending (outgoing for Bob)
    client.post(f"/friends/{dave_id}/accept")  # Bob accepts Dave -> now friends

    response = client.get("/friends")

    assert response.status_code == 200
    assert "carol@example.com" in response.text  # incoming request row
    assert "alice@example.com" in response.text  # outgoing request row
    assert "dave@example.com" in response.text  # accepted friend row
    # PR 215: the friend count moved into the intro paragraph, replacing the redundant
    # "Друзья" heading that used to sit directly above the list.
    assert "Сейчас у вас 1 друг." in response.text
    assert '<h2 class="profile-section__title">Друзья</h2>' not in response.text


async def test_friends_page_with_no_friends_shows_intro_hint_not_a_bare_heading(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")

    response = client.get("/friends")

    assert response.status_code == 200
    assert "Друзей пока нет — используйте поиск выше, чтобы найти знакомых читателей." in (
        response.text
    )
    assert '<h2 class="profile-section__title">Друзья</h2>' not in response.text


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


# --- PR 209: nickname search on /friends -----------------------------------------------


async def test_friends_page_without_query_shows_no_search_results_section(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com", nickname="Alice")

    response = client.get("/friends")

    assert response.status_code == 200
    assert "Результаты поиска" not in response.text
    # The explanatory intro text/search form are always there for a logged-in visitor.
    assert 'action="/friends"' in response.text
    assert "Ник пользователя" in response.text


async def test_friends_search_finds_a_matching_nickname(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="AliceReader")
    _register(client, "bob@example.com", nickname="Bob")

    response = client.get("/friends", params={"query": "alice"})

    assert response.status_code == 200
    assert "Результаты поиска" in response.text
    assert "AliceReader" in response.text
    assert "Bob" not in response.text
    assert "Добавить в друзья" in response.text


async def test_friends_search_empty_query_does_not_list_everyone(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="Alice")
    _register(client, "bob@example.com", nickname="Bob")

    response = client.get("/friends", params={"query": ""})

    # An empty `query` string behaves the same as no query at all - no results section,
    # not "everyone matched".
    assert "Результаты поиска" not in response.text


async def test_friends_search_no_match_shows_empty_state(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="Alice")

    response = client.get("/friends", params={"query": "nobody-like-this"})

    assert response.status_code == 200
    assert "Никого не нашли" in response.text


async def test_friends_search_excludes_the_searcher_themselves(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="AliceReader")

    response = client.get("/friends", params={"query": "alice"})

    assert response.status_code == 200
    assert "Результаты поиска" in response.text
    # The only match is the searcher's own account - excluded, so this reads as no
    # results at all rather than a row with "add yourself" as an option.
    assert "Никого не нашли" in response.text
    assert "Добавить в друзья" not in response.text


async def test_friends_search_shows_outgoing_state_for_an_already_sent_request(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com", nickname="Alice")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com", nickname="Bob")
    client.post(f"/friends/{alice_id}/request")

    response = client.get("/friends", params={"query": "alice"})

    assert response.status_code == 200
    assert "Заявка отправлена" in response.text


async def test_friends_search_shows_friends_state_for_an_accepted_friend(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com", nickname="Alice")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com", nickname="Bob")
    bob_id = await _user_id("bob@example.com")
    client.post(f"/friends/{alice_id}/request")
    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    client.post(f"/friends/{bob_id}/accept")

    response = client.get("/friends", params={"query": "bob"})

    assert response.status_code == 200
    assert "Вы в друзьях" in response.text


async def test_friends_search_requires_login_to_return_results(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="Alice")
    client.post("/logout")

    response = client.get("/friends", params={"query": "alice"})

    assert response.status_code == 200
    assert "Друзья скрыты" in response.text
    assert "Alice" not in response.text
