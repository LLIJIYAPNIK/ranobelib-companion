"""End-to-end add/remove-from-library through the real ASGI app.

Same isolation strategy as tests/test_api_auth.py: the shared test Postgres database is
wiped and re-migrated per test (see tests/db_reset.py), with an explicit
`with TestClient(app) as client:` so app.main's lifespan (migrations) actually runs.
"""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import TitleNotFoundError
from ranobelib.models import Chapter, Cover, Label, Title, Volume

from app.config import get_settings
from app.db.connection import connection
from app.db.library import record_progress
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


def _register(client: TestClient, email: str = "alice@example.com") -> None:
    register(client, email)


def _fake_title(slug_url: str = "6712--test-novel", cover: Cover | None = None) -> Title:
    return Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url=slug_url,
        cover=cover or Cover(),
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


class _FakeChapterClient:
    def __init__(self, chapter: Chapter) -> None:
        self._chapter = chapter

    async def __aenter__(self) -> "_FakeChapterClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_chapter(
        self, volume: int, number: str, *, branch_id: int | None = None
    ) -> Chapter:
        return self._chapter

    async def get_table_of_contents(self) -> list[Volume]:
        return []


def test_add_requires_login(client: TestClient) -> None:
    response = client.post(
        "/library/6712--test-novel/add", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_remove_requires_login(client: TestClient) -> None:
    response = client.post(
        "/library/6712--test-novel/remove", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_add_then_remove_round_trip(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        add_response = client.post(
            "/library/6712--test-novel/add", follow_redirects=False
        )
        title_page = client.get("/titles/6712--test-novel/data")

    assert add_response.status_code == 303
    assert add_response.headers["location"] == "/titles/6712--test-novel"
    assert "Убрать из библиотеки" in title_page.text

    remove_response = client.post(
        "/library/6712--test-novel/remove", follow_redirects=False
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        title_page_after = client.get("/titles/6712--test-novel/data")

    assert remove_response.status_code == 303
    assert remove_response.headers["location"] == "/library"
    assert "Добавить в библиотеку" in title_page_after.text


def test_reading_a_chapter_auto_adds_title_and_the_page_reflects_it(
    client: TestClient,
) -> None:
    """PR 35: no explicit "Добавить" click here, just opening a chapter - the title
    page should already show the "in library" state on the very next load."""
    _register(client)
    title = _fake_title()
    chapter = Chapter(id=1, volume="1", number="1", content="<p>x</p>")

    with patch("app.services.client.RanobeLib", return_value=_FakeChapterClient(chapter)):
        chapter_response = client.get("/titles/6712--test-novel/chapters/1/1")
    assert chapter_response.status_code == 200

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        title_page = client.get("/titles/6712--test-novel/data")

    assert "Убрать из библиотеки" in title_page.text


async def test_title_page_shows_reading_progress_for_library_entry(client: TestClient) -> None:
    _register(client)
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 5)],
        )
    ]

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")

    async with connection() as conn:
        await record_progress(
            conn, user_id=1, slug_url="6712--test-novel", volume="1", number="3"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'aria-label="Прочитано 75%"' in response.text  # 3 of 4 chapters
    assert 'style="width: 75%"' in response.text
    # PR 240: same last-read chapter the progress bar above is based on.
    assert 'href="/titles/6712--test-novel/chapters/1/3"' in response.text
    assert "Продолжить · Глава 3" in response.text
    # PR 253: the TOC marks the chapters before it as read and this one as current.
    assert 'aria-current="step"' in response.text
    assert response.text.count("toc__chapter--read") == 2


def test_title_page_omits_reading_progress_when_not_in_library(client: TestClient) -> None:
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert "Прочитано" not in response.text
    assert 'class="title-progress' not in response.text
    assert "Продолжить ·" not in response.text


async def test_title_page_omits_continue_reading_link_when_last_read_chapter_is_gone(
    client: TestClient,
) -> None:
    # PR 240: shares reading_progress_percent()'s own "still in the table of contents"
    # check (app/reading_progress.py) - a chapter recorded as last-read that's since been
    # removed upstream shouldn't offer a link to a page that no longer exists.
    _register(client)
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 5)],
        )
    ]

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")

    async with connection() as conn:
        await record_progress(
            conn, user_id=1, slug_url="6712--test-novel", volume="1", number="99"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert "Прочитано" not in response.text
    assert "Продолжить ·" not in response.text
    assert 'aria-current="step"' not in response.text


def test_add_is_idempotent(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        second = client.post("/library/6712--test-novel/add", follow_redirects=False)

    assert second.status_code == 303  # not an error to add twice


def test_add_unknown_title_is_not_found(client: TestClient) -> None:
    _register(client)
    exc = TitleNotFoundError("6712--missing")

    class _RaisingClient:
        async def __aenter__(self) -> "_RaisingClient":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def get_info(self) -> Title:
            raise exc

    with patch("app.services.client.RanobeLib", return_value=_RaisingClient()):
        response = client.post("/library/6712--missing/add")

    assert response.status_code == 404


def test_add_honors_custom_next(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/6712--test-novel/add",
            data={"next": "/library"},
            follow_redirects=False,
        )

    assert response.headers["location"] == "/library"


def test_add_rejects_open_redirect_next(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/6712--test-novel/add",
            data={"next": "https://evil.example"},
            follow_redirects=False,
        )

    assert response.headers["location"] == "/titles/6712--test-novel"


def test_add_rejects_protocol_relative_next(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/6712--test-novel/add",
            data={"next": "//evil.example"},
            follow_redirects=False,
        )

    assert response.headers["location"] == "/titles/6712--test-novel"


def test_show_library_anonymous_is_viewable_but_prompts_to_log_in(client: TestClient) -> None:
    response = client.get("/library")

    assert response.status_code == 200
    assert 'href="/login"' in response.text
    assert 'href="/register"' in response.text
    assert 'href="/catalog"' in response.text  # locked-state CTA (PR 15)


def test_show_library_anonymous_gets_the_locked_state(client: TestClient) -> None:
    """PR 252 (LOCKED): the shared locked_feature() macro, not the reading list."""
    response = client.get("/library")

    assert 'data-role="locked-feature"' in response.text
    assert "Список читаемого скрыт" in response.text
    assert 'data-role="library-titles"' not in response.text
    assert "library-tabs__count" not in response.text  # PR 275: no counts for a guest


async def test_show_library_counts_titles_on_the_tabs(client: TestClient) -> None:
    """PR 275: "Читаю" counts started titles only."""
    _register(client)
    title_a = _fake_title(slug_url="1--first")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title_a)):
        client.post("/library/1--first/add")
        client.post("/library/2--second/add")
    async with connection() as conn:
        await record_progress(conn, user_id=1, slug_url="1--first", volume="1", number="2")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title_a)):
        response = client.get("/library")

    reading_tab = 'aria-current="page">Читаю<span class="library-tabs__count">1</span></a>'
    assert reading_tab in response.text
    assert response.text.count('class="library-tabs__count"') == 1


async def test_library_count_covers_every_title_on_both_pages(client: TestClient) -> None:
    """PR 294: the «Библиотека» switch item counts all titles, started or not - on the
    library page and on the catalog."""
    _register(client)
    title_a = _fake_title(slug_url="1--first")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title_a)):
        client.post("/library/1--first/add")
        client.post("/library/2--second/add")
    async with connection() as conn:
        await record_progress(conn, user_id=1, slug_url="1--first", volume="1", number="2")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title_a)):
        library = client.get("/library")
    with patch("app.api.library.list_genres", return_value=[]), patch(
        "app.api.library.list_countries", return_value=[]
    ), patch("app.api.library.get_catalog"), patch(
        "app.api.library.list_catalog_titles"
    ) as list_titles:
        list_titles.return_value.items = []
        list_titles.return_value.has_next_page = False
        catalog = client.get("/catalog")

    assert library.context["library_count"] == 2
    assert catalog.context["library_count"] == 2


def test_library_count_is_none_for_a_guest(client: TestClient) -> None:
    library = client.get("/library")
    with patch("app.api.library.list_genres", return_value=[]), patch(
        "app.api.library.list_countries", return_value=[]
    ), patch("app.api.library.get_catalog"), patch(
        "app.api.library.list_catalog_titles"
    ) as list_titles:
        list_titles.return_value.items = []
        list_titles.return_value.has_next_page = False
        catalog = client.get("/catalog")

    assert library.context["library_count"] is None
    assert catalog.context["library_count"] is None


def test_catalog_tabs_have_no_counts(client: TestClient) -> None:
    """The tabs partial is shared with the catalog, which has no size to show."""
    _register(client)

    with patch("app.api.library.list_genres", return_value=[]), patch(
        "app.api.library.list_countries", return_value=[]
    ), patch("app.api.library.get_catalog"), patch(
        "app.api.library.list_catalog_titles"
    ) as list_titles:
        list_titles.return_value.items = []
        list_titles.return_value.has_next_page = False
        response = client.get("/catalog")

    assert response.status_code == 200
    assert 'href="/catalog" aria-current="page"' in response.text
    assert "library-tabs__count" not in response.text


def test_add_by_url_error_keeps_the_reading_tab_active(client: TestClient) -> None:
    _register(client)

    response = client.post("/library/add", data={"url": "not a link"})

    assert response.status_code == 400
    assert 'href="/library" aria-current="page"' in response.text


def test_show_library_empty_state(client: TestClient) -> None:
    _register(client)

    response = client.get("/library")

    assert response.status_code == 200
    assert "Пока пусто" in response.text


def test_show_library_wires_the_tab_swipe_script(client: TestClient) -> None:
    # PR 211: mobile-only swipe-to-switch-tabs shortcut between /library and
    # /catalog.
    response = client.get("/library")

    assert response.status_code == 200
    assert "static/js/library-tabs-swipe.js" in response.text


async def test_show_library_lists_added_titles_with_progress(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")

    # Progress recording itself is covered by tests/test_chapters.py - here just check
    # the library page reflects it once it's there.
    async with connection() as conn:
        await record_progress(
            conn, user_id=1, slug_url="6712--test-novel", volume="1", number="5"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/library")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert "Том 1, глава 5" in response.text


async def test_show_library_renders_reading_progress_bar(client: TestClient) -> None:
    _register(client)
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 5)],
        )
    ]

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")

    async with connection() as conn:
        await record_progress(
            conn, user_id=1, slug_url="6712--test-novel", volume="1", number="2"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/library")

    assert response.status_code == 200
    assert 'class="ui-progress ui-progress--lg"' in response.text
    assert 'style="width: 50%"' in response.text  # 2 of 4 chapters
    # PR 252: the percent is also shown as text next to the bar.
    assert '<span class="ui-progress__pct">50%</span>' in response.text


def test_show_library_omits_progress_bar_for_unopened_titles(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert response.status_code == 200
    # PR 275: a never-opened title goes under "Ещё в библиотеке", not a "Читаю" card.
    assert "Ещё в библиотеке" in response.text
    assert "не начаты · 1" in response.text
    assert "Не начато" in response.text
    assert 'class="ui-progress' not in response.text
    assert "wn-library-card" not in response.text


def test_show_library_prefers_russian_name(client: TestClient) -> None:
    _register(client)
    title = Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert response.status_code == 200
    assert "Тестовый роман" in response.text
    assert "Test Novel" not in response.text


def test_show_library_renders_cover(client: TestClient) -> None:
    _register(client)
    title = _fake_title(cover=Cover(default="https://example.com/cover.jpg"))

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert 'src="https://example.com/cover.jpg"' in response.text


def test_show_library_survives_one_unreachable_title(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")

    class _RaisingClient:
        async def __aenter__(self) -> "_RaisingClient":
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def get_info(self) -> Title:
            raise TitleNotFoundError("6712--test-novel")

    with patch("app.services.client.RanobeLib", return_value=_RaisingClient()):
        response = client.get("/library")

    assert response.status_code == 200
    assert "6712--test-novel" in response.text


def test_add_by_url_resolves_and_adds(client: TestClient) -> None:
    _register(client)
    title = _fake_title()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.post(
            "/library/add",
            data={"url": "https://ranobelib.me/ru/book/6712--test-novel"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/titles/6712--test-novel"


def test_add_by_url_rejects_unparseable_input(client: TestClient) -> None:
    _register(client)

    response = client.post("/library/add", data={"url": "not a link at all"})

    assert response.status_code == 400
    assert "Не удалось распознать ссылку" in response.text


def test_add_by_url_requires_login(client: TestClient) -> None:
    response = client.post(
        "/library/add", data={"url": "https://ranobelib.me/ru/book/6712--x"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- PR 293: "Избранное" is gone ---------------------------------------------------


def test_favorite_toggle_route_is_gone(client: TestClient) -> None:
    _register(client)
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")

    response = client.post("/library/6712--test-novel/favorite")

    assert response.status_code == 404


def test_show_library_has_no_favorites(client: TestClient) -> None:
    """No star on the cards, no "Избранное" tab or card, no favorite-toggle.js."""
    _register(client)
    title = _fake_title(cover=Cover(default="https://example.com/cover.jpg"))
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert response.status_code == 200
    assert 'data-role="library-item"' in response.text
    assert "favorite" not in response.text
    assert "Избранн" not in response.text


@pytest.mark.parametrize("tab", ["favorites", "fav", "reading", "whatever"])
def test_old_library_tabs_redirect_to_the_library(client: TestClient, tab: str) -> None:
    response = client.get(f"/library?tab={tab}", follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == "/library"


def test_old_favorites_tab_drops_other_query_parameters(client: TestClient) -> None:
    response = client.get("/library?tab=fav&sort=name", follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == "/library"


def test_old_all_tab_redirects_to_the_catalog(client: TestClient) -> None:
    response = client.get("/library?tab=all", follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == "/catalog"


def test_old_all_tab_keeps_the_other_query_parameters(client: TestClient) -> None:
    response = client.get(
        "/library?tab=all&query=dragon&genres=1&genres=2&sort=views", follow_redirects=False
    )

    assert response.status_code == 301
    assert response.headers["location"] == (
        "/catalog?query=dragon&genres=1&genres=2&sort=views"
    )


def test_old_favorites_page_redirects_to_the_library(client: TestClient) -> None:
    response = client.get("/library/favorites", follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == "/library"


def test_library_without_a_tab_is_not_redirected(client: TestClient) -> None:
    _register(client)

    response = client.get("/library", follow_redirects=False)

    assert response.status_code == 200


# --- PR 205: default_translation_index ("перевод по умолчанию для тайтла") -------------


def _title_with_ambiguous_chapters() -> tuple[Title, list[Volume]]:
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1", branches_count=2),
                Chapter(id=2, volume="1", number="2", branches_count=3),
            ],
        )
    ]
    return title, volumes


def test_default_translation_dropdown_hidden_when_not_in_library(client: TestClient) -> None:
    title, volumes = _title_with_ambiguous_chapters()

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert "несколько переводов" in response.text  # the note itself still shows
    assert 'action="/library/6712--test-novel/default-translation"' not in response.text


def test_default_translation_dropdown_shown_for_library_entry(client: TestClient) -> None:
    _register(client)
    title, volumes = _title_with_ambiguous_chapters()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'action="/library/6712--test-novel/default-translation"' in response.text
    # max_branches across ambiguous chapters is 3 (the second chapter's own branches_count)
    assert '<option value="0"' in response.text
    assert '<option value="1"' in response.text
    assert '<option value="2"' in response.text
    assert '<option value="3"' not in response.text


def test_set_default_translation_requires_login(client: TestClient) -> None:
    response = client.post(
        "/library/6712--test-novel/default-translation",
        data={"translation_index": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_set_default_translation_requires_title_in_library(client: TestClient) -> None:
    _register(client)

    response = client.post(
        "/library/6712--test-novel/default-translation", data={"translation_index": "1"}
    )

    assert response.status_code == 404


def test_set_default_translation_persists_and_preselects(client: TestClient) -> None:
    _register(client)
    title, volumes = _title_with_ambiguous_chapters()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")

        save_response = client.post(
            "/library/6712--test-novel/default-translation",
            data={"translation_index": "1"},
            follow_redirects=False,
        )
        page = client.get("/titles/6712--test-novel/data")

    assert save_response.status_code == 303
    assert save_response.headers["location"] == "/titles/6712--test-novel"
    assert '<option value="1" selected>' in page.text


def test_set_default_translation_empty_clears_it(client: TestClient) -> None:
    _register(client)
    title, volumes = _title_with_ambiguous_chapters()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")
        client.post(
            "/library/6712--test-novel/default-translation", data={"translation_index": "1"}
        )

        client.post(
            "/library/6712--test-novel/default-translation", data={"translation_index": ""}
        )
        page = client.get("/titles/6712--test-novel/data")

    assert " selected>" not in page.text


def test_set_default_translation_rejects_garbage_value(client: TestClient) -> None:
    _register(client)
    title, volumes = _title_with_ambiguous_chapters()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        client.post("/library/6712--test-novel/add")

        response = client.post(
            "/library/6712--test-novel/default-translation",
            data={"translation_index": "not-a-number"},
        )

    assert response.status_code == 422


async def test_show_library_reading_card_has_continue_cta_and_last_read_line(
    client: TestClient,
) -> None:
    """PR 275: a started title's card links straight to the last-read chapter."""
    _register(client)
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
    async with connection() as conn:
        await record_progress(
            conn, user_id=1, slug_url="6712--test-novel", volume="2", number="7"
        )

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/library")

    assert 'href="/titles/6712--test-novel/chapters/2/7"' in response.text
    assert 'aria-label="Продолжить · Глава 7"' in response.text
    assert '<span class="wn-library-card__cta-verb">Продолжить · </span>Гл. 7' in response.text
    assert "Читали сегодня" in response.text
    assert "Ещё в библиотеке" not in response.text


def test_show_library_wires_the_toolbar_script(client: TestClient) -> None:
    _register(client)
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        client.post("/library/6712--test-novel/add")
        response = client.get("/library")

    assert "static/js/library-toolbar.js" in response.text
    # Hidden until library-toolbar.js shows it - without JS it would do nothing.
    assert 'data-role="library-toolbar" hidden' in response.text
