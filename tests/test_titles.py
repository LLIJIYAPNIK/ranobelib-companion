from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient
from ranobelib import RanobeLibError, RateLimitError, TitleNotFoundError
from ranobelib.models import Chapter, Cover, Genre, Label, Tag, Title, Volume

from app.main import app

client = TestClient(app, follow_redirects=False)


def _fake_title(slug_url: str = "6712--test-novel") -> Title:
    return Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        slug="test-novel",
        slug_url=slug_url,
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        chapter_count=42,
    )


class _FakeClient:
    def __init__(
        self,
        title: Title,
        volumes: list[Volume] | None = None,
        estimated_size: int = 0,
    ) -> None:
        self._title = title
        self._volumes = volumes or []
        self._estimated_size = estimated_size

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

    async def get_table_of_contents(self) -> list[Volume]:
        return self._volumes

    async def estimate_title_size(self) -> int:
        return self._estimated_size


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> "_RaisingClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        raise self._exc


class _AssertNeverCalledClient:
    """Stands in for the SDK client on tests asserting show_title() itself makes no SDK
    call at all (PR 203) - any method being awaited fails the test immediately, rather
    than only implicitly via a missing return value."""

    async def __aenter__(self) -> "_AssertNeverCalledClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        raise AssertionError("show_title() must not call the SDK - that's title_data()'s job")

    async def get_table_of_contents(self) -> list[Volume]:
        raise AssertionError("show_title() must not call the SDK - that's title_data()'s job")


def test_open_title_redirects_to_canonical_slug_url() -> None:
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(
            "/titles/open",
            params={"url": "https://ranobelib.me/ru/book/6712--test-novel"},
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/titles/6712--test-novel"


def test_open_title_rejects_unparseable_input() -> None:
    response = client.get("/titles/open", params={"url": "not a link at all"})

    assert response.status_code == 400
    assert "Не удалось распознать ссылку" in response.text


def test_open_title_rate_limited() -> None:
    exc = RateLimitError(retry_after=30)
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get(
            "/titles/open",
            params={"url": "https://ranobelib.me/ru/book/6712--test-novel"},
        )

    assert response.status_code == 429
    assert response.json() == {
        "detail": "ranobelib сейчас ограничивает запросы, попробуйте позже"
    }


# --- PR 203: show_title() itself - a skeleton, no SDK call, never hangs/errors ----------


def test_show_title_makes_no_sdk_call_at_all() -> None:
    with patch("app.services.client.RanobeLib", return_value=_AssertNeverCalledClient()):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200


def test_show_title_renders_the_skeleton_and_loader_script() -> None:
    response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert 'data-role="title-content"' in response.text
    assert 'data-slug-url="6712--test-novel"' in response.text
    assert 'class="title-hero title-hero--skeleton"' in response.text
    assert "static/js/title-content-load.js" in response.text


def test_show_title_still_200s_for_a_slug_that_will_fail_to_load() -> None:
    # The core bug fix: a malformed/nonexistent slug used to 404 (or hang on a slow
    # upstream) right on this page navigation - now that's title_data()'s problem, surfaced
    # later in place of the skeleton, never this route's own status code.
    response = client.get("/titles/not-a-valid-slug")

    assert response.status_code == 200
    assert 'data-role="title-content"' in response.text


def test_show_title_renders_finished_notice_when_flagged() -> None:
    # PR 75: tap-to-read.js lands here with ?finished=1 after the last paragraph of a
    # title's last chapter - a plain query flag, not SDK data, so it renders without any
    # SDK call either.
    with patch("app.services.client.RanobeLib", return_value=_AssertNeverCalledClient()):
        response = client.get("/titles/6712--test-novel?finished=1")

    assert response.status_code == 200
    assert 'data-role="title-finished-notice"' in response.text
    assert "Тайтл прочитан" in response.text


def test_show_title_omits_finished_notice_by_default() -> None:
    response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "Тайтл прочитан" not in response.text


def test_show_title_loads_every_toc_dependent_script() -> None:
    response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    for script in (
        "chapter-export-panel.js",
        "title-size-estimate.js",
        "custom-dropdown.js",
        "toc-tap-progress.js",
        "image-lightbox.js",
        "title-content-load.js",
    ):
        assert f"static/js/{script}" in response.text


def test_show_title_not_found_still_200s_the_page_itself() -> None:
    exc = TitleNotFoundError("6712--missing")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--missing")

    # Unlike title_data() below, this route never even touches the SDK, so the exception
    # never reaches it in the first place.
    assert response.status_code == 200


# --- PR 203: GET /titles/{slug}/data - the real content, fetched separately ------------


def test_title_data_renders_metadata() -> None:
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert "Тестовый роман" in response.text
    assert "https://example.com/cover.jpg" in response.text
    assert "42" in response.text


def test_title_data_renders_size_estimate_placeholder() -> None:
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'data-role="title-size-estimate"' in response.text
    assert 'data-slug-url="6712--test-novel"' in response.text
    assert 'class="spinner"' in response.text
    assert 'data-role="title-size-estimate-status"' in response.text
    assert "Загружаем главы…" in response.text


def test_title_data_does_not_call_estimate_title_size() -> None:
    class _BlockingEstimateClient(_FakeClient):
        async def estimate_title_size(self) -> int:
            raise AssertionError("title_data() must not block on the size estimate")

    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_BlockingEstimateClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200


def test_title_size_estimate_endpoint_returns_formatted_label() -> None:
    title = _fake_title()
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(title, estimated_size=1_500_000),
    ):
        response = client.get("/titles/6712--test-novel/size-estimate")

    assert response.status_code == 200
    assert response.json() == {"label": "1.4 МБ"}


def test_title_size_estimate_endpoint_omits_label_when_zero() -> None:
    # 0 is estimate_title_size()'s own "nothing to estimate" return (no chapters, or none
    # with a resolvable translation) - not a real "≈0 Б" title.
    title = _fake_title()
    with patch(
        "app.services.client.RanobeLib",
        return_value=_FakeClient(title, estimated_size=0),
    ):
        response = client.get("/titles/6712--test-novel/size-estimate")

    assert response.status_code == 200
    assert response.json() == {"label": None}


def test_title_size_estimate_endpoint_survives_sample_failure() -> None:
    # A sampled chapter failing (rate limit, needs auth, ...) is a supplementary estimate
    # falling through, not a reason to error the whole request.
    class _FlakyEstimateClient(_FakeClient):
        async def estimate_title_size(self) -> int:
            raise RateLimitError(retry_after=30)

    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FlakyEstimateClient(title)):
        response = client.get("/titles/6712--test-novel/size-estimate")

    assert response.status_code == 200
    assert response.json() == {"label": None}


def test_title_quickview_renders_metadata_fragment() -> None:
    title = Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        summary="A short summary.",
        genres=[Genre(id=5, name="Фэнтези")],
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/quickview")

    assert response.status_code == 200
    assert "Тестовый роман" in response.text
    assert "A short summary." in response.text
    assert "Фэнтези" in response.text
    assert 'href="/titles/6712--test-novel"' in response.text
    # It's just the metadata fragment, not the full page.
    assert 'data-role="sidebar"' not in response.text
    assert 'data-role="chapter-toc-form"' not in response.text


def test_title_quickview_does_not_fetch_the_table_of_contents() -> None:
    # PR 117: the modal only shows what get_info() already returns - no reason to also
    # pay for get_table_of_contents() (a separate request the chapter list needs) just to
    # render a preview that never shows chapters.
    class _BlockingTocClient(_FakeClient):
        async def get_table_of_contents(self) -> list[Volume]:
            raise AssertionError("quickview must not fetch the table of contents")

    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_BlockingTocClient(title)):
        response = client.get("/titles/6712--test-novel/quickview")

    assert response.status_code == 200


def test_title_data_uses_russian_name_as_the_primary_heading() -> None:
    # PR 25: rus_name (already in the SDK's Title model, no issue needed) becomes the
    # primary display name wherever the title's name is shown, with the original name
    # falling back to an alt-name line instead of disappearing.
    #
    # PR 203: no server-rendered <title> tag here any more (this is a fragment, not a full
    # page) - title-content-load.js sets document.title client-side from this same
    # data-display-name attribute instead.
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'data-display-name="Тестовый роман"' in response.text
    assert '<h1 class="title-hero__name">Тестовый роман</h1>' in response.text
    assert '<p class="title-hero__alt-name">Test Novel</p>' in response.text
    assert 'alt="Обложка «Тестовый роман»"' in response.text


def test_title_data_falls_back_to_original_name_without_a_russian_one() -> None:
    title = Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'data-display-name="Test Novel"' in response.text
    assert '<h1 class="title-hero__name">Test Novel</h1>' in response.text
    assert "title-hero__alt-name" not in response.text


def test_title_data_renders_full_metadata() -> None:
    title = Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        eng_name="Test Novel EN",
        other_names=["Alt Title"],
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        release_date="2020",
        tags=[Tag(id=1, name="Реинкарнация")],
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert "Test Novel EN" in response.text
    assert "Alt Title" in response.text
    assert "2020" in response.text
    assert "Реинкарнация" in response.text


def test_title_data_genres_link_to_the_filtered_catalog() -> None:
    # PR 31/38: a genre badge is a link into /library/catalog, not just a static label -
    # just the id, the catalog page resolves the display name itself via
    # Catalog.list_genres() (PR 38) rather than needing it forwarded in the URL.
    title = Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        genres=[Genre(id=5, name="Фэнтези"), Genre(id=8, name="Романтика")],
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'href="/library/catalog?genres=5"' in response.text
    assert 'href="/library/catalog?genres=8"' in response.text


def test_title_data_tags_link_to_the_filtered_catalog() -> None:
    # PR 86: same pattern as genre badges (PR 31), but there's no Catalog.list_tags()
    # to resolve a display name from just an id on the catalog page - unlike genres,
    # the tag's own already-known name has to be forwarded through the link itself
    # (tag_name) for the catalog page's filter-chip hint to show it.
    title = Title(
        id=6712,
        name="Test Novel",
        slug="test-novel",
        slug_url="6712--test-novel",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        tags=[Tag(id=1, name="Реинкарнация"), Tag(id=2, name="Магия")],
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert f'href="/library/catalog?tags=1&tag_name={quote("Реинкарнация")}"' in response.text
    assert f'href="/library/catalog?tags=2&tag_name={quote("Магия")}"' in response.text
    assert '<span class="badge badge--muted">Реинкарнация</span>' not in response.text


def test_title_data_renders_table_of_contents() -> None:
    title = _fake_title()
    volumes = [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1", name="Начало"),
                Chapter(id=2, volume="1", number="1.5", name=None),
                Chapter(id=3, volume="1", number="2", name="Переводы", branches_count=3),
            ],
        )
    ]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert "Том 1" in response.text
    assert "Начало" in response.text
    assert "1.5" in response.text
    assert "Без названия" in response.text
    assert "3 переводов" in response.text
    assert response.text.count("переводов") == 1
    assert 'name="chapters"' in response.text
    assert 'value="1--1"' in response.text
    assert 'value="1--1.5"' in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/1"' in response.text


def test_title_data_renders_export_form() -> None:
    title = _fake_title()
    volumes = [
        Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1", name="Начало")])
    ]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'action="/titles/6712--test-novel/export"' in response.text
    assert 'name="fmt"' in response.text
    assert '<option value="epub">EPUB</option>' in response.text
    assert '<option value="txt">TXT</option>' in response.text
    assert (
        'formaction="/titles/6712--test-novel/volumes/1/export"' in response.text
    )
    assert "Скачать том" in response.text
    assert 'action="/titles/6712--test-novel/download"' in response.text
    assert "Скачать тайтл" in response.text


def test_title_data_export_panel_is_present_for_the_floating_panel_script() -> None:
    title = _fake_title()
    volumes = [
        Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1", name="Начало")])
    ]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert 'data-role="chapter-toc-form"' in response.text
    assert 'data-role="chapter-export-panel"' in response.text


def test_title_data_has_two_format_selects() -> None:
    # PR 54: both the title-level "Скачать тайтл" and the selected-chapters "Скачать
    # выбранное" format pickers are plain <select>s enhanced by the same shared script.
    title = _fake_title()
    volumes = [
        Volume(number="1", chapters=[Chapter(id=1, volume="1", number="1", name="Начало")])
    ]
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title, volumes)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 200
    assert response.text.count('class="toc__export-format"') == 2


def test_title_data_not_found_renders_json_by_default() -> None:
    # PR 203: this is a fetch() target, not a page navigation - the central RanobeLibError
    # handler answers with JSON here by default (see app/exceptions.py's _wants_html()),
    # which is exactly what title-content-load.js reads `detail` from.
    exc = TitleNotFoundError("6712--missing")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--missing/data")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


def test_title_data_malformed_slug_url_returns_friendly_404_not_500() -> None:
    # No RanobeLib patch here on purpose - the real SDK class raises a plain ValueError
    # for a slug_url that doesn't parse, which open_client() must convert rather than
    # letting it fall through as an unhandled 500.
    response = client.get("/titles/not-a-valid-slug/data")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}


class _UnmappedError(RanobeLibError):
    """A stand-in for a future RanobeLibError subclass this app's table doesn't cover
    yet - used to confirm the fallback branch never leaks the exception's own message
    (which could carry internal detail)."""


def test_title_data_unmapped_error_hides_the_message() -> None:
    exc = _UnmappedError("some internal SDK detail")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--test-novel/data")

    assert response.status_code == 500
    assert response.json()["detail"] == "Внутренняя ошибка, попробуйте позже"
    assert "some internal SDK detail" not in response.text
