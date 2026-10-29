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
    assert 'method="get" action="/catalog" role="search"' in response.text
    assert 'type="search"' in response.text
    assert 'name="query"' in response.text
    assert 'enterkeyhint="search"' in response.text
    assert '<button class="hm-search__submit" type="submit">Найти</button>' in response.text
    # PR 210: the search field's autofocus is blurred back off on mobile, so its keyboard
    # doesn't pop the instant the page loads.
    assert "static/js/disable-mobile-autofocus.js" in response.text


def test_home_shows_empty_state_when_no_recent_titles() -> None:
    """PR 198: a first-time visitor (no recent_titles cookie at all) sees what the site
    is instead of a blank space - and no «Недавно открытые» with nothing in it."""
    response = client.get("/")

    assert response.status_code == 200
    assert 'class="hm-intro' in response.text
    assert "Онлайн-чтение по главам" in response.text
    assert 'data-role="recent-titles"' not in response.text


def test_home_omits_empty_state_when_recent_titles_exist() -> None:
    payload = dumps([{"slug_url": "6712--test-novel", "name": "Test Novel"}]).encode("utf-8")
    test_client = TestClient(app)
    test_client.cookies.set("recent_titles", urlsafe_b64encode(payload).decode("ascii"))

    response = test_client.get("/")

    assert response.status_code == 200
    # A guest's recently opened titles show too, under the intro.
    assert 'data-role="recent-titles"' in response.text
    assert 'data-role="recent-item"' in response.text


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
    assert 'id="home-account-title"' in response.text
    assert 'href="/register" data-role="auth-modal-trigger"' in response.text


def test_home_recent_card_keeps_forget_button() -> None:
    payload = dumps([{"slug_url": "6712--test-novel", "name": "Test Novel"}]).encode("utf-8")
    test_client = TestClient(app)
    test_client.cookies.set("recent_titles", urlsafe_b64encode(payload).decode("ascii"))

    response = test_client.get("/")

    assert 'data-role="recent-item"' in response.text
    assert 'data-role="forget-recent-title"' in response.text
    assert 'data-slug-url="6712--test-novel"' in response.text
    assert 'data-role="focus-open-title"' not in response.text


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

    monkeypatch.setattr(home_api, "connection", _ConnectionContext)
    monkeypatch.setattr(home_api, "library_items_for_user", fake_library_items)
    monkeypatch.setattr(home_api, "build_activity_summary", fake_summary)

    dashboard = await home_api._home_dashboard(
        SimpleNamespace(id=42, timezone=None)  # type: ignore[arg-type]
    )

    assert dashboard is not None
    # Only titles with a reading position, in order (up to _READING_LIMIT).
    assert [item["name"] for item in dashboard.reading] == [f"Read {d}" for d in range(1, 5)]
    assert dashboard.hero == dashboard.reading[0]
    assert dashboard.summary is summary


def _render_signed_in_home(
    monkeypatch: pytest.MonkeyPatch, query: str = "", book: object = None
) -> str:
    """GET / as a reader with two books on the go, every data source stubbed."""
    from fastapi import Request

    from app.api import home as home_api
    from app.auth.dependencies import get_current_user
    from app.db.users import User

    user = User(42, "reader@example.com", "hash", "2026-10-01T07:00:00+00:00")

    def reading_item(slug: str, name: str, number: str, percent: int) -> dict[str, object]:
        entry = SimpleNamespace(
            slug_url=slug,
            last_read_at="2026-10-01T08:00:00+00:00",
            last_read_volume="1",
            last_read_number=number,
        )
        return {"entry": entry, "name": name, "cover_url": None, "progress_percent": percent}

    summary = home_api.ActivitySummary([], 1, "8 мин", [], [])
    dashboard = home_api.HomeDashboard(
        [
            reading_item("6712--test-novel", "Старшая школа D×D", "92", 41),
            reading_item("7000--other-novel", "Другая новелла", "5", 12),
        ],
        summary,
    )

    async def override_current_user(request: Request) -> object:
        request.state.current_user = user
        return user

    async def fake_dashboard(passed_user: object) -> home_api.HomeDashboard:
        assert passed_user is user
        return dashboard

    async def fake_book(item: object) -> object:
        return book

    async def no_recent(request: Request, passed_user: object) -> list[dict[str, object]]:
        raise AssertionError("«Недавние» aren't looked up for a reader with a book on the go")

    async def no_popular(limit: int = 12) -> list[Title]:
        raise AssertionError("no popular shelf for a reader with a book on the go")

    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(home_api, "_home_dashboard", fake_dashboard)
    monkeypatch.setattr(home_api, "book_focus", fake_book)
    monkeypatch.setattr(home_api, "_recent_with_progress", no_recent)
    monkeypatch.setattr(home_api, "popular_titles", no_popular)
    try:
        response = TestClient(app).get("/" + query)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    return response.text


def test_home_is_the_readers_library_shelf(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _render_signed_in_home(monkeypatch)

    assert 'class="hm hm--v hm--flow"' in text
    assert 'data-role="home-reading-hero"' in text
    # Every book on the shelf, the book on the go in front, each with its own details.
    assert text.count("data-flow-book=") == 2
    assert text.count("data-flow-panel=") == 2
    assert 'class="hl-flow__book is-active"' in text
    assert 'href="/titles/6712--test-novel/chapters/1/92"' in text
    assert 'href="/titles/7000--other-novel/chapters/1/5"' in text
    assert 'aria-label="Прочитано 41%"' in text
    assert 'aria-label="Прочитано 12%"' in text
    assert "js/home-flow.js" in text
    # Nothing but the library.
    for absent in ('id="friends-reading-title"', 'id="popular-title"', 'id="downloads-title"'):
        assert absent not in text
    assert "подряд" not in text


def test_home_shows_each_books_description_and_chapters_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.book_focus import BookChapter, BookFocus

    book = BookFocus(
        summary="Описание книги.",
        status="Онгоинг",
        position=92,
        total=120,
        chapters=[
            BookChapter("1", "92", "Банкет", True),
            BookChapter("1", "93", "Письмо", False),
        ],
    )
    text = _render_signed_in_home(monkeypatch, book=book)

    # The same focus for both books in this stub: both panels carry it.
    assert text.count("Описание книги.") == 2
    assert "из 120" in text
    assert 'href="/titles/6712--test-novel/chapters/1/93"' in text
    assert 'href="/titles/7000--other-novel/chapters/1/93"' in text
    assert 'aria-current="step"' in text
    assert 'href="/titles/6712--test-novel#title-panel-toc"' in text


async def test_book_focus_finds_the_current_chapter_in_sdk_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import book_focus as focus_module
    from app.services.titles import TitleDetails

    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=i, volume="1", number=str(i), name=f"Глава {i}") for i in range(1, 10)
            ],
        )
    ]

    async def fake_details(slug_url: str) -> TitleDetails:
        return TitleDetails("Тайтл", None, "Онгоинг", volumes, "Описание")

    monkeypatch.setattr(focus_module, "title_details", fake_details)
    item = {"entry": SimpleNamespace(slug_url="1--t", last_read_volume="1", last_read_number="3")}
    focus = await focus_module.book_focus(item)

    assert focus is not None
    assert (focus.position, focus.total, focus.summary) == (3, 9, "Описание")
    assert [c.number for c in focus.chapters] == ["3", "4", "5", "6"]
    assert focus.chapters[0].is_current


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
    assert "hm-cover__bar" not in response.text


def test_home_omits_progress_when_title_not_in_library(db_client: TestClient) -> None:
    """Logged in, but this particular recent title was only opened from its description
    page and never added to the personal library - still nothing to show."""
    _register(db_client)
    title = _fake_title()
    _set_recent_cookie(db_client, "6712--test-novel", "Test Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = db_client.get("/")

    assert response.status_code == 200
    assert "hm-cover__bar" not in response.text


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
    assert "hm-cover__bar" not in response.text


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
        await record_progress(conn, user_id=1, slug_url="6712--test-novel", volume="1", number="3")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = db_client.get("/")

    assert response.status_code == 200
    assert 'aria-label="Прочитано 75%"' in response.text
    assert 'style="width: 75%"' in response.text  # 3 of 4 chapters
    # PR 250: the percent is also shown as text next to the bar.
    assert '<span class="hm-num">75%</span>' in response.text


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
    assert 'id="friends-reading-title"' not in response.text


async def test_home_does_not_show_what_friends_read(db_client: TestClient) -> None:
    """The home page is the reader's library - a friend's book, comment or streak isn't
    on it (that's for «Активность»)."""
    from app.db.activity import record_chapter_read
    from app.db.comments import create_comment
    from app.db.connection import connection
    from app.db.library import add_entry, record_progress

    alice_id, _ = await _make_friends(db_client)  # session is Alice
    async with connection() as conn:
        await add_entry(conn, alice_id, "6712--test-novel")
        await record_progress(conn, alice_id, "6712--test-novel", volume="1", number="5")
        await record_chapter_read(conn, alice_id, "6712--test-novel", "1", "5")
        await create_comment(conn, alice_id, "6712--test-novel", "1", "5", "", 0, "Отличная глава!")

    _login_as_bob(db_client)
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = db_client.get("/")

    assert 'id="friends-reading-title"' not in response.text
    assert "Отличная глава!" not in response.text
    assert "подряд" not in response.text


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

    assert 'id="friends-reading-title"' not in response.text
    assert "1 день подряд" not in response.text
