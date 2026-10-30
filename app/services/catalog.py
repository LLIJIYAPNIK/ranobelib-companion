"""The only place in this application allowed to construct ``Catalog(...)``."""

import random
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import httpx
from ranobelib import Catalog, CatalogPage, RanobeLibError
from ranobelib.catalog import MAX_PER_PAGE, MIN_PER_PAGE
from ranobelib.models import Country, Genre, Label, Title

from app.config import get_settings
from app.services import sdk_outcomes


@asynccontextmanager
async def get_catalog() -> AsyncIterator[Catalog]:
    """An open `Catalog` client for listing/searching the ranobelib.me catalog, as an
    async context manager (``async with get_catalog() as catalog``).

    Shares the same `cache_dir`/`cache_ttl` as `get_client()` (app/services/client.py) -
    one common on-disk cache for the whole app, per CLAUDE.md, not a separate one for
    catalog browsing. PR 345: how the session ended goes to app/services/sdk_outcomes.py
    for the admin overview, same as `open_client()`'s.
    """
    settings = get_settings()
    async with (
        sdk_outcomes.watch(),
        Catalog(cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl) as catalog,
    ):
        yield catalog


async def list_genres() -> list[Genre]:
    """Every genre `Catalog.list_titles(genres=[...])` can filter by (PR 38's catalog
    genre-filter checkboxes), sourced from `Catalog.list_genres()` (SDK >=0.7.0) rather
    than a hardcoded id -> name table on the web layer, per CLAUDE.md's Golden rule.
    """
    async with get_catalog() as catalog:
        return await catalog.list_genres()


async def list_countries() -> list[Country]:
    """Every country `Catalog.list_titles(countries=[...])` can filter by (PR 85's
    "Страна" filter section, checkboxes since PR 100), sourced from
    `Catalog.list_countries()` (SDK >=0.8.0) rather than a hardcoded id -> name table on
    the web layer, same reasoning as `list_genres()`.
    """
    async with get_catalog() as catalog:
        return await catalog.list_countries()


async def list_statuses() -> list[Label]:
    """Every title status `Catalog.list_titles(statuses=[...])` can filter by (PR 303's
    «Статус» filter group), sourced from `Catalog.list_statuses()` (SDK >=0.12.0) rather
    than a hardcoded id -> label table, same reasoning as `list_genres()`.
    """
    async with get_catalog() as catalog:
        return await catalog.list_statuses()


# How many titles one page of the merged any-genre listing holds - list_titles()'s own
# `per_page` default, i.e. the same page size the 0/1-genre path (which doesn't pass
# `per_page` at all) already gets, so infinite scroll pages look the same either way.
_ANY_GENRE_PAGE_SIZE = 30
# Upper bound on API pages fetched per selected genre (each MAX_PER_PAGE titles) - keeps
# one merged listing a bounded amount of work however deep the visitor scrolls; past it
# the merged listing simply ends (has_next_page=False).
_MAX_PAGES_PER_GENRE = 5


async def list_catalog_titles(
    catalog: Catalog,
    *,
    page: int,
    query: str | None,
    sort: str,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    statuses: list[int] | None = None,
    min_chapters: int | None = None,
) -> CatalogPage:
    """One page of the catalog listing (PR 228): straight `Catalog.list_titles()` for 0
    or 1 selected genre - nothing changes on the wire there - and
    `list_titles_any_genre()` for 2+, since `list_titles(genres=[...])` means AND (the
    API's own semantics, see its docstring) while the filter panel promises "any of".
    """
    if len(genres) < 2:
        return await catalog.list_titles(
            page=page,
            query=query,
            sort=sort,
            genres=genres or None,
            countries=countries or None,
            tags=tags or None,
            statuses=statuses or None,
            min_chapters=min_chapters,
        )
    return await list_titles_any_genre(
        catalog,
        page=page,
        query=query,
        sort=sort,
        genres=genres,
        countries=countries,
        tags=tags,
        statuses=statuses,
        min_chapters=min_chapters,
    )


async def list_titles_any_genre(
    catalog: Catalog,
    *,
    page: int,
    query: str | None,
    sort: str,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    statuses: list[int] | None = None,
    min_chapters: int | None = None,
) -> CatalogPage:
    """Titles matching *any* of `genres` (OR), built from one single-genre
    `list_titles()` listing per genre, merged and re-paginated here.

    TEMPORARY workaround (PR 228): the ranobelib.me API - and so `Catalog.list_titles()`
    in SDK 0.12.0, the latest release - only filters genres with AND semantics and has no
    "any of" switch. This fan-out belongs in the SDK, not here.
    TODO(PR 228): replace with an SDK-side OR option once one exists.

    Merge order: every per-genre listing already comes back sorted by the API by the same
    `sort`, but listing items carry none of the values most sorts use: `Title` has no
    views, rating or created_at, and `last_chapter_at` (SDK 0.12.0) is filled in by
    `get_info()` only - it's always None on `list_titles()` items (ranobelib-python-sdk#74,
    PR 304). So the lists can't be re-sorted by that key here.
    Instead they're interleaved round-robin by rank (1st of each genre, then 2nd of each,
    ...), skipping titles already emitted - a title in two selected genres appears once.
    That keeps each genre's own order and puts the top results of every genre on the
    first page, but it's an approximation of one global sort, not the real thing.

    Requests go out one at a time through the single `catalog` client (no parallel calls
    around its own rate limiting) and lazily - a genre's next API page is only fetched
    once the merge actually reaches it - and each one goes through the SDK's own disk
    cache like any other `list_titles()` call, so scrolling further re-reads the earlier
    pages from cache rather than the API. `has_next_page` means "the merge produced at
    least one title past this page", not the API's own flag; the merge ends early once
    every genre hits `_MAX_PAGES_PER_GENRE`.
    """
    all_streams = [
        _genre_stream(
            catalog,
            genre,
            query=query,
            sort=sort,
            countries=countries,
            tags=tags,
            statuses=statuses,
            min_chapters=min_chapters,
        )
        for genre in genres
    ]
    start = (page - 1) * _ANY_GENRE_PAGE_SIZE
    needed = start + _ANY_GENRE_PAGE_SIZE + 1  # +1 to know whether a next page exists
    merged: list[Title] = []
    seen: set[int] = set()
    live = list(all_streams)
    try:
        while live and len(merged) < needed:
            for stream in list(live):
                title = await anext(stream, None)
                if title is None:
                    live.remove(stream)
                    continue
                if title.id in seen:
                    continue
                seen.add(title.id)
                merged.append(title)
                if len(merged) >= needed:
                    break
    finally:
        # Most streams stop mid-listing once the page is filled - close them explicitly
        # rather than leaving half-consumed async generators to the garbage collector.
        for stream in all_streams:
            await stream.aclose()
    return CatalogPage(
        items=merged[start : start + _ANY_GENRE_PAGE_SIZE],
        page=page,
        has_next_page=len(merged) > start + _ANY_GENRE_PAGE_SIZE,
    )


async def _genre_stream(
    catalog: Catalog,
    genre: int,
    *,
    query: str | None,
    sort: str,
    countries: list[int],
    tags: list[int],
    statuses: list[int] | None = None,
    min_chapters: int | None = None,
) -> AsyncGenerator[Title, None]:
    """One genre's listing, title by title, fetching API pages only as they're consumed."""
    for api_page in range(1, _MAX_PAGES_PER_GENRE + 1):
        result = await catalog.list_titles(
            page=api_page,
            per_page=MAX_PER_PAGE,
            query=query,
            sort=sort,
            genres=[genre],
            countries=countries or None,
            tags=tags or None,
            statuses=statuses or None,
            min_chapters=min_chapters,
        )
        for title in result.items:
            yield title
        if not result.has_next_page:
            return


async def pick_random_title(
    catalog: Catalog,
    *,
    query: str | None,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    statuses: list[int] | None = None,
    min_chapters: int | None = None,
) -> Title | None:
    """One random title matching the current catalog filters (PR 230's "Случайно"), or
    `None` if nothing matches them.

    `sort="random"` makes the API shuffle its listing; the first item is the pick.
    `refresh=True` is required, not optional: without it the SDK would serve this exact
    parameter combination from its disk cache for the whole `cache_ttl`, and "Случайно"
    would keep returning the same title to everyone for hours.

    Several genres mean "any of them" here too, like `list_catalog_titles()` (PR 228):
    rather than the SDK's AND, one of the selected genres is picked at random first, then
    a random title from it - trying the rest in random order if that genre has nothing
    left under the other filters. A title from a smaller genre (or in several selected
    genres) is somewhat likelier to come up than under a true uniform pick; a single
    request instead of listing every genre is the trade-off.
    """
    genre_choices: list[list[int] | None] = (
        [[g] for g in genres] if len(genres) >= 2 else [genres or None]
    )
    random.shuffle(genre_choices)
    for genre_filter in genre_choices:
        result = await catalog.list_titles(
            page=1,
            per_page=MIN_PER_PAGE,
            query=query,
            sort="random",
            genres=genre_filter,
            countries=countries or None,
            tags=tags or None,
            statuses=statuses or None,
            min_chapters=min_chapters,
            refresh=True,
        )
        if result.items:
            return result.items[0]
    return None


async def popular_titles(limit: int = 12) -> list[Title]:
    """The most viewed titles (the catalog's own «По просмотрам» sort), for the home
    page's «Популярное» shelf - a guest's and a new reader's way in. ranobelib.me being
    unreachable just means no shelf, not a failed home page."""
    try:
        async with get_catalog() as catalog:
            page = await catalog.list_titles(
                page=1, per_page=max(MIN_PER_PAGE, min(limit, MAX_PER_PAGE)), sort="views"
            )
    except (RanobeLibError, httpx.TransportError):
        return []
    return page.items[:limit]


# Issue 394: the shortest query worth a request, and the most suggestions one returns.
SUGGEST_MIN_QUERY = 2
SUGGEST_MAX = MIN_PER_PAGE


async def suggest_titles(query: str, limit: int = 8) -> list[Title]:
    """The titles «Найти тайтл по названию» suggests as the visitor types (issue 394):
    the first page of the very search the catalog runs for the same query on Enter, cut
    to `limit` - so a suggestion is never a title the catalog itself wouldn't list.
    Nothing for a query under SUGGEST_MIN_QUERY characters. No cache of its own: a
    repeated query is answered from the SDK's shared disk cache (`cache_ttl`), like any
    other `list_titles()` call (CLAUDE.md - no parallel cache over the SDK's)."""
    query = query.strip()
    if len(query) < SUGGEST_MIN_QUERY:
        return []
    limit = max(1, min(limit, SUGGEST_MAX))
    async with get_catalog() as catalog:
        page = await catalog.list_titles(page=1, per_page=MIN_PER_PAGE, query=query)
    return page.items[:limit]
