from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from ranobelib import (
    AuthRequiredError,
    ChapterNotFoundError,
    MultipleTranslationsError,
    RateLimitError,
)
from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

import app.db.connection as db_connection
from app.config import get_settings
from app.db.activity import list_chapters_read_today
from app.db.connection import get_connection
from app.db.library import add_entry, get_entry, list_entries
from app.main import app

client = TestClient(app)


class _FakeClient:
    def __init__(
        self,
        chapter: Chapter | None = None,
        exc: Exception | None = None,
        volumes: list[Volume] | None = None,
    ) -> None:
        self._chapter = chapter
        self._exc = exc
        self._volumes = volumes or []
        self.received_branch_id: int | None | str = "not called"

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_chapter(
        self, volume: int, number: str, *, branch_id: int | None = None
    ) -> Chapter:
        self.received_branch_id = branch_id
        if self._exc is not None:
            raise self._exc
        assert self._chapter is not None
        return self._chapter

    async def get_table_of_contents(self) -> list[Volume]:
        return self._volumes


def test_read_chapter_renders_heading_and_content() -> None:
    chapter = Chapter(
        id=1, volume="1", number="5", name="Начало", content="<p>Текст главы</p>"
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "Глава 5" in response.text
    assert "Начало" in response.text
    assert 'href="/titles/6712--test-novel"' in response.text
    assert "<p>Текст главы</p>" in response.text


def test_read_chapter_applies_reader_settings_without_inline_panel() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "static/js/reader-settings.js" in response.text
    assert 'data-role="reader-settings"' not in response.text


def test_read_chapter_shows_adjacent_chapter_links() -> None:
    chapter = Chapter(id=2, volume="1", number="2", name="Середина", content="<p>x</p>")
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1"),
                Chapter(id=2, volume="1", number="2"),
                Chapter(id=3, volume="1", number="3"),
            ],
        )
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/2")

    assert response.status_code == 200
    assert 'href="/titles/6712--test-novel/chapters/1/1"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/3"' in response.text


def test_read_chapter_duplicates_adjacent_links_below_the_content() -> None:
    # PR 28: the same prev/next navigation as the top block, repeated after the chapter
    # text so it doesn't take a scroll back to the top to reach it. PR 52 adds a third
    # copy in the reveal-on-scroll-up overlay, ahead of both - see the test below.
    chapter = Chapter(id=2, volume="1", number="2", name="Середина", content="<p>x</p>")
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1"),
                Chapter(id=2, volume="1", number="2"),
                Chapter(id=3, volume="1", number="3"),
            ],
        )
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/2")

    assert response.status_code == 200
    assert response.text.count('href="/titles/6712--test-novel/chapters/1/1"') == 3
    assert response.text.count('href="/titles/6712--test-novel/chapters/1/3"') == 3
    assert 'class="reader-nav__adjacent reader-nav__adjacent--bottom"' in response.text
    # Bottom block comes after the chapter content, not before it.
    assert response.text.index("reader-nav__adjacent--bottom") > response.text.index(
        'data-role="chapter"'
    )


def test_read_chapter_renders_reveal_on_scroll_up_overlay() -> None:
    # PR 52: a fixed panel duplicating the back link/heading/prev-next, shown on any
    # upward scroll mid-chapter - see app/static/js/reader-scroll-nav.js.
    chapter = Chapter(id=2, volume="1", number="2", name="Середина", content="<p>x</p>")
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1"),
                Chapter(id=2, volume="1", number="2"),
                Chapter(id=3, volume="1", number="3"),
            ],
        )
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/2")

    assert response.status_code == 200
    assert 'data-role="reader-scroll-nav"' in response.text
    assert "static/js/reader-scroll-nav.js" in response.text
    # The overlay is the very first thing in the page - ahead of the in-flow nav.
    assert response.text.index('data-role="reader-scroll-nav"') < response.text.index(
        'class="reader-nav"'
    )


def test_read_chapter_includes_tap_to_read_script() -> None:
    # PR 62: off by default (readerSettings.tapToRead, PR 63 adds the switch), but the
    # script itself loads on every chapter page so turning it on later needs no server
    # round trip - it just starts reading a localStorage key nobody can set yet.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "static/js/tap-to-read.js" in response.text


def test_read_chapter_includes_reader_progress_script() -> None:
    # PR 84: the script itself checks readerSettings.tapToRead at runtime and skips
    # tracking when tap-to-read is on (that mode tracks its own progress), so it loads
    # unconditionally regardless of the setting, same as image-lightbox.js.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "static/js/reader-progress.js" in response.text


def test_read_chapter_disables_browser_scroll_restoration() -> None:
    # PR 129: without this, the browser's own auto-restore-scroll-on-reload/back-forward
    # races against (and typically wins over) tap-to-read.js/reader-progress.js's own
    # deferred scroll-to-saved-progress, silently undoing it.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert 'history.scrollRestoration = "manual";' in response.text
    # Set in <head>, before either script tag - as early as possible in the page's life.
    assert response.text.index("scrollRestoration") < response.text.index(
        "static/js/tap-to-read.js"
    )


def test_read_chapter_includes_image_lightbox_script() -> None:
    # PR 66: the script itself checks readerSettings.tapToRead at runtime and disables
    # itself in that mode, so it loads unconditionally regardless of the setting.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "static/js/image-lightbox.js" in response.text


def test_read_chapter_includes_paragraph_menu_script() -> None:
    # PR 131: infrastructure for PR 132/133's reactions/comments - loads unconditionally,
    # same as image-lightbox.js/reader-progress.js, regardless of reading mode.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert "static/js/paragraph-menu.js" in response.text


def test_read_chapter_exposes_paragraph_key_fields_on_content() -> None:
    # paragraph-menu.js (and eventually PR 132/133) key a paragraph off
    # slug_url/volume/number/branch_id plus its own index among .reader-content's
    # children - the first three of those come from these data attributes.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5?branch_id=42")

    assert response.status_code == 200
    assert 'data-volume="1"' in response.text
    assert 'data-number="5"' in response.text
    assert 'data-branch-id="42"' in response.text


def test_read_chapter_omits_branch_id_when_not_selected() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert 'data-branch-id=""' in response.text


def test_read_chapter_marks_content_authenticated_when_logged_in(
    logged_in_client: TestClient,
) -> None:
    # Anonymous vs. logged-in decides whether paragraph-menu.js's stub items point at
    # /login (see _locked_feature.html elsewhere) or render as disabled stubs.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    assert 'data-authenticated="1"' in response.text


def test_read_chapter_marks_content_not_authenticated_when_anonymous() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert 'data-authenticated=""' in response.text


def test_read_chapter_crosses_volume_boundary() -> None:
    chapter = Chapter(id=1, volume="1", number="3", name="Конец тома", content="<p>x</p>")
    volumes = [
        Volume(number="1", chapters=[Chapter(id=1, volume="1", number="3")]),
        Volume(number="2", chapters=[Chapter(id=2, volume="2", number="1")]),
    ]
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(chapter, volumes=volumes),
    ):
        response = client.get("/titles/6712--test-novel/chapters/1/3")

    assert response.status_code == 200
    assert "Предыдущая глава" not in response.text
    assert 'href="/titles/6712--test-novel/chapters/2/1"' in response.text


def test_read_chapter_not_found() -> None:
    exc = ChapterNotFoundError("6712--test-novel", volume="1", number="999")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/999", headers={"accept": "text/html"}
        )

    assert response.status_code == 404
    assert "Глава не найдена" in response.text


def test_read_chapter_auth_required() -> None:
    exc = AuthRequiredError("https://ranobelib.me/x")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 403
    assert response.json() == {"detail": "Требуется авторизация — недоступно"}


def test_read_chapter_rate_limited() -> None:
    exc = RateLimitError(retry_after=30)
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 429
    assert response.json() == {
        "detail": "ranobelib сейчас ограничивает запросы, попробуйте позже"
    }


def test_read_chapter_malformed_slug_url_returns_friendly_404_not_500() -> None:
    # No RanobeLib patch here on purpose - see the equivalent test in test_titles.py.
    response = client.get("/titles/not-a-valid-slug/chapters/1/5")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


def test_read_chapter_multiple_translations_shows_choice_page() -> None:
    branches = [
        ChapterBranch(
            id=1,
            branch_id=1,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            teams=[Team(id=1, slug="team-a", slug_url="team-a", name="Команда А")],
            user=ChapterUser(id=1, username="uploader1"),
        ),
        ChapterBranch(
            id=2,
            branch_id=2,
            created_at=datetime(2024, 3, 1, tzinfo=UTC),
            teams=[],
            user=ChapterUser(id=2, username="solo_translator"),
        ),
    ]
    exc = MultipleTranslationsError(
        "6712--test-novel", volume="1", number="5", branches=branches
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(exc=exc)):
        response = client.get(
            "/titles/6712--test-novel/chapters/1/5", headers={"accept": "text/html"}
        )

    assert response.status_code == 409
    assert "Команда А" in response.text
    assert "solo_translator" in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/5?branch_id=1"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/5?branch_id=2"' in response.text


def test_read_chapter_passes_branch_id_from_query_to_sdk() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5?branch_id=42")

    assert response.status_code == 200
    assert fake.received_branch_id == 42


def test_read_chapter_passes_no_branch_id_by_default() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    fake = _FakeClient(chapter)
    with patch("app.services.client.RanobeLib", return_value=fake):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    assert fake.received_branch_id is None


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Isolated DB + an authenticated session - see tests/test_api_auth.py for why the
    isolation dance (own DB file, reset connection singleton, `with TestClient`) is
    needed."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

    with TestClient(app) as test_client:
        test_client.post(
            "/register",
            data={
                "email": "alice@example.com",
                "password": "hunter2pass",
                "password_confirm": "hunter2pass",
            },
        )
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Same DB isolation as logged_in_client, without registering a session - for
    anonymous requests that still need the schema migrated (unlike most anonymous routes
    tested against the bare module-level `client` above, GET .../reactions reads the
    `reactions` table regardless of login, so it needs `with TestClient(app)` to actually
    run migrations first)."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def test_read_chapter_records_progress_for_title_in_library(
    logged_in_client: TestClient,
) -> None:
    add_entry(get_connection(), user_id=1, slug_url="6712--test-novel")
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    entry = get_entry(get_connection(), user_id=1, slug_url="6712--test-novel")
    assert entry.last_read_volume == "1"
    assert entry.last_read_number == "5"


def test_read_chapter_adds_title_to_library_if_missing(logged_in_client: TestClient) -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200
    entry = get_entry(get_connection(), user_id=1, slug_url="6712--test-novel")
    assert entry is not None
    assert entry.last_read_volume == "1"
    assert entry.last_read_number == "5"


def test_read_chapter_twice_does_not_duplicate_or_reset_the_library_entry(
    logged_in_client: TestClient,
) -> None:
    chapter_one = Chapter(id=1, volume="1", number="1", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter_one)):
        logged_in_client.get("/titles/6712--test-novel/chapters/1/1")
    first_added_at = get_entry(
        get_connection(), user_id=1, slug_url="6712--test-novel"
    ).added_at

    chapter_two = Chapter(id=2, volume="1", number="2", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter_two)):
        logged_in_client.get("/titles/6712--test-novel/chapters/1/2")

    entries = [
        e for e in list_entries(get_connection(), user_id=1) if e.slug_url == "6712--test-novel"
    ]
    assert len(entries) == 1
    assert entries[0].added_at == first_added_at
    assert entries[0].last_read_number == "2"


def test_read_chapter_includes_heartbeat_script_when_logged_in(
    logged_in_client: TestClient,
) -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    assert "static/js/activity-heartbeat.js" in response.text
    assert 'data-slug-url="6712--test-novel"' in response.text


def test_read_chapter_omits_heartbeat_script_when_anonymous() -> None:
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert "static/js/activity-heartbeat.js" not in response.text


def test_read_chapter_records_activity_even_outside_the_library(
    logged_in_client: TestClient,
) -> None:
    # Deliberately not added to the library first - unlike record_progress, the activity
    # feed isn't gated on library membership (see app/db/activity.py).
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        logged_in_client.get("/titles/6712--test-novel/chapters/1/5")

    counts = list_chapters_read_today(get_connection(), user_id=1)
    assert [c.slug_url for c in counts] == ["6712--test-novel"]
    assert counts[0].chapters_read == 1


def test_read_chapter_anonymous_does_not_touch_the_database(tmp_path: Path) -> None:
    # No login here - just confirms current_user=None takes the no-op path rather than
    # erroring out trying to record progress for nobody.
    chapter = Chapter(id=1, volume="1", number="5", content="<p>x</p>")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(chapter)):
        response = client.get("/titles/6712--test-novel/chapters/1/5")

    assert response.status_code == 200


def test_get_reactions_is_empty_for_a_chapter_with_none(
    isolated_client: TestClient,
) -> None:
    response = isolated_client.get("/titles/6712--test-novel/chapters/1/5/reactions")

    assert response.status_code == 200
    assert response.json() == {"counts": {}, "mine": {}}


def test_get_reactions_does_not_require_login(isolated_client: TestClient) -> None:
    # Reading who reacted what needs no account, only adding a reaction does (PR 132).
    response = isolated_client.get("/titles/6712--test-novel/chapters/1/5/reactions")

    assert response.status_code == 200


def test_post_reaction_requires_login() -> None:
    response = client.post(
        "/titles/6712--test-novel/chapters/1/5/reactions",
        data={"paragraph_index": "0", "emoji": "👍"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_reaction_rejects_an_emoji_outside_the_allowed_set(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/reactions",
        data={"paragraph_index": "0", "emoji": "🍕"},
    )

    assert response.status_code == 400


def test_post_reaction_sets_it_and_reports_the_new_count(
    logged_in_client: TestClient,
) -> None:
    response = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/reactions",
        data={"paragraph_index": "0", "emoji": "👍"},
    )

    assert response.status_code == 200
    assert response.json() == {"paragraph_index": 0, "counts": {"👍": 1}, "mine": "👍"}


def test_post_reaction_same_emoji_again_removes_it(logged_in_client: TestClient) -> None:
    logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/reactions",
        data={"paragraph_index": "0", "emoji": "👍"},
    )

    response = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/reactions",
        data={"paragraph_index": "0", "emoji": "👍"},
    )

    assert response.json() == {"paragraph_index": 0, "counts": {}, "mine": None}


def test_post_reaction_reflected_by_a_later_get(logged_in_client: TestClient) -> None:
    logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/reactions",
        data={"paragraph_index": "0", "emoji": "🔥"},
    )
    logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/reactions",
        data={"paragraph_index": "2", "emoji": "👏", "branch_id": "3"},
    )

    same_branch = logged_in_client.get(
        "/titles/6712--test-novel/chapters/1/5/reactions"
    ).json()
    other_branch = logged_in_client.get(
        "/titles/6712--test-novel/chapters/1/5/reactions", params={"branch_id": "3"}
    ).json()

    assert same_branch == {"counts": {"0": {"🔥": 1}}, "mine": {"0": "🔥"}}
    assert other_branch == {"counts": {"2": {"👏": 1}}, "mine": {"2": "👏"}}


def test_get_comment_counts_is_empty_for_a_chapter_with_none(
    isolated_client: TestClient,
) -> None:
    response = isolated_client.get("/titles/6712--test-novel/chapters/1/5/comments/counts")

    assert response.status_code == 200
    assert response.json() == {"counts": {}}


def test_get_comments_is_empty_for_a_paragraph_with_none(
    isolated_client: TestClient,
) -> None:
    response = isolated_client.get(
        "/titles/6712--test-novel/chapters/1/5/comments", params={"paragraph_index": "0"}
    )

    assert response.status_code == 200
    assert response.json() == {"paragraph_index": 0, "count": 0, "comments": []}


def test_post_comment_requires_login() -> None:
    response = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "hi"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_comment_rejects_an_empty_body(logged_in_client: TestClient) -> None:
    response = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "   "},
    )

    assert response.status_code == 400


def test_post_comment_creates_a_root_comment(logged_in_client: TestClient) -> None:
    response = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "Отличная глава!"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["paragraph_index"] == 0
    assert data["count"] == 1
    assert len(data["comments"]) == 1
    comment = data["comments"][0]
    assert comment["body"] == "Отличная глава!"
    assert comment["author"] == "alice@example.com"
    assert comment["parent_comment_id"] is None
    assert comment["replies"] == []
    assert isinstance(comment["user_id"], int)
    assert comment["avatar_url"] is None
    assert comment["avatar_initials"] == "AL"


def test_post_comment_reply_nests_under_its_parent(logged_in_client: TestClient) -> None:
    root = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "root"},
    ).json()["comments"][0]

    response = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={
            "paragraph_index": "0",
            "body": "reply",
            "parent_comment_id": str(root["id"]),
        },
    )

    data = response.json()
    assert data["count"] == 2
    assert len(data["comments"]) == 1
    assert data["comments"][0]["replies"][0]["body"] == "reply"


def test_post_comment_rejects_a_parent_from_a_different_paragraph(
    logged_in_client: TestClient,
) -> None:
    root = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "root"},
    ).json()["comments"][0]

    response = logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={
            "paragraph_index": "3",
            "body": "reply",
            "parent_comment_id": str(root["id"]),
        },
    )

    assert response.status_code == 400


def test_get_comment_counts_reflects_posted_comments(logged_in_client: TestClient) -> None:
    logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "a"},
    )
    logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "b"},
    )
    logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "3", "body": "c"},
    )

    response = logged_in_client.get("/titles/6712--test-novel/chapters/1/5/comments/counts")

    assert response.json() == {"counts": {"0": 2, "3": 1}}


def test_comments_are_scoped_to_branch_id(logged_in_client: TestClient) -> None:
    logged_in_client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "on branch 1", "branch_id": "1"},
    )

    same_branch = logged_in_client.get(
        "/titles/6712--test-novel/chapters/1/5/comments",
        params={"paragraph_index": "0", "branch_id": "1"},
    ).json()
    other_branch = logged_in_client.get(
        "/titles/6712--test-novel/chapters/1/5/comments",
        params={"paragraph_index": "0", "branch_id": "2"},
    ).json()

    assert same_branch["count"] == 1
    assert other_branch["count"] == 0
