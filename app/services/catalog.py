"""The only place in this application allowed to construct ``Catalog(...)``."""

import random
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal

from ranobelib import Catalog, CatalogPage
from ranobelib.catalog import MAX_PER_PAGE, MIN_PER_PAGE
from ranobelib.models import Country, Genre, Label, Title

from app.config import get_settings


def get_catalog() -> Catalog:
    """Build a `Catalog` client for listing/searching the ranobelib.me catalog.

    Shares the same `cache_dir`/`cache_ttl` as `get_client()` (app/services/client.py) -
    one common on-disk cache for the whole app, per CLAUDE.md, not a separate one for
    catalog browsing.
    """
    settings = get_settings()
    return Catalog(cache_dir=settings.cache_dir, cache_ttl=settings.cache_ttl)


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


# PR 295 (Catalog handoff.md, `buildStream` in catalog-data.js): in the editorial mode one
# featured title - the next one by views - follows every 12 regular cards, counted over
# the whole feed rather than per page.
FEATURED_EVERY = 12
_EDITORIAL_SORT = "last_chapter_at"
_FEATURED_SORT = "views"
# Upper bound on views pages read for one feed page - plenty for the inserts a page
# can need, a guard against a runaway loop if the API keeps saying "more".
_MAX_FEATURED_PAGES = 5


@dataclass(frozen=True)
class StreamItem:
    kind: Literal["card", "featured"]
    title: Title
    # 1-based number of a featured insert on the feed (the design's decorative
    # «Выпуск N») - not a rank by views. None for a regular card.
    issue: int | None = None


@dataclass(frozen=True)
class CatalogStream:
    """One page of the catalog feed plus the cursor to ask for the next one with:
    `shown` regular cards and `featured` inserts so far, over the whole feed."""

    items: list[StreamItem]
    has_next_page: bool
    shown: int
    featured: int


def interleave(
    regular: list[Title],
    top: list[Title],
    *,
    shown: int,
    featured: int,
    every: int = FEATURED_EVERY,
) -> tuple[list[StreamItem], int, int]:
    """`buildStream` from the design, resumable across pages: regular titles in order,
    minus any title of the featured buffer `top` (so a title is never both a featured
    insert and a regular card); after every `every`-th regular card of the whole feed
    (`shown` carries the count from earlier pages) the next title of `top` (from index
    `featured`) as a featured insert. Returns the items and the updated cursor. Pure -
    no I/O - so the rhythm and the dedup are testable on their own."""
    top_ids = {title.id for title in top}
    items: list[StreamItem] = []
    for title in regular:
        if title.id in top_ids:
            continue
        items.append(StreamItem("card", title))
        shown += 1
        if shown % every == 0 and featured < len(top):
            items.append(StreamItem("featured", top[featured], issue=featured + 1))
            featured += 1
    return items, shown, featured


async def catalog_stream(
    catalog: Catalog,
    *,
    editorial: bool,
    page: int,
    shown: int,
    featured: int,
    query: str | None,
    sort: str,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    statuses: list[int] | None = None,
    min_chapters: int | None = None,
) -> CatalogStream:
    """One page of the catalog feed. The results mode (a search, another sort or any
    filter) is the plain listing - list_catalog_titles(), no inserts, cursor untouched.
    The editorial mode adds the featured inserts: the regular `last_chapter_at` page,
    then as many `views` pages as the inserts due on this page need (at least the first
    one, so the dedup set never shrinks between pages), all through the one `catalog`
    client and its cache like any other listing - a composition of two SDK listings on
    the web layer, not new domain logic (see CLAUDE.md, wave 36)."""
    if not editorial:
        result = await list_catalog_titles(
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
        items = [StreamItem("card", title) for title in result.items]
        return CatalogStream(items, result.has_next_page, shown, featured)

    regular = await list_catalog_titles(
        catalog,
        page=page,
        query=None,
        sort=_EDITORIAL_SORT,
        genres=[],
        countries=[],
        tags=[],
        statuses=[],
        min_chapters=None,
    )
    # Inserts this page can hold at most (if none of its titles is dropped as a dup).
    slots = (shown + len(regular.items)) // FEATURED_EVERY - shown // FEATURED_EVERY
    top: list[Title] = []
    for views_page in range(1, _MAX_FEATURED_PAGES + 1):
        result = await catalog.list_titles(page=views_page, sort=_FEATURED_SORT)
        top.extend(result.items)
        if len(top) >= featured + slots or not result.has_next_page:
            break
    items, shown, featured = interleave(regular.items, top, shown=shown, featured=featured)
    return CatalogStream(items, regular.has_next_page, shown, featured)


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
