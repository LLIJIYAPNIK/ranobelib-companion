import re
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import CatalogPage
from ranobelib.models import Country, Cover, Genre, Label, Title

from app.main import app

client = TestClient(app)


def _fake_title(id_: int = 1, name: str = "Test Novel") -> Title:
    return Title(
        id=id_,
        name=name,
        slug=f"test-novel-{id_}",
        slug_url=f"{id_}--test-novel-{id_}",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _FakeCatalog:
    def __init__(
        self,
        page: CatalogPage | None = None,
        exc: Exception | None = None,
        genres: list[Genre] | None = None,
        countries: list[Country] | None = None,
    ) -> None:
        self._page = page
        self._exc = exc
        self._genres = genres or []
        self._countries = countries or []
        self.received_kwargs: dict[str, object] | None = None

    async def __aenter__(self) -> "_FakeCatalog":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def list_titles(self, **kwargs: object) -> CatalogPage:
        self.received_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        assert self._page is not None
        return self._page

    async def list_genres(self) -> list[Genre]:
        return self._genres

    async def list_countries(self) -> list[Country]:
        return self._countries


def test_show_catalog_renders_countries_in_grid_data_attribute() -> None:
    # catalog-scroll.js reads data-country off the grid the same way it already reads
    # data-genres, to forward the filter on every infinite-scroll page fetch.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog", params={"countries": [3, 5]})

    assert 'data-country="3,5"' in response.text


def test_show_catalog_without_countries_leaves_data_country_empty() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert 'data-country=""' in response.text


def test_show_catalog_renders_cards() -> None:
    page = CatalogPage(items=[_fake_title(1, "High School DxD")], page=1, has_next_page=True)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert "High School DxD" in response.text
    assert 'href="/titles/1--test-novel-1"' in response.text
    assert 'data-next-page="2"' in response.text
    # PR 54: the sort <select> is progressively enhanced into a custom listbox.
    assert "static/js/custom-dropdown.js" in response.text


def test_show_catalog_wraps_the_sort_select_for_its_own_dropdown_width() -> None:
    # PR 115: .catalog-sort scopes the wider min-width fix to just this dropdown, without
    # widening every other select.toc__export-format-based dropdown on the site.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert '<div class="catalog-sort">' in response.text
    catalog_sort_start = response.text.index('<div class="catalog-sort">')
    select_start = response.text.index('name="sort"')
    assert catalog_sort_start < select_start


def test_show_catalog_renders_back_to_top_button() -> None:
    # PR 102: catalog-back-to-top.js reveals this once scrolled past REVEAL_DEPTH and
    # smooth-scrolls to the top on click - starts `hidden` so it never flashes on load.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    button_tag = re.search(r'<button[^>]*data-role="catalog-back-to-top"[^>]*>', response.text)
    assert button_tag is not None
    assert "hidden" in button_tag.group(0)
    assert "static/js/catalog-back-to-top.js" in response.text


def test_show_catalog_header_reveals_on_scroll_up() -> None:
    # PR 101: catalog-header-scroll.js hides/reveals .header on scroll direction - both the
    # hook it queries for and the script itself need to be on the page for that to work.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    header_tag = re.search(r"<header[^>]*>", response.text)
    assert header_tag is not None
    assert 'data-role="catalog-scroll-header"' in header_tag.group(0)
    assert "static/js/catalog-header-scroll.js" in response.text


def test_show_catalog_prefers_russian_name() -> None:
    title = Title(
        id=1,
        name="High School DxD",
        rus_name="Школа демонов",
        slug="test-novel-1",
        slug_url="1--test-novel-1",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )
    page = CatalogPage(items=[title], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert "Школа демонов" in response.text
    assert "High School DxD" not in response.text


def test_show_catalog_no_next_page_leaves_data_next_page_empty() -> None:
    page = CatalogPage(items=[_fake_title()], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'data-next-page=""' in response.text


def test_show_catalog_is_viewable_without_login() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200  # no redirect to /login


def test_show_catalog_invalid_page_is_rejected() -> None:
    response = client.get("/library/catalog", params={"page": 0})

    assert response.status_code == 422


def test_show_catalog_passes_page_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=3, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"page": 3})

    assert fake.received_kwargs["page"] == 3


def test_show_catalog_passes_query_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"query": "dxd"})

    assert fake.received_kwargs["query"] == "dxd"


def test_show_catalog_empty_query_is_treated_as_no_search() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"query": ""})

    assert fake.received_kwargs["query"] is None


def test_show_catalog_renders_query_in_search_input_and_data_attribute() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog", params={"query": "dxd"})

    assert 'value="dxd"' in response.text
    assert 'data-query="dxd"' in response.text


def test_show_catalog_defaults_sort_to_last_chapter_at() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog")

    assert fake.received_kwargs["sort"] == "last_chapter_at"


def test_show_catalog_passes_sort_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"sort": "views"})

    assert fake.received_kwargs["sort"] == "views"


def test_show_catalog_renders_sort_in_select_and_data_attribute() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog", params={"sort": "views"})

    assert 'data-sort="views"' in response.text
    assert '<option value="views" selected>' in response.text


def test_show_catalog_passes_genres_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"genres": 5})

    assert fake.received_kwargs["genres"] == [5]


def test_show_catalog_passes_several_genres_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"genres": [5, 8]})

    assert fake.received_kwargs["genres"] == [5, 8]


def test_show_catalog_without_genres_passes_none() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog")

    assert fake.received_kwargs["genres"] is None


def test_show_catalog_passes_tags_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"tags": [1, 2]})

    assert fake.received_kwargs["tags"] == [1, 2]


def test_show_catalog_without_tags_passes_none() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog")

    assert fake.received_kwargs["tags"] is None


def test_show_catalog_renders_tag_filter_chip_using_forwarded_name() -> None:
    # No Catalog.list_tags() exists to resolve a name from just an id (unlike genres),
    # so the title-page link forwards its own already-known label as tag_name.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        response = client.get(
            "/library/catalog", params={"tags": 7, "tag_name": "Реинкарнация"}
        )

    assert response.status_code == 200
    assert "Тег: Реинкарнация" in response.text
    assert "Сбросить фильтр<" in response.text  # singular - only one filter active


def test_show_catalog_tag_without_forwarded_name_falls_back_to_the_id() -> None:
    # Reachable by hand-editing the URL to drop tag_name, or a hypothetical future
    # multi-tag selector - either way there's nothing to resolve a display name from.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        response = client.get("/library/catalog", params={"tags": 7})

    assert response.status_code == 200
    assert "Тег: 7" in response.text


def test_show_catalog_renders_tags_in_grid_data_attribute() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog", params={"tags": [1, 2]})

    assert 'data-tags="1,2"' in response.text


def test_show_catalog_passes_countries_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"countries": 3})

    assert fake.received_kwargs["countries"] == [3]


def test_show_catalog_passes_several_countries_to_the_sdk() -> None:
    # PR 100: countries is OR/IN, not AND like genres - but the SDK shape (a repeated
    # list param) is identical, so several selected countries just means a longer list.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog", params={"countries": [1, 2]})

    assert fake.received_kwargs["countries"] == [1, 2]


def test_show_catalog_without_countries_passes_none() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog")

    assert fake.received_kwargs["countries"] is None


def test_show_catalog_garbage_country_id_errors_same_as_a_garbage_genre_id() -> None:
    # PR 100: countries is now a repeated list param shaped exactly like genres (was a
    # single raw string manually parsed to tolerate garbage input before this PR) - a
    # non-numeric value 422s via FastAPI's own int coercion, same as genres already does.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        genres_response = client.get("/library/catalog", params={"genres": "not-a-number"})
        countries_response = client.get(
            "/library/catalog", params={"countries": "not-a-number"}
        )

    assert countries_response.status_code == genres_response.status_code == 422


def test_show_catalog_renders_genre_checkboxes() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези"), Genre(id=8, name="Романтика")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog", params={"genres": 5})

    assert response.status_code == 200
    assert 'data-role="catalog-filters"' in response.text
    assert '<span>Фэнтези</span>' in response.text
    assert '<span>Романтика</span>' in response.text

    checkbox_5 = re.search(r'value="5"[^>]*>', response.text)
    checkbox_8 = re.search(r'value="8"[^>]*>', response.text)
    assert checkbox_5 is not None and "checked" in checkbox_5.group(0)
    assert checkbox_8 is not None and "checked" not in checkbox_8.group(0)


def test_show_catalog_renders_genre_filter_chip_with_resolved_names() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези"), Genre(id=8, name="Романтика")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog", params={"genres": [5, 8]})

    assert response.status_code == 200
    assert "Жанры: Фэнтези, Романтика" in response.text
    assert 'data-genres="5,8"' in response.text


def test_show_catalog_filters_panel_renders_unhidden_for_the_no_js_fallback() -> None:
    # PR 98 (revisiting PR 85): the panel is opened/closed via the "Фильтры" button in JS,
    # but renders without a `hidden` attribute so it's still fully visible and usable
    # without JS - the toggle script is what hides it (see catalog-filters-toggle.js).
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog", params={"genres": 5})

    assert response.status_code == 200
    aside_tag = re.search(r"<aside\b[^>]*>", response.text)
    assert aside_tag is not None
    assert 'class="catalog-filters"' in aside_tag.group(0)
    assert 'data-role="catalog-filters"' in aside_tag.group(0)
    assert 'id="catalog-filters-panel"' in aside_tag.group(0)
    assert "hidden" not in aside_tag.group(0)
    assert '<h2 class="catalog-filters__title">Фильтры</h2>' in response.text
    assert "Жанры (1)" in response.text
    assert "catalog-genres" not in response.text
    assert "catalog-genres-toggle" not in response.text


def test_show_catalog_renders_a_filters_toggle_button_when_there_are_filters() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'data-role="catalog-filters-toggle"' in response.text
    assert 'aria-controls="catalog-filters-panel"' in response.text


def test_filters_panel_header_sits_outside_the_scrolling_body_wrapper() -> None:
    # PR 114: .catalog-filters used to scroll as one block, carrying the panel header
    # (title + close button) and every section's toggle out of view along with a long
    # options list. The header must now sit outside data-role="catalog-filters-body" (the
    # new scroll container) entirely, and every section must be inside it.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    header_marker = response.text.index('class="catalog-filters__header"')
    body_marker = response.text.index('data-role="catalog-filters-body"')
    section_marker = response.text.index('data-role="catalog-filters-section"')
    assert header_marker < body_marker < section_marker


def test_filters_toggle_is_a_header_sibling_of_the_search_form_not_nested_inside_it() -> None:
    # PR 111: nested inside .search-form (max-width: 480px), margin-left: auto only
    # pushed the button to that form's own right edge, not the header's - moved it to be
    # a .header child instead so it reaches the actual right edge of the page.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'class="header__filters-toggle"' in response.text
    form_end = response.text.index("</form>")
    toggle_start = response.text.index('data-role="catalog-filters-toggle"')
    assert toggle_start > form_end


def test_show_catalog_filters_panel_has_a_close_button() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'data-role="catalog-filters-close"' in response.text


def test_show_catalog_omits_the_filters_toggle_when_there_is_nothing_to_filter_by() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'data-role="catalog-filters-toggle"' not in response.text
    assert "catalog-filters-toggle.js" not in response.text


def test_show_catalog_includes_the_filters_toggle_script_when_there_are_filters() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert "static/js/catalog-filters-toggle.js" in response.text


def test_show_catalog_renders_country_checkboxes() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    countries = [Country(id=1, name="Япония"), Country(id=2, name="Корея")]
    with patch(
        "app.services.catalog.Catalog", return_value=_FakeCatalog(page, countries=countries)
    ):
        response = client.get("/library/catalog", params={"countries": 1})

    assert response.status_code == 200
    assert 'data-section-key="countries"' in response.text
    assert ">Страны (1)</button>" in response.text
    assert "<span>Япония</span>" in response.text
    assert "<span>Корея</span>" in response.text

    checkbox_1 = re.search(r'name="countries"\s+value="1"[^>]*>', response.text)
    checkbox_2 = re.search(r'name="countries"\s+value="2"[^>]*>', response.text)
    assert checkbox_1 is not None and "checked" in checkbox_1.group(0)
    assert checkbox_2 is not None and "checked" not in checkbox_2.group(0)


def test_show_catalog_without_countries_checks_nothing() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    countries = [Country(id=1, name="Япония")]
    with patch(
        "app.services.catalog.Catalog", return_value=_FakeCatalog(page, countries=countries)
    ):
        response = client.get("/library/catalog")

    japan_checkbox = re.search(r'name="countries"\s+value="1"[^>]*>', response.text)
    assert japan_checkbox is not None and "checked" not in japan_checkbox.group(0)


def test_show_catalog_renders_country_filter_chip_with_resolved_names() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    countries = [Country(id=1, name="Япония"), Country(id=2, name="Корея")]
    with patch(
        "app.services.catalog.Catalog", return_value=_FakeCatalog(page, countries=countries)
    ):
        response = client.get("/library/catalog", params={"countries": [1, 2]})

    assert response.status_code == 200
    assert "Страны: Япония, Корея" in response.text
    assert 'data-country="1,2"' in response.text


def test_show_catalog_renders_combined_filter_chip_and_plural_reset_link() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    countries = [Country(id=1, name="Япония")]
    with patch(
        "app.services.catalog.Catalog",
        return_value=_FakeCatalog(page, genres=genres, countries=countries),
    ):
        response = client.get("/library/catalog", params={"genres": 5, "countries": 1})

    assert response.status_code == 200
    assert "Жанр: Фэнтези · Страна: Япония" in response.text
    assert "Сбросить фильтры<" in response.text  # plural - two filters active
    # the reset link drops both, not just one
    assert 'href="?query=&sort=last_chapter_at"' in response.text


def test_show_catalog_without_genres_omits_the_filter_chip() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert "Жанр:" not in response.text
    assert 'data-role="catalog-filters"' not in response.text


def test_catalog_page_fragment_passes_genres_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog/page", params={"genres": [5, 8]})

    assert fake.received_kwargs["genres"] == [5, 8]


def test_catalog_page_fragment_passes_tags_to_the_sdk() -> None:
    # catalog-scroll.js forwards data-tags on every infinite-scroll page fetch (same as
    # data-genres) so the filter stays applied past the first page.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog/page", params={"tags": [1, 2]})

    assert fake.received_kwargs["tags"] == [1, 2]


def test_catalog_page_fragment_passes_countries_to_the_sdk() -> None:
    # catalog-scroll.js forwards data-country on every infinite-scroll page fetch (same
    # as data-genres) so the filter stays applied past the first page.
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog/page", params={"countries": [3, 5]})

    assert fake.received_kwargs["countries"] == [3, 5]


def test_catalog_page_fragment_passes_sort_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog/page", params={"sort": "views"})

    assert fake.received_kwargs["sort"] == "views"


def test_catalog_page_fragment_passes_query_to_the_sdk() -> None:
    page = CatalogPage(items=[], page=2, has_next_page=False)
    fake = _FakeCatalog(page)
    with patch("app.services.catalog.Catalog", return_value=fake):
        client.get("/library/catalog/page", params={"query": "dxd", "page": 2})

    assert fake.received_kwargs["query"] == "dxd"


def test_catalog_page_fragment_returns_only_cards() -> None:
    page = CatalogPage(items=[_fake_title(1, "High School DxD")], page=2, has_next_page=True)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog/page", params={"page": 2})

    assert response.status_code == 200
    assert response.headers["X-Has-Next-Page"] == "true"
    assert "High School DxD" in response.text
    assert "<html" not in response.text
    assert "sidebar" not in response.text


def test_catalog_page_fragment_has_next_page_false() -> None:
    page = CatalogPage(items=[], page=5, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog/page", params={"page": 5})

    assert response.headers["X-Has-Next-Page"] == "false"


def test_library_tabs_active_state() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)

    library_response = client.get("/library")
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        catalog_response = client.get("/library/catalog")

    assert 'library-tabs__link library-tabs__link--active" href="/library"' in library_response.text
    assert (
        'library-tabs__link library-tabs__link--active" href="/library/catalog"'
        in catalog_response.text
    )


def test_show_catalog_genre_section_is_an_accordion_toggle() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'data-section-key="genres"' in response.text
    toggle = re.search(r'data-role="catalog-filters-section-toggle"[^>]*>', response.text)
    assert toggle is not None
    assert 'aria-expanded="true"' in toggle.group(0)
    assert 'aria-controls="catalog-filters-genres-options"' in toggle.group(0)
    assert 'id="catalog-filters-genres-options"' in response.text


def test_show_catalog_country_section_is_an_accordion_toggle() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    countries = [Country(id=1, name="Япония")]
    with patch(
        "app.services.catalog.Catalog", return_value=_FakeCatalog(page, countries=countries)
    ):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert 'data-section-key="countries"' in response.text
    assert 'aria-controls="catalog-filters-countries-options"' in response.text
    assert 'id="catalog-filters-countries-options"' in response.text


def test_show_catalog_includes_the_accordion_script_when_there_are_filters() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    genres = [Genre(id=5, name="Фэнтези")]
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page, genres=genres)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert "static/js/catalog-filters-accordion.js" in response.text


def test_show_catalog_omits_the_accordion_script_when_there_is_nothing_to_filter_by() -> None:
    page = CatalogPage(items=[], page=1, has_next_page=False)
    with patch("app.services.catalog.Catalog", return_value=_FakeCatalog(page)):
        response = client.get("/library/catalog")

    assert response.status_code == 200
    assert "catalog-filters-accordion.js" not in response.text
