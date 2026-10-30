"""Issue 399: a search by name lists the most viewed titles first - in the catalog's
results for a query and in its infinite scroll - unless a sort was picked; the listing
without a query keeps «По обновлению». The suggestions' side is tests/test_search_suggest.py."""

from pathlib import Path
from unittest.mock import patch

import pytest
from ranobelib import CatalogPage
from ranobelib.models import Genre

from app.services.catalog import SEARCH_SORT
from tests.test_api_catalog import _FakeCatalog, client

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "app/static/js/catalog-search-sort.js").read_text(encoding="utf-8")
EMPTY = CatalogPage(items=[], page=1, has_next_page=False)


def _get(
    path: str, genres: list[Genre] | None = None, **params: object
) -> tuple[object, _FakeCatalog]:
    catalog = _FakeCatalog(EMPTY, genres=genres)
    with patch("app.services.catalog.Catalog", return_value=catalog):
        response = client.get(path, params=params)
    assert response.status_code == 200
    return response, catalog


def test_a_search_goes_by_popularity() -> None:
    assert SEARCH_SORT == "views"
    response, catalog = _get("/catalog", query="поднятие уровня")

    assert catalog.calls[0]["sort"] == "views"
    assert response.context["sort"] == "views"
    assert response.context["default_sort"] == "views"
    assert response.context["sort_kept"] is False
    # The toolbar shows «По просмотрам», not lit as a choice of the visitor's.
    assert '<option value="views" selected>' in response.text
    assert "catalog-toolbar__sort--active" not in response.text
    assert "data-sort-kept" not in response.text


@pytest.mark.parametrize("sort", ["last_chapter_at", "name", "rate_avg"])
def test_a_picked_sort_stays_for_a_search(sort: str) -> None:
    response, catalog = _get("/catalog", query="поднятие уровня", sort=sort)

    assert catalog.calls[0]["sort"] == sort
    assert response.context["sort_kept"] is True
    assert "catalog-toolbar__sort--active" in response.text
    assert 'data-role="catalog-sort" data-sort-kept' in response.text


@pytest.mark.parametrize("query", [None, "", "   "])
def test_without_a_query_the_listing_keeps_its_own_default(query: str | None) -> None:
    params = {} if query is None else {"query": query}
    response, catalog = _get("/catalog", **params)

    assert catalog.calls[0]["sort"] == "last_chapter_at"
    assert response.context["default_sort"] == "last_chapter_at"
    assert response.context["editorial"] is True


def test_an_empty_sort_is_no_sort() -> None:
    _, catalog = _get("/catalog", query="поднятие уровня", sort="")
    assert catalog.calls[0]["sort"] == "views"


def test_a_sections_own_sort_is_kept_for_a_search_typed_there() -> None:
    """A search from «Новое в каталоге» keeps that section's order (catalog.html)."""
    response, _ = _get("/catalog", section="new")
    assert response.context["sort_kept"] is True
    assert 'data-role="catalog-sort" data-sort-kept' in response.text

    fresh, _ = _get("/catalog")
    assert fresh.context["sort_kept"] is False


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"query": "поднятие", "page": 2}, "views"),
        ({"query": "поднятие", "sort": "name", "page": 2}, "name"),
        ({"page": 2}, "last_chapter_at"),
    ],
)
def test_the_infinite_scroll_pages_go_the_same_way(
    params: dict[str, object], expected: str
) -> None:
    _, catalog = _get("/catalog/page", **params)
    assert catalog.calls[0]["sort"] == expected


def test_genre_links_from_a_search_carry_no_redundant_sort() -> None:
    genres = [Genre(id=5, name="Фэнтези"), Genre(id=8, name="Романтика")]
    response, _ = _get("/catalog", genres=genres, query="поднятие")
    links = response.context["genre_links"]
    assert links == {
        5: "/catalog?query=%D0%BF%D0%BE%D0%B4%D0%BD%D1%8F%D1%82%D0%B8%D0%B5&genres=5",
        8: "/catalog?query=%D0%BF%D0%BE%D0%B4%D0%BD%D1%8F%D1%82%D0%B8%D0%B5&genres=8",
    }

    picked, _ = _get("/catalog", genres=genres, query="поднятие", sort="name")
    assert all("sort=name" in href for href in picked.context["genre_links"].values())


def test_the_toolbar_leaves_an_untouched_sort_out_of_a_search() -> None:
    catalog = (ROOT / "app/templates/catalog.html").read_text(encoding="utf-8")
    assert "{{ static_url('js/catalog-search-sort.js') }}\" defer" in catalog
    assert 'if (!select || select.hasAttribute("data-sort-kept")) return;' in SCRIPT
    assert "if (!changed) select.disabled = true;" in SCRIPT
    assert 'window.addEventListener("pageshow"' in SCRIPT
