from unittest.mock import patch

from ranobelib import CatalogPage
from ranobelib.models import Country, Cover, Genre, Label, Title

from app.config import get_settings
from app.services.catalog import get_catalog, list_catalog_titles, list_countries, list_genres


def test_get_catalog_constructs_catalog_with_configured_cache() -> None:
    settings = get_settings()

    with patch("app.services.catalog.Catalog") as mock_catalog:
        get_catalog()

    mock_catalog.assert_called_once_with(cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl)


class _FakeCatalog:
    def __init__(
        self, genres: list[Genre] | None = None, countries: list[Country] | None = None
    ) -> None:
        self._genres = genres or []
        self._countries = countries or []

    async def __aenter__(self) -> "_FakeCatalog":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def list_genres(self) -> list[Genre]:
        return self._genres

    async def list_countries(self) -> list[Country]:
        return self._countries


async def test_list_genres_delegates_to_the_catalog_client() -> None:
    genres = [Genre(id=5, name="Фэнтези"), Genre(id=8, name="Романтика")]
    with patch("app.services.catalog.get_catalog", return_value=_FakeCatalog(genres=genres)):
        result = await list_genres()

    assert result == genres


async def test_list_countries_delegates_to_the_catalog_client() -> None:
    countries = [Country(id=1, name="Япония"), Country(id=2, name="Корея")]
    with patch("app.services.catalog.get_catalog", return_value=_FakeCatalog(countries=countries)):
        result = await list_countries()

    assert result == countries


# --- list_catalog_titles() / list_titles_any_genre() (PR 228) -------------------------


def _title(id_: int) -> Title:
    return Title(
        id=id_,
        name=f"Title {id_}",
        slug=f"title-{id_}",
        slug_url=f"{id_}--title-{id_}",
        cover=Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _GenreListingCatalog:
    """Serves `list_titles(genres=[g])` from a per-genre list of title ids, paginated by
    the `per_page` asked for - the API's AND listing for a single genre."""

    def __init__(self, by_genre: dict[int, list[int]]) -> None:
        self._by_genre = by_genre
        self.calls: list[dict[str, object]] = []

    async def list_titles(self, **kwargs: object) -> CatalogPage:
        self.calls.append(kwargs)
        genres = kwargs["genres"]
        ids = self._by_genre[genres[0]] if genres else []  # type: ignore[index]
        page = kwargs.get("page", 1)
        per_page = kwargs.get("per_page", 30)
        start = (page - 1) * per_page  # type: ignore[operator]
        chunk = ids[start : start + per_page]  # type: ignore[operator]
        return CatalogPage(
            items=[_title(i) for i in chunk],
            page=page,  # type: ignore[arg-type]
            has_next_page=start + per_page < len(ids),  # type: ignore[operator]
        )


async def _list(catalog: _GenreListingCatalog, genres: list[int], page: int = 1) -> CatalogPage:
    return await list_catalog_titles(
        catalog,  # type: ignore[arg-type]
        page=page,
        query=None,
        sort="last_chapter_at",
        genres=genres,
        countries=[],
        tags=[],
    )


async def test_single_genre_goes_straight_to_the_sdk() -> None:
    catalog = _GenreListingCatalog({5: [1, 2]})

    result = await _list(catalog, [5])

    assert [t.id for t in result.items] == [1, 2]
    assert len(catalog.calls) == 1
    assert catalog.calls[0]["genres"] == [5]
    assert "per_page" not in catalog.calls[0]


async def test_no_genre_passes_none_to_the_sdk() -> None:
    catalog = _GenreListingCatalog({})

    await _list(catalog, [])

    assert catalog.calls == [
        {
            "page": 1,
            "query": None,
            "sort": "last_chapter_at",
            "genres": None,
            "countries": None,
            "tags": None,
        }
    ]


async def test_title_in_only_one_of_two_genres_is_included() -> None:
    catalog = _GenreListingCatalog({5: [1], 8: [2]})

    result = await _list(catalog, [5, 8])

    assert {t.id for t in result.items} == {1, 2}


async def test_title_in_both_genres_appears_once() -> None:
    catalog = _GenreListingCatalog({5: [1, 3], 8: [3, 2]})

    result = await _list(catalog, [5, 8])

    assert [t.id for t in result.items] == [1, 3, 2]


async def test_genres_are_interleaved_by_rank() -> None:
    catalog = _GenreListingCatalog({5: [1, 2, 3], 8: [10, 20]})

    result = await _list(catalog, [5, 8])

    assert [t.id for t in result.items] == [1, 10, 2, 20, 3]
    assert result.has_next_page is False


async def test_other_filters_are_forwarded_to_every_genre_listing() -> None:
    catalog = _GenreListingCatalog({5: [], 8: []})

    await list_catalog_titles(
        catalog,  # type: ignore[arg-type]
        page=1,
        query="dxd",
        sort="rate_avg",
        genres=[5, 8],
        countries=[1],
        tags=[7],
    )

    assert [c["genres"] for c in catalog.calls] == [[5], [8]]
    for call in catalog.calls:
        assert call["query"] == "dxd"
        assert call["sort"] == "rate_avg"
        assert call["countries"] == [1]
        assert call["tags"] == [7]


async def test_merged_listing_is_paginated_in_pages_of_thirty() -> None:
    catalog = _GenreListingCatalog({5: list(range(1, 41)), 8: list(range(101, 141))})

    first = await _list(catalog, [5, 8], page=1)
    second = await _list(catalog, [5, 8], page=2)
    third = await _list(catalog, [5, 8], page=3)

    assert len(first.items) == 30 and first.has_next_page is True
    assert len(second.items) == 30 and second.has_next_page is True
    assert len(third.items) == 20 and third.has_next_page is False
    all_ids = [t.id for t in first.items + second.items + third.items]
    assert sorted(all_ids) == list(range(1, 41)) + list(range(101, 141))


async def test_genre_pages_are_fetched_only_as_the_merge_needs_them() -> None:
    # 60 per API page: page 1 of the merge (31 titles incl. the look-ahead) never needs
    # a second API page of either genre.
    catalog = _GenreListingCatalog({5: list(range(1, 200)), 8: list(range(1001, 1200))})

    await _list(catalog, [5, 8], page=1)

    assert [(c["genres"], c["page"]) for c in catalog.calls] == [([5], 1), ([8], 1)]


async def test_each_genre_is_capped_at_five_api_pages() -> None:
    catalog = _GenreListingCatalog({5: list(range(1, 1000)), 8: []})

    last = await _list(catalog, [5, 8], page=10)  # items 271-300 of genre 5's first 300
    past_the_cap = await _list(catalog, [5, 8], page=11)

    assert len(last.items) == 30 and last.has_next_page is False
    assert past_the_cap.items == []
    assert max(c["page"] for c in catalog.calls if c["genres"] == [5]) == 5  # type: ignore[type-var]
