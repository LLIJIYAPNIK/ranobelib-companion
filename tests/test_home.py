from base64 import urlsafe_b64encode
from collections.abc import Iterator
from json import dumps
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib.models import Chapter, Cover, Label, Title, Volume

from app.main import app
from tests.auth_helpers import register

client = TestClient(app)


def test_home_renders_search_form() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'action="/titles/open"' in response.text
    assert 'name="url"' in response.text
    # PR 210: the search field's autofocus is blurred back off on mobile, so its keyboard
    # doesn't pop the instant the page loads.
    assert "static/js/disable-mobile-autofocus.js" in response.text


def test_home_shows_empty_state_when_no_recent_titles() -> None:
    """PR 198: a first-time visitor (no recent_titles cookie at all) sees an explanation
    of the page instead of a blank space under the search field."""
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-role="home-empty"' in response.text
    assert "RanobeLib" in response.text
    assert 'data-role="recent-titles"' not in response.text


def test_home_omits_empty_state_when_recent_titles_exist() -> None:
    payload = dumps([{"slug_url": "6712--test-novel", "name": "Test Novel"}]).encode("utf-8")
    test_client = TestClient(app)
    test_client.cookies.set("recent_titles", urlsafe_b64encode(payload).decode("ascii"))

    response = test_client.get("/")

    assert response.status_code == 200
    assert 'data-role="recent-titles"' in response.text
    assert 'data-role="home-empty"' not in response.text


def test_home_empty_state_links_to_catalog() -> None:
    """PR 250 (HOME-EMPTY): the empty card offers the catalog as a next step."""
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/catalog"' in response.text
    assert "Открыть каталог" in response.text


def test_home_shows_guest_banner_to_anonymous_visitor() -> None:
    """PR 250 (HOME-GUEST): a guest sees the sign-in pitch, its links open the auth modal
    and still point at /login and /register without JS."""
    response = client.get("/")

    assert response.status_code == 200
    assert 'class="home-guest"' in response.text
    assert 'href="/register" data-role="auth-modal-trigger"' in response.text


def test_home_recent_card_keeps_forget_button() -> None:
    payload = dumps([{"slug_url": "6712--test-novel", "name": "Test Novel"}]).encode("utf-8")
    test_client = TestClient(app)
    test_client.cookies.set("recent_titles", urlsafe_b64encode(payload).decode("ascii"))

    response = test_client.get("/")

    assert 'class="ui-title-card"' in response.text
    assert 'data-role="forget-recent-title"' in response.text
    assert 'data-slug-url="6712--test-novel"' in response.text
    assert 'class="home__grid wn-home__recent-grid"' in response.text
    assert 'href="#home-open-url" data-role="focus-open-title"' in response.text


async def test_home_dashboard_reuses_existing_services_and_limits_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import home as home_api

    conn = object()

    class _ConnectionContext:
        async def __aenter__(self) -> object:
            return conn

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    reading_items = [
        {"entry": SimpleNamespace(last_read_at=None), "name": "Unread"},
        *[
            {"entry": SimpleNamespace(last_read_at=f"2026-10-0{day}"), "name": f"Read {day}"}
            for day in range(1, 5)
        ],
    ]
    summary = home_api.ActivitySummary([], 2, "8 мин", [], [])

    async def fake_library_items(user: object, passed_conn: object) -> list[dict[str, object]]:
        assert passed_conn is conn
        return reading_items

    async def fake_summary(user: object, passed_conn: object) -> home_api.ActivitySummary:
        assert passed_conn is conn
        return summary

    async def fake_downloads(
        passed_conn: object, user_id: int, limit: int
    ) -> list[home_api.DownloadHistoryEntry]:
        assert (passed_conn, user_id, limit) == (conn, 42, 3)
        return []

    monkeypatch.setattr(home_api, "connection", _ConnectionContext)
    monkeypatch.setattr(home_api, "library_items_for_user", fake_library_items)
    monkeypatch.setattr(home_api, "build_activity_summary", fake_summary)
    monkeypatch.setattr(home_api, "list_download_history", fake_downloads)

    dashboard = await home_api._home_dashboard(SimpleNamespace(id=42))  # type: ignore[arg-type]

    assert dashboard is not None
    assert [item["name"] for item in dashboard.reading] == ["Read 1", "Read 2", "Read 3"]
    assert dashboard.hero == dashboard.reading[0]
    assert dashboard.summary is summary


def test_home_renders_signed_in_cinematic_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import Request

    from app.api import home as home_api
    from app.auth.dependencies import get_current_user
    from app.db.users import User

    user = User(42, "reader@example.com", "hash", "2026-10-01T07:00:00+00:00")
    entry = SimpleNamespace(
        slug_url="6712--test-novel",
        last_read_at="2026-10-01T08:00:00+00:00",
        last_read_volume="1",
        last_read_number="92",
    )
    item = {
        "entry": entry,
        "name": "Старшая школа D×D",
        "cover_url": "https://example.test/cover.jpg",
        "progress_percent": 41,
    }
    summary = home_api.ActivitySummary([], 1, "8 мин", [], [])
    download = home_api.DownloadHistoryEntry(
        1,
        42,
        "6712--test-novel",
        "epub",
        "done",
        92,
        None,
        "2026-10-01T08:30:00+00:00",
        None,
    )
    dashboard = home_api.HomeDashboard([item], summary, [download])
    friend_card = home_api.FriendActivityCard(
        home_api.FriendUser(7, "Alice", None, "AL"),
        None,
        [
            home_api.RecentComment(
                9,
                "Отличная глава!",
                "2026-10-01T08:10:00+00:00",
                "6712--test-novel",
                "1",
                "5",
                "",
            )
        ],
        0,
    )

    async def override_current_user(request: Request) -> object:
        request.state.current_user = user
        return user

    async def fake_dashboard(passed_user: object) -> home_api.HomeDashboard:
        assert passed_user is user
        return dashboard

    async def fake_recent(request: Request, passed_user: object) -> list[dict[str, object]]:
        assert passed_user is user
        return []

    async def fake_friend_activity(passed_user: object) -> list[home_api.FriendActivityCard]:
        assert passed_user is user
        return [friend_card]

    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(home_api, "_home_dashboard", fake_dashboard)
    monkeypatch.setattr(home_api, "_recent_with_progress", fake_recent)
    monkeypatch.setattr(home_api, "_friend_activity_cards", fake_friend_activity)
    try:
        response = client.get("/")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert 'data-role="home-reading-hero"' in response.text
    assert 'class="wn-library-hero__backdrop"' in response.text
    assert "Продолжить · Гл. 92" in response.text
    assert 'href="/titles/6712--test-novel#title-panel-toc"' in response.text
    assert 'aria-label="Прогресс чтения" aria-valuenow="41"' in response.text
    assert 'id="today-title"' in response.text
    assert 'class="wn-home-reading"' in response.text
    assert 'id="latest-downloads-title"' in response.text
    assert "Отличная глава!" in response.text
    assert "/titles/6712--test-novel/chapters/1/5" in response.text


def _set_recent_cookie(test_client: TestClient, slug_url: str, name: str) -> None:
    payload = dumps([{"slug_url": slug_url, "name": name}]).encode("utf-8")
    test_client.cookies.set("recent_titles", urlsafe_b64encode(payload).decode("ascii"))


def _fake_title(slug_url: str = "6712--test-novel", name: str = "Test Novel") -> Title:
    return Title(
        id=6712,
        name=name,
        slug=slug_url.split("--", 1)[1],
        slug_url=slug_url,
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _FakeClient:
    def __init__(self, title: Title, volumes: list[Volume] | None = None) -> None:
        self._title = title
        self._volumes = volumes or []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

    async def get_table_of_contents(self) -> list[Volume]:
        return self._volumes

    async def estimate_title_size(self) -> int:
        return 0


@pytest.fixture
def db_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """PR 68's progress lookup needs a real logged-in user with a library entry, so
    unlike the plain `client` above this needs the app's DB - same isolation strategy as
    tests/test_api_library.py: the shared test Postgres database is wiped and re-migrated
    per test, with an explicit `with TestClient(app) as client:` so app.main's lifespan
    (migrations) actually runs."""
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")

    from app.config import get_settings
    from tests.db_reset import reset_app_database

    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _register(test_client: TestClient, email: str = "alice@example.com") -> None:
    register(test_client, email)


def test_home_hides_guest_banner_for_logged_in_user(db_client: TestClient) -> None:
    _register(db_client)

    response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="home-guest"' not in response.text


def test_home_omits_progress_for_anonymous_visitor(db_client: TestClient) -> None:
    """No account, so nothing to match the recent-titles cookie against (PR 68)."""
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    response = db_client.get("/")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert 'class="ui-progress"' not in response.text


def test_home_omits_progress_when_title_not_in_library(db_client: TestClient) -> None:
    """Logged in, but this particular recent title was only opened from its description
    page and never added to the personal library - still nothing to show."""
    _register(db_client)
    title = _fake_title()
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="ui-progress"' not in response.text


def test_home_omits_progress_when_never_read(db_client: TestClient) -> None:
    """In the library, but no chapter opened yet - PR 27's own rule for "Читаю" applies
    here the same way: no recorded position, no percentage to show."""
    _register(db_client)
    title = _fake_title()
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        db_client.post("/library/6712--test-novel/add")
        response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="ui-progress"' not in response.text


async def test_home_shows_progress_bar_for_logged_in_user_with_recorded_progress(
    db_client: TestClient,
) -> None:
    from app.db.connection import connection
    from app.db.library import record_progress

    _register(db_client)
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 5)],
        )
    ]
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        db_client.post("/library/6712--test-novel/add")

    async with connection() as conn:
        await record_progress(
            conn, user_id=1, slug_url="6712--test-novel", volume="1", number="3"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="ui-progress"' in response.text
    assert 'style="width: 75%"' in response.text  # 3 of 4 chapters
    # PR 250: the percent is also shown as text next to the bar.
    assert '<span class="ui-progress__pct">75%</span>' in response.text


# --- PR 200: friend activity column ------------------------------------------------------


async def _user_id(email: str) -> int:
    from app.db.connection import connection
    from app.db.users import get_user_by_email

    async with connection() as conn:
        user = await get_user_by_email(conn, email)
    assert user is not None
    return user.id


async def _make_friends(
    client: TestClient, alice_email: str = "alice@example.com", bob_email: str = "bob@example.com"
) -> tuple[int, int]:
    """Registers both, sends Bob -> Alice, and accepts it - leaves the client's session
    logged in as Alice, same as tests/test_api_friendships.py's own multi-user flow."""
    _register(client, alice_email)
    alice_id = await _user_id(alice_email)
    _register(client, bob_email)  # switches the session to Bob
    bob_id = await _user_id(bob_email)
    client.post(f"/friends/{alice_id}/request")
    client.post("/logout")
    client.post("/login", data={"email": alice_email, "password": "hunter2pass"})
    client.post(f"/friends/{bob_id}/accept")
    return alice_id, bob_id


def _login_as_bob(client: TestClient) -> None:
    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "hunter2pass"})


async def test_home_omits_friend_column_for_anonymous_visitor(db_client: TestClient) -> None:
    response = db_client.get("/")

    assert response.status_code == 200
    assert 'class="home__friends"' not in response.text


async def test_home_omits_friend_column_without_friends(db_client: TestClient) -> None:
    _register(db_client)

    response = db_client.get("/")

    assert 'class="home__friends"' not in response.text


async def test_home_shows_a_card_for_each_friend_with_no_activity(
    db_client: TestClient,
) -> None:
    await _make_friends(db_client)  # session is Alice

    _login_as_bob(db_client)
    response = db_client.get("/")

    assert 'class="home__friends"' in response.text
    assert "alice@example.com" in response.text
    assert "Пока нет активности" in response.text


async def test_home_shows_what_a_friend_is_currently_reading(db_client: TestClient) -> None:
    from app.db.connection import connection
    from app.db.library import add_entry, record_progress

    alice_id, _ = await _make_friends(db_client)  # session is Alice
    async with connection() as conn:
        await add_entry(conn, alice_id, "6712--test-novel")
        await record_progress(conn, alice_id, "6712--test-novel", volume="1", number="3")

    _login_as_bob(db_client)
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = db_client.get("/")

    assert "Читает" in response.text
    assert "Test Novel" in response.text


async def test_home_hides_a_friends_currently_reading_when_they_opted_out(
    db_client: TestClient,
) -> None:
    from app.db.connection import connection
    from app.db.library import add_entry, record_progress
    from app.db.users import update_privacy_settings

    alice_id, _ = await _make_friends(db_client)  # session is Alice
    async with connection() as conn:
        await add_entry(conn, alice_id, "6712--test-novel")
        await record_progress(conn, alice_id, "6712--test-novel", volume="1", number="3")
        await update_privacy_settings(
            conn,
            alice_id,
            show_currently_reading=False,
            show_library=True,
            show_friends_activity_home=True,
            show_friends=True,
        )

    _login_as_bob(db_client)
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = db_client.get("/")

    assert "Читает" not in response.text


async def test_home_shows_a_friends_recent_comment(db_client: TestClient) -> None:
    from app.db.comments import create_comment
    from app.db.connection import connection

    alice_id, _ = await _make_friends(db_client)  # session is Alice
    async with connection() as conn:
        await create_comment(
            conn, alice_id, "6712--test-novel", "1", "5", "", 0, "Отличная глава!"
        )

    _login_as_bob(db_client)
    response = db_client.get("/")

    assert "Отличная глава!" in response.text
    assert "/titles/6712--test-novel/chapters/1/5" in response.text


async def test_home_shows_a_friends_reading_streak(db_client: TestClient) -> None:
    from app.db.activity import record_chapter_read
    from app.db.connection import connection

    alice_id, _ = await _make_friends(db_client)  # session is Alice
    async with connection() as conn:
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "5")

    _login_as_bob(db_client)
    response = db_client.get("/")

    assert "1 день подряд" in response.text


# --- PR 202: show_friends_activity_home gates the column on the viewer's OWN home page ---


async def test_home_hides_the_friend_column_when_the_viewer_opted_out(
    db_client: TestClient,
) -> None:
    from app.db.activity import record_chapter_read
    from app.db.connection import connection
    from app.db.users import update_privacy_settings

    alice_id, bob_id = await _make_friends(db_client)  # session is Alice
    async with connection() as conn:
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "5")

    _login_as_bob(db_client)
    async with connection() as conn:
        # Bob is opting out of seeing *his own* home page's friend-activity column - not
        # about what Alice shares.
        await update_privacy_settings(
            conn,
            bob_id,
            show_currently_reading=True,
            show_library=True,
            show_friends_activity_home=False,
            show_friends=True,
        )
    response = db_client.get("/")

    assert 'class="home__friends"' not in response.text
    assert "1 день подряд" not in response.text
