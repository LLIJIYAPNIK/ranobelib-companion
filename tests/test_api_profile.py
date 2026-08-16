"""GET /profile (PR 92) - the read-only account profile page.

PR 122 turned it into a public page (GET /profile/{user_id}) with two extra sections -
"Читает сейчас" and "Библиотека" - covered further down. PR 124's privacy toggles
("Приватность", settings_account.html) that gate those sections (plus PR 123's
"Избранное") for a non-owner visitor are covered at the very end.
"""

import itertools
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Cover, Label, Title

import app.db.connection as db_connection
from app.config import get_settings
from app.db.activity import record_chapter_read, record_heartbeat
from app.db.comments import create_comment
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


def test_profile_shows_zero_comments_for_a_user_with_none(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert "Комментариев: 0" in response.text


def test_profile_shows_the_users_comment_count(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    create_comment(get_connection(), alice_id, "6712--test-novel", "1", "5", "", 0, "hi")
    create_comment(get_connection(), alice_id, "6712--test-novel", "1", "5", "", 3, "there")

    response = client.get("/profile")

    assert response.status_code == 200
    assert "Комментариев: 2" in response.text


def test_profile_shows_an_empty_reading_calendar_with_no_history(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert 'class="reading-calendar"' in response.text
    # Every cell is level 0 - an empty history is a grid of empty cells, not a missing
    # grid or an error.
    assert 'reading-calendar__day--level-1' not in response.text
    assert response.text.count('reading-calendar__day--level-0') > 300


def test_profile_reading_calendar_marks_todays_activity(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    record_chapter_read(get_connection(), alice_id, "6712--test-novel", "1", "1")
    record_chapter_read(get_connection(), alice_id, "6712--test-novel", "1", "2")

    response = client.get("/profile")

    assert response.status_code == 200
    # The only day with any reading is automatically this user's own busiest day, so it's
    # level 4 (intensity is relative to the user's own max, not a fixed absolute scale).
    assert 'reading-calendar__day--level-4' in response.text
    today = datetime.now(UTC).date().strftime("%d.%m.%Y")
    # PR 140: no heartbeat recorded today, so the tooltip's time portion reads "0 мин" -
    # always shown, not omitted, same as a real day with reading but no active-time ticks.
    assert f'title="{today}: 2 главы, 0 мин"' in response.text


def test_profile_reading_calendar_tooltip_shows_active_time_under_an_hour(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    record_chapter_read(get_connection(), alice_id, "6712--test-novel", "1", "1")
    record_heartbeat(get_connection(), alice_id, "6712--test-novel", 1500)  # 25 min

    response = client.get("/profile")

    assert response.status_code == 200
    today = datetime.now(UTC).date().strftime("%d.%m.%Y")
    assert f'title="{today}: 1 глава, 25 мин"' in response.text


def test_profile_reading_calendar_tooltip_shows_active_time_over_an_hour(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    record_chapter_read(get_connection(), alice_id, "6712--test-novel", "1", "1")
    record_heartbeat(get_connection(), alice_id, "6712--test-novel", 5400)  # 1h 30min

    response = client.get("/profile")

    assert response.status_code == 200
    today = datetime.now(UTC).date().strftime("%d.%m.%Y")
    assert f'title="{today}: 1 глава, 1 ч 30 мин"' in response.text


def test_profile_reading_calendar_tooltip_handles_active_time_with_no_chapters_read(
    client: TestClient,
) -> None:
    """A day can have heartbeat ticks (the reader page stayed open) with zero
    chapter_read events, e.g. every open happened the day before - the two counters are
    independent, so the tooltip must handle either being present without the other."""
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    record_heartbeat(get_connection(), alice_id, "6712--test-novel", 600)  # 10 min

    response = client.get("/profile")

    assert response.status_code == 200
    today = datetime.now(UTC).date().strftime("%d.%m.%Y")
    assert f'title="{today}: нет прочитанных глав, 10 мин"' in response.text


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
    assert "Читает сейчас" not in response.text
    assert 'class="title-card-grid"' not in response.text
    # Unlike those two, PR 136's calendar section always renders (an empty history is a
    # grid of empty cells, not an omitted section) - so it's the one case where
    # class="profile-section" is expected even with nothing else on the page.
    assert response.text.count('class="profile-section"') == 1
    assert "Календарь чтения" in response.text


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


def test_public_profile_shows_the_owners_comment_count_to_another_visitor(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    create_comment(get_connection(), alice_id, "6712--test-novel", "1", "5", "", 0, "hi")
    _register(client, "bob@example.com")  # now viewing as a different, logged-in user

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Комментариев: 1" in response.text


# --- PR 123: the "Избранное" section --------------------------------------------------


def test_public_profile_shows_the_favorite_section_when_one_is_set(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    client.post("/library/6712--test-novel/favorite")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert '<h2 class="profile-section__title">Избранное</h2>' in response.text
    assert 'class="title-card title-card--favorite"' in response.text
    assert "Test Novel" in response.text


def test_public_profile_omits_the_favorite_section_when_nothing_is_favorited(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Избранное" not in response.text
    assert "title-card--favorite" not in response.text


def test_public_profile_favorite_section_updates_after_a_new_favorite_is_chosen(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title_a = _fake_title(slug_url="1--first")
    title_b = _fake_title(slug_url="2--second")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title_a)):
        client.post("/library/1--first/add")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title_b)):
        client.post("/library/2--second/add")
    client.post("/library/1--first/favorite")
    client.post("/library/2--second/favorite")  # replaces the previous favorite

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title_b)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    favorite_section = response.text.split('<h2 class="profile-section__title">Избранное</h2>')[
        1
    ].split("</section>")[0]
    assert "1--first" not in favorite_section
    assert "2--second" in favorite_section


# --- PR 124: privacy flags gate the three sections for non-owner visitors ------------


def _populate_full_profile(client: TestClient, title: Title) -> None:
    """Puts a title in the library, gives it a recorded reading position, and marks it
    favorite - so all three sections ("Читает сейчас"/"Избранное"/"Библиотека") would
    render for the very same title if nothing were hiding them."""
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post(f"/library/{title.slug_url}/add")
    record_progress(
        get_connection(),
        user_id=_user_id("alice@example.com"),
        slug_url=title.slug_url,
        volume="1",
        number="5",
    )
    client.post(f"/library/{title.slug_url}/favorite")


def _set_privacy(
    client: TestClient, *, reading: bool, favorite: bool, library: bool
) -> None:
    data = {}
    if reading:
        data["show_currently_reading"] = "on"
    if favorite:
        data["show_favorite"] = "on"
    if library:
        data["show_library"] = "on"
    client.post("/settings/account/privacy", data=data)


@pytest.mark.parametrize(
    ("show_reading", "show_favorite", "show_library"),
    list(itertools.product([True, False], repeat=3)),
)
def test_privacy_flags_gate_sections_for_a_non_owner_visitor(
    client: TestClient, show_reading: bool, show_favorite: bool, show_library: bool
) -> None:
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()
    _populate_full_profile(client, title)
    _set_privacy(
        client, reading=show_reading, favorite=show_favorite, library=show_library
    )
    _register(client, "bob@example.com")  # a different, logged-in visitor

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert ("Читает сейчас" in response.text) is show_reading
    assert ('<h2 class="profile-section__title">Избранное</h2>' in response.text) is (
        show_favorite
    )
    assert ('<h2 class="profile-section__title">Библиотека</h2>' in response.text) is (
        show_library
    )


def test_privacy_flags_do_not_affect_the_owners_own_view(client: TestClient) -> None:
    _register(client, "alice@example.com")
    title = _fake_title()
    _populate_full_profile(client, title)
    _set_privacy(client, reading=False, favorite=False, library=False)

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/profile")

    assert response.status_code == 200
    assert "Читает сейчас" in response.text
    assert '<h2 class="profile-section__title">Избранное</h2>' in response.text
    assert '<h2 class="profile-section__title">Библиотека</h2>' in response.text


def test_privacy_flags_default_to_showing_everything(client: TestClient) -> None:
    """No visit to /settings/account/privacy at all - PR 122's original, unconditional
    rendering must not change for a user who's never touched the new toggles."""
    _register(client, "alice@example.com")
    alice_id = _user_id("alice@example.com")
    title = _fake_title()
    _populate_full_profile(client, title)
    _register(client, "bob@example.com")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Читает сейчас" in response.text
    assert '<h2 class="profile-section__title">Избранное</h2>' in response.text
    assert '<h2 class="profile-section__title">Библиотека</h2>' in response.text
