"""Personal library: add/remove a title, list what's in it (the "Читаю" tab)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg import AsyncConnection
from ranobelib import RanobeLibError

from app.auth.dependencies import get_current_user, require_current_user
from app.db.connection import connection, get_connection
from app.db.library import (
    LibraryEntry,
    add_entry,
    get_currently_reading_entries,
    get_entry,
    list_entries,
    remove_entry,
    set_default_translation_index,
)
from app.db.users import User
from app.reading_progress import reading_progress_percent
from app.services.catalog import (
    get_catalog,
    list_catalog_titles,
    list_countries,
    list_genres,
    pick_random_title,
)
from app.services.client import get_client, open_client
from app.templating import templates

router = APIRouter(prefix="/library")
# PR 292 (wave 36): the catalog is its own top-level page now, next to /library rather
# than under it - its handlers live here with the rest of the library/catalog code.
catalog_router = APIRouter(prefix="/catalog")

# Catalog.list_titles()'s own known-accepted `sort` values (see its docstring - the SDK
# doesn't validate `sort` itself, so this is only for the dropdown, not enforced here).
DEFAULT_CATALOG_SORT = "last_chapter_at"
CATALOG_SORT_OPTIONS = {
    "last_chapter_at": "По обновлению",
    "name": "По названию",
    "created_at": "По дате добавления",
    "views": "По просмотрам",
    "chap_count": "По числу глав",
    "rate_avg": "По рейтингу",
    "random": "Случайно",
}


@router.get("", response_model=None)
async def show_library(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    tab: str | None = None,
) -> HTMLResponse | RedirectResponse:
    """Viewing the library page itself doesn't require an account - only an anonymous
    visitor can't have a personal reading list, so that's the one thing the page won't
    show them (library.html prompts them to log in/register instead of the list). conn is
    checked out below, not taken as a route-level Depends(get_connection) parameter, so an
    anonymous visitor never checks one out of the pool at all (see get_current_user()'s
    own docstring for the same reasoning).

    `tab` is no longer a view of this page (PR 293, Catalog handoff.md): old links keep
    working through a redirect - `?tab=all` to the catalog (any other query parameters
    kept), every other value (`reading`, `favorites`, `fav`, ...) to the plain library."""
    if tab is not None:
        if tab == "all":
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != "tab"]
            url = f"/catalog?{urlencode(rest)}" if rest else "/catalog"
            return RedirectResponse(url=url, status_code=301)
        return RedirectResponse(url="/library", status_code=301)
    if user is None:
        # A guest has no library to count - the tabs render without numbers.
        context = {**_library_context([]), "tab_counts": None}
        return templates.TemplateResponse(request, "library.html", context)
    async with connection() as conn:
        items = await library_items_for_user(user, conn)
    return templates.TemplateResponse(request, "library.html", _library_context(items))


@router.get("/favorites")
async def redirect_favorites() -> RedirectResponse:
    """The old "Избранное" page address (PR 293) - the plain library now."""
    return RedirectResponse(url="/library", status_code=301)


@router.get("/catalog")
@router.get("/catalog/page")
@router.get("/catalog/random")
async def redirect_old_catalog(request: Request) -> RedirectResponse:
    """The catalog's addresses before PR 292 moved it to /catalog - same path minus the
    /library prefix, with the query string passed through untouched (filters, search,
    sort and page all survive)."""
    path = request.url.path.removeprefix("/library")
    query = request.url.query
    return RedirectResponse(url=f"{path}?{query}" if query else path, status_code=301)


def _library_context(
    items: list[dict[str, LibraryEntry | str | int | None]],
) -> dict[str, object]:
    """PR 275 (Webnovells Redesign): the page splits the library into started titles
    ("Читаю", cards with progress) and not-started ones ("Ещё в библиотеке") - both from
    the same library_items_for_user() list, kept in its "most recently read first" order."""
    today = datetime.now(UTC).date()
    items = [
        {**item, "last_read_label": _last_read_label(item["entry"].last_read_at, today)}  # type: ignore[union-attr]
        for item in items
    ]
    reading = [item for item in items if item["entry"].last_read_volume is not None]  # type: ignore[union-attr]
    not_started = [item for item in items if item["entry"].last_read_volume is None]  # type: ignore[union-attr]
    return {
        "active_nav": "library",
        "active_tab": "reading",
        "items": items,
        "reading": reading,
        "not_started": not_started,
        "tab_counts": {"reading": len(reading)},
    }


_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _last_read_label(last_read_at: str | None, today: date) -> str | None:
    """The line under a "Читаю" card: "Читали сегодня", "Читали вчера" or "Последнее
    чтение 6 сентября" - "today" is the UTC calendar date, same as app/db/activity.py.
    The year is added only when it isn't the current one."""
    if last_read_at is None:
        return None
    read_at = datetime.fromisoformat(last_read_at)
    if read_at.tzinfo is None:
        read_at = read_at.replace(tzinfo=UTC)
    read_on = read_at.astimezone(UTC).date()
    if read_on == today:
        return "Читали сегодня"
    if read_on == today - timedelta(days=1):
        return "Читали вчера"
    label = f"Последнее чтение {read_on.day} {_MONTHS_GENITIVE[read_on.month - 1]}"
    if read_on.year != today.year:
        label += f" {read_on.year}"
    return label


@catalog_router.get("", response_model=None)
async def show_catalog(
    request: Request,
    query: str | None = None,
    sort: str = DEFAULT_CATALOG_SORT,
    genres: Annotated[list[int] | None, Query()] = None,
    countries: Annotated[list[int] | None, Query()] = None,
    tags: Annotated[list[int] | None, Query()] = None,
    tag_name: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    random_empty: bool = False,
) -> Response:
    """The catalog tab - unlike "Читаю", browsing it has never needed an account (see
    the "Список читаемого скрыт" copy on library.html's locked state).

    `genres` (PR 31/38): a genre badge on the title page links here with a single id,
    and the checkbox filter block on this page itself (PR 38) can send several - both
    are the same repeated `?genres=5&genres=8` query param, matching
    `Catalog.list_titles(genres=[...])`'s own parameter name. Their display names are
    resolved from `list_genres()` (SDK >=0.7.0) rather than forwarded from the caller,
    unlike PR 31's original `genre_name` workaround - there's a real endpoint for that
    now. Several selected genres mean "any of them" (PR 228), not the SDK's AND - see
    `list_catalog_titles()`.

    `countries` (PR 85, checkboxes since PR 100): a title only ever has one country of
    origin, but the *filter* still matches OR-style against several - same shape as
    `genres` (a repeated `?countries=1&countries=2` param), with OR semantics straight
    from `Catalog.list_titles(countries=[...])` (SDK >=0.9.0). Before PR 100
    this was a single-select radio group and a single `country: int | None` SDK
    parameter; garbage/non-numeric input now 422s the same way a garbage `genres` id
    already did, rather than being silently swallowed.

    `tags` (PR 86): same shape/semantics as `genres` (`Catalog.list_titles(tags=[...])`
    is also AND, also a list), but there's no `Catalog.list_tags()` to resolve a display
    name from just an id the way `list_genres()` does for genres - unlike PR 31's
    original genre_name workaround, this one can't be retired yet. `tag_name` carries the
    clicked tag's own already-known label (title.html already has it - it's the link's
    own text) through to the hint chip; only meaningful for the single-tag case a badge
    click produces today, so it's ignored if more than one tag id is present.
    """
    genres = genres or []
    countries = countries or []
    tags = tags or []
    if sort == "random":
        # PR 230: the no-JS path (catalog-random-redirect.js normally never lets the form
        # submit sort=random) - same one-random-title redirect, not a reshuffled list.
        params = _catalog_filter_params(query, genres, countries, tags, tag_name)
        return RedirectResponse(url=f"/catalog/random?{urlencode(params)}", status_code=303)
    all_genres = await list_genres()
    all_countries = await list_countries()
    async with get_catalog() as catalog:
        result = await list_catalog_titles(
            catalog,
            page=page,
            query=query or None,
            sort=sort,
            genres=genres,
            countries=countries,
            tags=tags,
        )
    genre_names_by_id = {genre.id: genre.name for genre in all_genres}
    country_names_by_id = {c.id: c.name for c in all_countries}
    selected_tag_names = (
        [tag_name] if len(tags) == 1 and tag_name else [str(t) for t in tags]
    )
    return templates.TemplateResponse(
        request,
        "catalog.html",
        {
            "active_nav": "library",
            "active_tab": "catalog",
            "items": result.items,
            "has_next_page": result.has_next_page,
            "page": page,
            "query": query,
            "sort": sort,
            "sort_options": CATALOG_SORT_OPTIONS,
            "all_genres": all_genres,
            "genres": genres,
            "selected_genre_names": [genre_names_by_id.get(g, str(g)) for g in genres],
            "all_countries": all_countries,
            "countries": countries,
            "selected_country_names": [country_names_by_id.get(c, str(c)) for c in countries],
            "tags": tags,
            "selected_tag_names": selected_tag_names,
            "tag_name": tag_name,
            "random_empty": random_empty,
        },
    )


@catalog_router.get("/random")
async def random_catalog_title(
    query: str | None = None,
    genres: Annotated[list[int] | None, Query()] = None,
    countries: Annotated[list[int] | None, Query()] = None,
    tags: Annotated[list[int] | None, Query()] = None,
    tag_name: str | None = None,
) -> RedirectResponse:
    """PR 230, "Случайно": straight to one random title matching the current filters,
    not the catalog list reshuffled (what `sort=random` alone gives - Ошибка 6). If the
    filters match nothing, back to the catalog with those same filters and a
    "nothing found" notice (`random_empty`) instead of an error page."""
    genres = genres or []
    countries = countries or []
    tags = tags or []
    async with get_catalog() as catalog:
        title = await pick_random_title(
            catalog, query=query or None, genres=genres, countries=countries, tags=tags
        )
    if title is not None:
        return RedirectResponse(url=f"/titles/{title.slug_url}", status_code=303)
    params = _catalog_filter_params(query, genres, countries, tags, tag_name)
    params.append(("random_empty", "1"))
    return RedirectResponse(url=f"/catalog?{urlencode(params)}", status_code=303)


def _catalog_filter_params(
    query: str | None,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    tag_name: str | None,
) -> list[tuple[str, str | int]]:
    params: list[tuple[str, str | int]] = []
    if query:
        params.append(("query", query))
    params += [("genres", g) for g in genres]
    params += [("countries", c) for c in countries]
    params += [("tags", t) for t in tags]
    if tag_name:
        params.append(("tag_name", tag_name))
    return params


@catalog_router.get("/page", response_model=None)
async def catalog_page_fragment(
    request: Request,
    query: str | None = None,
    sort: str = DEFAULT_CATALOG_SORT,
    genres: Annotated[list[int] | None, Query()] = None,
    countries: Annotated[list[int] | None, Query()] = None,
    tags: Annotated[list[int] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    """Just the card markup, no base.html - what catalog-scroll.js fetches and appends
    as the visitor scrolls (see app/static/js/catalog-scroll.js)."""
    async with get_catalog() as catalog:
        result = await list_catalog_titles(
            catalog,
            page=page,
            query=query or None,
            sort=sort,
            genres=genres or [],
            countries=countries or [],
            tags=tags or [],
        )
    response = templates.TemplateResponse(request, "_catalog_cards.html", {"items": result.items})
    response.headers["X-Has-Next-Page"] = "true" if result.has_next_page else "false"
    return response


@router.post("/add", response_model=None)
async def add_to_library_by_url(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    url: Annotated[str, Form()],
) -> Response:
    """The library page's own "paste a link" form - same URL resolution as `open_title`
    in app/api/titles.py (PR 4), just followed by adding the resolved title instead of
    only redirecting to it."""
    try:
        async with get_client(url) as lib:
            title = await lib.get_info()
    except ValueError:
        items = await library_items_for_user(user, conn)
        return templates.TemplateResponse(
            request,
            "library.html",
            {
                **_library_context(items),
                "error": "Не удалось распознать ссылку на тайтл",
                "submitted_url": url,
            },
            status_code=400,
        )
    await add_entry(conn, user.id, title.slug_url)
    return RedirectResponse(url=f"/titles/{title.slug_url}", status_code=303)


@router.post("/{slug_url}/add")
async def add_to_library(
    slug_url: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    async with open_client(slug_url) as lib:
        await lib.get_info()  # 404s via the usual TitleNotFoundError mapping if bogus
    await add_entry(conn, user.id, slug_url)
    return RedirectResponse(url=_safe_next(next, f"/titles/{slug_url}"), status_code=303)


@router.post("/{slug_url}/remove")
async def remove_from_library(
    slug_url: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    await remove_entry(conn, user.id, slug_url)
    return RedirectResponse(url=_safe_next(next, "/library"), status_code=303)


@router.post("/{slug_url}/default-translation")
async def set_title_default_translation(
    slug_url: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    translation_index: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """The dropdown next to .title-credits's "N глав несколько переводов" note (PR 205) -
    an empty `translation_index` (the "Спрашивать каждый раз" option) clears the saved
    default back to None, same as never having set one. Requires the title already being
    in the library (404 otherwise) - there's nowhere else to persist
    this per-user choice, see set_default_translation_index()'s own docstring."""
    entry = await get_entry(conn, user.id, slug_url)
    if entry is None:
        raise HTTPException(status_code=404, detail="Тайтл не в библиотеке")
    if translation_index == "":
        parsed_index = None
    else:
        try:
            parsed_index = int(translation_index)
        except ValueError:
            raise HTTPException(status_code=422, detail="Некорректный вариант перевода") from None
    await set_default_translation_index(conn, user.id, slug_url, parsed_index)
    return RedirectResponse(url=f"/titles/{slug_url}", status_code=303)


def _safe_next(next_url: str | None, default: str) -> str:
    """Only a same-site path is accepted as a redirect target - `next` comes straight
    from the request body, so anything else (an absolute URL, `//evil.example`) would be
    an open redirect."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default


async def library_items_for_user(
    user: User, conn: AsyncConnection
) -> list[dict[str, LibraryEntry | str | int | None]]:
    """Each entry's display name/cover/reading-progress, fetched fresh through the SDK
    (cheap - cache_dir makes it a local cache hit after the first request) rather than
    stored in our own DB, which would duplicate SDK response data. A title that's gone/
    unreachable on ranobelib.me doesn't take the whole page down with it - it just renders
    with its slug_url as a fallback label instead of a name, and no cover/progress.

    Public (not prefixed `_`) so app/api/profile.py (PR 122) can reuse it for the "Читает
    сейчас"/"Библиотека" sections on a profile page, instead of re-fetching the same SDK
    data through a second code path.
    """
    items: list[dict[str, LibraryEntry | str | int | None]] = []
    for entry in await list_entries(conn, user.id):
        name: str | None = None
        cover_url: str | None = None
        progress_percent: int | None = None
        try:
            async with open_client(entry.slug_url) as lib:
                title = await lib.get_info()
                name = title.rus_name or title.name
                cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
                # Only worth the extra fetch once there's a recorded position to place -
                # nothing to compute a percentage against for a never-opened entry.
                if entry.last_read_volume is not None:
                    volumes = await lib.get_table_of_contents()
                    progress_percent = reading_progress_percent(
                        volumes, entry.last_read_volume, entry.last_read_number
                    )
        except RanobeLibError:
            pass
        items.append(
            {
                "entry": entry,
                "name": name,
                "cover_url": cover_url,
                "progress_percent": progress_percent,
            }
        )
    return items


async def currently_reading_for_users(
    user_ids: list[int], conn: AsyncConnection
) -> dict[int, dict[str, LibraryEntry | str | int | None]]:
    """What each of these users (a viewer's friends, PR 200) is currently reading, keyed by
    user_id - the same per-entry name/cover/progress enrichment library_items_for_user()
    does for one user's whole library, just for a single "currently reading" entry across
    many users at once. The DB lookup itself is batched (one query via
    get_currently_reading_entries(), not N of them); the SDK enrichment loop below still
    makes one call per distinct entry, same as library_items_for_user()'s own loop - there's
    no bulk "get_info for many titles" in the SDK to batch that part with. A user_id with
    nothing currently being read is simply absent from the returned dict.
    """
    entries = await get_currently_reading_entries(conn, user_ids)
    result: dict[int, dict[str, LibraryEntry | str | int | None]] = {}
    for user_id, entry in entries.items():
        name: str | None = None
        cover_url: str | None = None
        progress_percent: int | None = None
        try:
            async with open_client(entry.slug_url) as lib:
                title = await lib.get_info()
                name = title.rus_name or title.name
                cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
                if entry.last_read_volume is not None:
                    volumes = await lib.get_table_of_contents()
                    progress_percent = reading_progress_percent(
                        volumes, entry.last_read_volume, entry.last_read_number
                    )
        except RanobeLibError:
            pass
        result[user_id] = {
            "entry": entry,
            "name": name,
            "cover_url": cover_url,
            "progress_percent": progress_percent,
        }
    return result
