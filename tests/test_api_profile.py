"""GET /profile (PR 92) - the read-only account profile page.

PR 122 turned it into a public page (GET /profile/{user_id}) with two extra sections -
"Читает сейчас" and "Библиотека" - covered further down. PR 124's privacy toggles
("Приватность", settings_account.html) that gate those sections for a non-owner
visitor are covered at the very end.
"""

import itertools
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Cover, Label, Title

from app.config import get_settings
from app.db.activity import record_chapter_read, record_heartbeat
from app.db.comments import create_comment
from app.db.connection import connection
from app.db.library import record_progress
from app.db.users import get_user_by_email
from tests.auth_helpers import register
from tests.db_reset import reset_app_database


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _register(client: TestClient, email: str, password: str = "hunter2pass") -> None:
    register(client, email, password)


async def _user_id(email: str) -> int:
    async with connection() as conn:
        user = await get_user_by_email(conn, email)
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


def _stat(html: str, label: str) -> str:
    """The hero figure over `label` («за год», «тайтлов», «комментариев» ...)."""
    stats = html.split('<dl class="pf-stats">')[1].split("</dl>")[0]
    marker = f'<dt class="pf-stats__label">{label}</dt>'
    assert marker in stats, label
    after = stats.split(marker)[1]
    return after.split('<dd class="pf-stats__value hm-num">')[1].split("</dd>")[0]


def _friend_count(html: str) -> str:
    """The number beside «Друзья» on the profile."""
    return html.split('data-role="profile-friend-count">')[1].split("</span>")[0]


async def test_anonymous_visitor_sees_a_locked_screen_instead_of_the_profile(
    client: TestClient,
) -> None:
    response = client.get("/profile")

    assert response.status_code == 200
    assert 'data-role="locked-feature"' in response.text
    assert 'class="profile__avatar"' not in response.text


async def test_profile_shows_the_avatar_and_email_when_no_nickname_is_set(
    client: TestClient,
) -> None:
    _register(client, "alice.wong@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert 'class="profile__avatar"' in response.text
    assert ">AW</div>" in response.text
    assert "alice.wong@example.com" in response.text


async def test_profile_shows_the_uploaded_avatar_image_over_initials(client: TestClient) -> None:
    _register(client, "alice.wong@example.com")
    client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")},
    )

    response = client.get("/profile")

    assert response.status_code == 200
    assert '<img class="avatar-img" src="/avatars/' in response.text
    assert ">AW</div>" not in response.text


async def test_public_profile_shows_the_owners_uploaded_avatar_image(client: TestClient) -> None:
    # PR 173: image-lightbox.js opens for .profile__avatar img regardless of whether the
    # visitor is the profile's own owner - both views render from the same profile.html,
    # so there's nothing in the markup itself that could differ between them, but this
    # nails that down rather than just assuming it from the shared template.
    _register(client, "alice.wong@example.com")
    client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")},
    )
    alice_id = await _user_id("alice.wong@example.com")
    _register(client, "bob@example.com")  # switches the session to Bob

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert '<img class="avatar-img" src="/avatars/' in response.text


async def test_profile_prefers_the_nickname_over_the_email(client: TestClient) -> None:
    _register(client, "alice.wong@example.com")
    client.post(
        "/settings/account",
        data={"email": "alice.wong@example.com", "nickname": "Bob Carter", "bio": ""},
    )

    response = client.get("/profile")

    assert response.status_code == 200
    assert ">Bob Carter</h1>" in response.text
    assert ">BC</div>" in response.text


async def test_profile_shows_the_bio_when_set(client: TestClient) -> None:
    _register(client, "alice@example.com")
    client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "", "bio": "Hello there"},
    )

    response = client.get("/profile")

    assert response.status_code == 200
    assert 'class="profile__bio"' in response.text
    assert "Hello there" in response.text


async def test_profile_shows_an_empty_state_when_no_bio_is_set(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert "не рассказал о себе" in response.text


async def test_profile_shows_the_registration_date(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert "На сайте с" in response.text


async def test_profile_shows_zero_comments_for_a_user_with_none(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert _stat(response.text, "комментариев") == "0"
    assert "Комментариев:" not in response.text  # not in the meta line any more


async def test_profile_shows_the_users_comment_count(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    async with connection() as conn:
        await create_comment(conn, alice_id, "6712--test-novel", "1", "5", "", 0, "hi")
        await create_comment(conn, alice_id, "6712--test-novel", "1", "5", "", 3, "there")

    response = client.get("/profile")

    assert response.status_code == 200
    assert _stat(response.text, "комментария") == "2"


async def test_profile_shows_an_empty_month_calendar_with_no_history(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    # «Активность»'s month calendar: a whole month of days, none of them read.
    assert 'data-role="activity-calendar"' in response.text
    assert 'class="ac-cal__grid"' in response.text
    assert "ac-day--read" not in response.text
    assert response.text.count('class="ac-day__tile hm-num"') >= 1
    assert "js/activity-calendar.js" in response.text


async def test_profile_month_calendar_marks_todays_reading(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    async with connection() as conn:
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "1")
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "2")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/profile")

    assert response.status_code == 200
    # PR 323: chapters opened, no reading time logged - the lowest level.
    assert "ac-day--read ac-day--l1" in response.text
    today = datetime.now(UTC).date().isoformat()
    panel = response.text.split(f'data-day-panel="{today}"')[1].split("</article>")[0]
    assert "Сегодня" in panel
    assert "2 главы" in panel
    assert "Test Novel" in panel


async def test_profile_month_calendar_shows_the_days_reading_time(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    async with connection() as conn:
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "1")
        await record_heartbeat(conn, alice_id, "6712--test-novel", 1500)  # 25 min

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get("/profile")

    assert "ac-day--read ac-day--l2" in response.text  # 15-45 min
    today = datetime.now(UTC).date().isoformat()
    panel = response.text.split(f'data-day-panel="{today}"')[1].split("</article>")[0]
    assert "25 мин" in panel
    assert "<b>25 мин</b> и 1 глава за месяц" in response.text


async def test_profile_calendar_leaves_continuing_to_the_hero(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    async with connection() as conn:
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "7")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        own = client.get("/profile")
        _register(client, "bob@example.com")
        other = client.get(f"/profile/{alice_id}")

    # Today's card has no «Читать дальше» of its own on either profile.
    assert "ac-continue" not in own.text
    assert "ac-continue" not in other.text
    assert "Test Novel" in other.text  # the day's book is still shown


async def test_profile_calendar_hides_the_days_books_when_reading_is_hidden(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    async with connection() as conn:
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "1")
    _set_privacy(client, reading=False, library=True)

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        own = client.get("/profile")
        _register(client, "bob@example.com")
        other = client.get(f"/profile/{alice_id}")

    today = datetime.now(UTC).date().isoformat()
    own_panel = own.text.split(f'data-day-panel="{today}"')[1].split("</article>")[0]
    other_panel = other.text.split(f'data-day-panel="{today}"')[1].split("</article>")[0]
    assert "Test Novel" in own_panel
    assert "1 глава" in other_panel
    assert "Test Novel" not in other_panel


async def test_profile_calendar_links_stay_on_the_profile(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    today = datetime.now(UTC).date().isoformat()

    own = client.get("/profile")
    _register(client, "bob@example.com")
    other = client.get(f"/profile/{alice_id}")

    assert f'href="/profile?day={today}"' in own.text
    assert 'href="/profile?month=' in own.text  # the previous month
    assert f'href="/profile/{alice_id}?day={today}"' in other.text
    assert "/activity?day=" not in other.text


async def test_profile_calendar_opens_a_month_on_its_summary(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    async with connection() as conn:
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "1")
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "2")
    today = datetime.now(UTC).date()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(_fake_title())):
        response = client.get(f"/profile?month={today:%Y-%m}")

    head = response.text.split('data-day-panel="month"')[1].split(">")[0]
    assert "hidden" not in head  # the month shown, not today
    panel = response.text.split('data-day-panel="month"')[1].split("</article>")[0]
    assert "1 день с чтением" in panel
    assert "Test Novel" in panel
    assert "2 главы" in panel


async def test_profile_calendar_opens_the_month_asked_for(client: TestClient) -> None:
    _register(client, "alice@example.com")
    today = datetime.now(UTC).date()
    previous = (today.replace(day=1) - timedelta(days=1)).replace(day=1)

    response = client.get(f"/profile?month={previous:%Y-%m}")
    bad = client.get("/profile?month=2026-13")

    assert response.status_code == 200
    assert f'href="/profile?month={today:%Y-%m}"' in response.text  # the arrow forward
    assert bad.status_code == 422


async def test_profile_has_an_edit_link_to_settings_account(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    # Beside the name, in the hero's name line.
    line = response.text.split('<div class="pf-id__line">')[1].split("</div>")[0]
    assert '<a class="pf-edit" href="/settings/account">' in line
    assert "Редактировать" in line


# --- PR 122: /profile/{user_id} - the public profile page ---------------------------


async def test_public_profile_by_id_shows_the_owners_info(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "alice@example.com" in response.text


async def test_public_profile_unknown_user_id_is_404(client: TestClient) -> None:
    response = client.get("/profile/999")

    assert response.status_code == 404


async def test_public_profile_is_viewable_while_logged_out(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    client.post("/logout")

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "alice@example.com" in response.text


async def test_public_profile_of_another_user_has_no_edit_link(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")  # switches the session to Bob

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Редактировать" not in response.text


async def test_public_profile_shows_currently_reading_when_a_position_is_recorded(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    async with connection() as conn:
        await record_progress(
            conn, user_id=alice_id, slug_url="6712--test-novel", volume="1", number="5"
        )
    _register(client, "bob@example.com")  # someone else looks at Alice's profile

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert 'id="profile-current-title"' in response.text
    assert "Читает сейчас" in response.text
    assert '<a class="pf-now__name" href="/titles/6712--test-novel">' in response.text
    # Someone else's book: open the title, not «Читать дальше» at the owner's chapter.
    assert 'href="/titles/6712--test-novel/chapters/1/5"' not in response.text
    assert ">Открыть тайтл</a>" in response.text
    assert "Test Novel" in response.text
    assert "Том 1, глава 5" in response.text


async def test_public_profile_omits_currently_reading_for_a_never_opened_entry(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Читает сейчас" not in response.text


async def test_public_profile_shows_the_library_grid(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert 'id="profile-library-title">Библиотека</h2>' in response.text
    assert 'data-role="profile-library"' in response.text
    assert "Test Novel" in response.text


async def test_public_profile_omits_both_new_sections_when_the_library_is_empty(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Читает сейчас" not in response.text
    assert 'data-role="profile-library"' not in response.text
    # Unlike those two, the reading calendar always renders - an empty month is a month
    # of unread days, not an omitted section.
    assert response.text.count('data-role="activity-calendar"') == 1
    assert _stat(response.text, "за год") == "0 мин"


async def test_public_profile_is_the_same_for_the_owner_and_a_different_visitor(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    async with connection() as conn:
        await record_progress(
            conn, user_id=alice_id, slug_url="6712--test-novel", volume="1", number="5"
        )
    _register(client, "bob@example.com")  # now viewing as a different, logged-in user

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Читает сейчас" in response.text
    assert 'id="profile-library-title">Библиотека</h2>' in response.text
    assert "Том 1, глава 5" in response.text


async def test_own_profile_continues_the_book_from_its_hero(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    async with connection() as conn:
        await record_progress(
            conn, user_id=alice_id, slug_url="6712--test-novel", volume="1", number="5"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/profile")

    hero = response.text.split('<section class="pf-hero')[1].split("</section>")[0]
    assert "Читает сейчас" in hero
    assert '<a class="hm-go pf-now__go" href="/titles/6712--test-novel/chapters/1/5">' in hero
    assert "Читать дальше<small>Глава 5</small>" in hero


async def test_public_profile_shows_the_owners_comment_count_to_another_visitor(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    async with connection() as conn:
        await create_comment(conn, alice_id, "6712--test-novel", "1", "5", "", 0, "hi")
    _register(client, "bob@example.com")  # now viewing as a different, logged-in user

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert _stat(response.text, "комментарий") == "1"


# --- PR 293: no "Избранное" section ------------------------------------------------


async def test_profile_has_no_favorite_section_even_with_a_stored_favorite(
    client: TestClient,
) -> None:
    """library_entries.is_favorite stays in the table (PR 293) but is no longer read."""
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    async with connection() as conn:
        await conn.execute(
            "UPDATE library_entries SET is_favorite = 1 WHERE user_id = %s", (alice_id,)
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        own = client.get("/profile")
        _register(client, "bob@example.com")
        public = client.get(f"/profile/{alice_id}")

    for response in (own, public):
        assert response.status_code == 200
        assert "Test Novel" in response.text
        assert "Избранн" not in response.text
        assert "favorite" not in response.text


# --- PR 124: privacy flags gate the sections for non-owner visitors ------------------


async def _populate_full_profile(client: TestClient, title: Title) -> None:
    """Puts a title in the library and gives it a recorded reading position - so both
    sections ("Читает сейчас"/"Библиотека") would render for the very same title if
    nothing were hiding them."""
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post(f"/library/{title.slug_url}/add")
    async with connection() as conn:
        await record_progress(
            conn,
            user_id=await _user_id("alice@example.com"),
            slug_url=title.slug_url,
            volume="1",
            number="5",
        )


def _set_privacy(client: TestClient, *, reading: bool, library: bool) -> None:
    data = {}
    if reading:
        data["show_currently_reading"] = "on"
    if library:
        data["show_library"] = "on"
    client.post("/settings/account/privacy", data=data)


@pytest.mark.parametrize(
    ("show_reading", "show_library"),
    list(itertools.product([True, False], repeat=2)),
)
async def test_privacy_flags_gate_sections_for_a_non_owner_visitor(
    client: TestClient, show_reading: bool, show_library: bool
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()
    await _populate_full_profile(client, title)
    _set_privacy(client, reading=show_reading, library=show_library)
    _register(client, "bob@example.com")  # a different, logged-in visitor

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert ("Читает сейчас" in response.text) is show_reading
    assert ('id="profile-library-title">Библиотека</h2>' in response.text) is (show_library)


async def test_privacy_flags_do_not_affect_the_owners_own_view(client: TestClient) -> None:
    _register(client, "alice@example.com")
    title = _fake_title()
    await _populate_full_profile(client, title)
    _set_privacy(client, reading=False, library=False)

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/profile")

    assert response.status_code == 200
    assert "Читает сейчас" in response.text
    assert 'id="profile-library-title">Библиотека</h2>' in response.text


async def test_privacy_flags_default_to_showing_everything(client: TestClient) -> None:
    """No visit to /settings/account/privacy at all - PR 122's original, unconditional
    rendering must not change for a user who's never touched the new toggles."""
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()
    await _populate_full_profile(client, title)
    _register(client, "bob@example.com")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert "Читает сейчас" in response.text
    assert 'id="profile-library-title">Библиотека</h2>' in response.text


# --- PR 201: "Друзья" section on the profile page + its full-list page ------------------


async def _befriend(user_a_id: int, user_b_id: int) -> None:
    from app.db.friendships import accept_request, send_request

    async with connection() as conn:
        await send_request(conn, requester_id=user_a_id, addressee_id=user_b_id)
        await accept_request(conn, user_id=user_b_id, requester_id=user_a_id)


async def test_profile_omits_friends_section_without_any_friends(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/profile")

    assert response.status_code == 200
    assert 'id="profile-friends-title"' not in response.text


async def test_profile_shows_a_friend_preview_with_a_count(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert 'id="profile-friends-title">Друзья</h2>' in response.text
    assert _friend_count(response.text) == "1"
    assert "bob@example.com" in response.text


async def test_profile_omits_show_all_link_within_the_preview_limit(
    client: TestClient,
) -> None:
    from app.api.profile import _FRIEND_PREVIEW_LIMIT

    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    assert _FRIEND_PREVIEW_LIMIT > 1  # sanity - a single friend must stay within it
    response = client.get(f"/profile/{alice_id}")

    assert f'href="/profile/{alice_id}/friends"' not in response.text


async def test_profile_shows_a_show_all_link_over_the_preview_limit(
    client: TestClient,
) -> None:
    from app.api.profile import _FRIEND_PREVIEW_LIMIT

    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    for i in range(_FRIEND_PREVIEW_LIMIT + 1):
        _register(client, f"friend{i}@example.com")
        friend_id = await _user_id(f"friend{i}@example.com")
        await _befriend(alice_id, friend_id)

    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    count = _FRIEND_PREVIEW_LIMIT + 1
    assert _friend_count(response.text) == str(count)
    assert f'href="/profile/{alice_id}/friends">Все</a>' in response.text
    assert "Все →" not in response.text
    # only the preview limit's worth of rows actually render on the profile page itself
    assert response.text.count('class="pf-friend"') == _FRIEND_PREVIEW_LIMIT


async def test_profile_friends_section_shows_on_the_owners_own_profile_too(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    response = client.get("/profile")

    assert response.status_code == 200
    assert 'id="profile-friends-title">Друзья</h2>' in response.text
    assert _friend_count(response.text) == "1"


async def test_profile_friends_page_lists_the_full_friend_list(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    response = client.get(f"/profile/{alice_id}/friends")

    assert response.status_code == 200
    assert "bob@example.com" in response.text
    assert f'href="/profile/{alice_id}"' in response.text  # back link


async def test_profile_friends_page_shows_empty_state_without_friends(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")

    response = client.get(f"/profile/{alice_id}/friends")

    assert response.status_code == 200
    assert "Пока нет друзей." in response.text


async def test_profile_friends_page_404s_for_an_unknown_user(client: TestClient) -> None:
    response = client.get("/profile/999/friends")

    assert response.status_code == 404


# --- PR 202: show_friends gates both the profile section and the full list page --------


def _hide_friends(client: TestClient) -> None:
    client.post(
        "/settings/account/privacy",
        data={
            "show_currently_reading": "on",
            "show_library": "on",
            "show_friends_activity_home": "on",
            # show_friends deliberately omitted - an unchecked checkbox
        },
    )


async def test_profile_hides_the_friends_section_from_others_when_opted_out(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    _hide_friends(client)

    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "hunter2pass"})
    response = client.get(f"/profile/{alice_id}")

    assert response.status_code == 200
    assert 'id="profile-friends-title"' not in response.text


async def test_profile_still_shows_the_friends_section_to_its_own_owner(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    _hide_friends(client)

    response = client.get("/profile")

    assert response.status_code == 200
    assert 'id="profile-friends-title">Друзья</h2>' in response.text
    assert _friend_count(response.text) == "1"


async def test_profile_friends_page_hides_the_list_from_others_when_opted_out(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    _hide_friends(client)

    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "hunter2pass"})
    response = client.get(f"/profile/{alice_id}/friends")

    assert response.status_code == 200
    # Bob's own email legitimately appears in the page header regardless (he's the logged-
    # in viewer) - what must actually be absent is his row in the friend list itself.
    assert 'ui-friend-row__name">bob@example.com</span>' not in response.text
    assert "Пока нет друзей." in response.text


async def test_profile_friends_page_still_shows_the_list_to_its_own_owner(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "hunter2pass"})
    _hide_friends(client)

    response = client.get(f"/profile/{alice_id}/friends")

    assert response.status_code == 200
    assert "bob@example.com" in response.text


async def test_profile_friends_page_hides_the_list_from_an_anonymous_visitor_when_opted_out(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _hide_friends(client)

    client.post("/logout")
    response = client.get(f"/profile/{alice_id}/friends")

    assert response.status_code == 200
    assert "Пока нет друзей." in response.text


# PR 277 (Webnovells Redesign, screen A5).


async def test_profile_hero_shows_the_stat_pills(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/profile")

    assert _stat(response.text, "за год") == "0 мин"
    assert _stat(response.text, "тайтл") == "1"
    assert _stat(response.text, "друзей") == "0"
    assert _stat(response.text, "комментариев") == "0"
    assert alice_id  # the owner's own /profile


async def test_profile_offers_add_bio_only_on_the_owners_own_profile(client: TestClient) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")

    own = client.get("/profile")
    _register(client, "bob@example.com")
    other = client.get(f"/profile/{alice_id}")

    assert "Добавить о себе" in own.text
    assert "Добавить о себе" not in other.text
    assert "Пользователь пока не рассказал о себе." in other.text


@pytest.mark.parametrize("show_reading", [True, False])
async def test_profile_friend_preview_shows_what_a_friend_reads_unless_hidden(
    client: TestClient, show_reading: bool
) -> None:
    _register(client, "alice@example.com")
    alice_id = await _user_id("alice@example.com")
    _register(client, "bob@example.com")  # the client is now bob
    bob_id = await _user_id("bob@example.com")
    await _befriend(alice_id, bob_id)
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    async with connection() as conn:
        await record_progress(conn, user_id=bob_id, slug_url=title.slug_url, volume="1", number="2")
    _set_privacy(client, reading=show_reading, library=True)

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(f"/profile/{alice_id}")

    assert ("Читает «Test Novel»" in response.text) is show_reading
    assert ("Пока нет активности" in response.text) is not show_reading


async def test_profile_library_preview_shows_at_most_ten_titles(client: TestClient) -> None:
    from app.api.profile import _LIBRARY_PREVIEW_LIMIT

    _register(client, "alice@example.com")
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        for i in range(_LIBRARY_PREVIEW_LIMIT + 2):
            client.post(f"/library/{i}--novel/add")
        response = client.get("/profile")

    assert _LIBRARY_PREVIEW_LIMIT == 10
    assert response.text.count('<li class="hm-card pf-book">') == 10
    assert "12 тайтлов" in response.text
    assert 'href="/library">Открыть библиотеку</a>' in response.text
    assert "Открыть библиотеку →" not in response.text
