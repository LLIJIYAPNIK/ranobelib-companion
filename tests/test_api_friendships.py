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
from tests.auth_helpers import register
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
    register(client, email, password, nickname)


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
    # PR 278: each list's count sits on its tab.
    assert 'aria-current="page">Друзья<span class="wn-friends-tabs__count">1</span>' in (
        response.text
    )
    assert 'Входящие<span class="wn-friends-tabs__count">1</span>' in response.text
    assert 'Исходящие<span class="wn-friends-tabs__count">1</span>' in response.text


async def test_friends_page_with_no_friends_shows_the_empty_hint(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")

    response = client.get("/friends")

    assert response.status_code == 200
    # PR 278: the invite card doubles as the empty state.
    assert 'id="friends-invite-title">Друзей пока нет</h2>' in response.text
    assert 'Друзья<span class="wn-friends-tabs__count">0</span>' in response.text


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
    # PR 223: a bare "Bob" not in response.text substring check also caught the sidebar
    # account row's own display of the logged-in searcher's name (Bob, in this test) -
    # scope this to the actual search-result row instead.
    assert '<span class="ui-friend-row__name">Bob</span>' not in response.text
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


async def test_friends_search_marks_the_searchers_own_nickname_as_self(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com", nickname="AliceReader")

    response = client.get("/friends", params={"query": "alice"})

    assert response.status_code == 200
    assert "Результаты поиска" in response.text
    # The only match is the searcher's own account (PR 216) - shown as a row marked "Это
    # вы", not folded into "no results" (indistinguishable from a nonexistent nickname)
    # and not offered "Добавить в друзья" (adding yourself makes no sense).
    assert "Никого не нашли" not in response.text
    assert "AliceReader" in response.text
    assert "Это вы" in response.text
    assert "Добавить в друзья" not in response.text


async def test_friends_search_no_match_still_shows_empty_state(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/friends", params={"query": "nobody-with-this-nickname"})

    assert response.status_code == 200
    assert "Никого не нашли по запросу «nobody-with-this-nickname»." in response.text


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


# PR 278 (Webnovells Redesign, screen A6).


async def _alice_with_requests(client: TestClient) -> tuple[int, int, int]:
    """Bob -> Alice (incoming for Alice), Alice -> Carol (outgoing), logged in as Alice."""
    _register(client, "bob@example.com")
    _register(client, "carol@example.com")
    carol_id = await _user_id("carol@example.com")
    _register(client, "alice@example.com", nickname="AliceReader")
    alice_id = await _user_id("alice@example.com")
    bob_id = await _user_id("bob@example.com")
    client.post(f"/friends/{carol_id}/request")
    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "hunter2pass"})
    client.post(f"/friends/{alice_id}/request")
    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    return alice_id, bob_id, carol_id


async def test_friends_tabs_show_each_list_in_the_main_column(client: TestClient) -> None:
    _alice_id, bob_id, carol_id = await _alice_with_requests(client)

    incoming = client.get("/friends?tab=incoming")
    outgoing = client.get("/friends?tab=outgoing")

    incoming_main = incoming.text.split('data-role="friends-list"')[1].split("<aside")[0]
    outgoing_main = outgoing.text.split('data-role="friends-list"')[1].split("<aside")[0]
    assert f'action="/friends/{bob_id}/accept"' in incoming_main
    assert "Хочет добавить вас в друзья" in incoming_main
    assert f'action="/friends/{carol_id}/remove"' in outgoing_main
    assert 'href="/friends?tab=incoming" aria-current="page"' in incoming.text


async def test_friends_empty_request_tabs_show_their_empty_states(client: TestClient) -> None:
    _register(client, "alice@example.com")

    assert "Новых заявок нет" in client.get("/friends?tab=incoming").text
    assert "Отправленных заявок нет" in client.get("/friends?tab=outgoing").text


async def test_friends_unknown_tab_is_rejected(client: TestClient) -> None:
    _register(client, "alice@example.com")

    assert client.get("/friends?tab=everyone").status_code == 422


async def test_friends_requests_card_lists_both_kinds_on_every_tab(client: TestClient) -> None:
    _alice_id, bob_id, carol_id = await _alice_with_requests(client)

    response = client.get("/friends")

    side = response.text.split('id="friends-requests-title"')[1].split("</section>")[0]
    assert f'action="/friends/{bob_id}/accept"' in side
    assert f'action="/friends/{carol_id}/remove"' in side


async def test_friends_invite_card_offers_the_nickname_to_copy(client: TestClient) -> None:
    _register(client, "alice@example.com", nickname="AliceReader")

    response = client.get("/friends")

    assert 'data-copy="AliceReader"' in response.text
    assert "static/js/friends-invite-copy.js" in response.text


async def test_friends_invite_card_without_a_nickname_points_to_settings(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")

    response = client.get("/friends")

    assert 'data-role="friends-invite-copy"' not in response.text
    assert 'href="/settings/account">настройках аккаунта</a>' in response.text
    assert 'href="/settings/account">Настройки приватности</a>' in response.text
    assert "Настройки приватности →" not in response.text


async def test_friends_privacy_switch_flips_only_currently_reading(client: TestClient) -> None:
    _register(client, "alice@example.com")
    client.post(
        "/settings/account/privacy",
        data={"show_currently_reading": "on", "show_library": "on", "show_friends": "on"},
    )

    response = client.post(
        "/friends/privacy", data={"show_currently_reading": "false"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/friends"
    async with connection() as conn:
        user = await get_user_by_email(conn, "alice@example.com")
    assert user is not None
    assert user.show_currently_reading is False
    # The other three are written back exactly as they were.
    assert user.show_library is True
    assert (user.show_friends_activity_home, user.show_friends) == (False, True)
    page = client.get("/friends")
    assert 'aria-checked="false"' in page.text
    assert '<input type="hidden" name="show_currently_reading" value="true">' in page.text


def test_friends_privacy_switch_requires_login(client: TestClient) -> None:
    response = client.post(
        "/friends/privacy", data={"show_currently_reading": "true"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("show_reading", [True, False])
async def test_friend_card_shows_what_the_friend_reads_unless_hidden(
    client: TestClient, show_reading: bool
) -> None:
    from unittest.mock import patch

    from ranobelib.models import Cover, Label, Title

    from app.db.library import add_entry, record_progress

    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    client.post(f"/friends/{alice_id}/request")
    client.post(
        "/settings/account/privacy",
        data={"show_currently_reading": "on"} if show_reading else {},
    )
    async with connection() as conn:
        await add_entry(conn, bob_id, "6712--test-novel")
        await record_progress(conn, bob_id, "6712--test-novel", "1", "3")
    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    # Accept redirects to /friends, whose friend cards enrich the current title through
    # the SDK. Do not follow that setup redirect before the fake client below is active:
    # tests must never make a live ranobelib.me request (CLAUDE.md, "Тесты").
    client.post(f"/friends/{bob_id}/accept", follow_redirects=False)
    title = Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )

    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def get_info(self) -> Title:
            return title

        async def get_table_of_contents(self) -> list:
            return []

    with patch("app.services.client.RanobeLib", return_value=_FakeClient()):
        response = client.get("/friends")

    assert ("Test Novel" in response.text) is show_reading
    assert ("Пока нет активности" in response.text) is not show_reading
