"""Personal library: add/remove a title, list what's in it (the "Читаю" tab)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from psycopg import AsyncConnection
from ranobelib import RanobeLibError

from app.auth.dependencies import get_current_user, require_current_user
from app.db.connection import connection, get_connection
from app.db.library import (
    LibraryEntry,
    add_entry,
    get_currently_reading_entries,
    get_entry,
    library_slugs,
    list_entries,
    remove_entry,
    set_default_translation_index,
)
from app.db.users import User
from app.reading_progress import reading_progress_percent
from app.services.catalog import (
    catalog_stream,
    get_catalog,
    list_countries,
    list_genres,
    list_statuses,
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
# PR 303: the «Количество глав» thresholds - CatalogDesktop/CatalogMobile's own presets,
# a UI choice like the sort labels above, not domain data. Sent as `min_chapters`
# (Catalog.list_titles(min_chapters=...), SDK >=0.12.0); 0 is «Любое», no filter.
CATALOG_MIN_CHAPTERS_OPTIONS = (100, 500, 1000)


def _is_editorial(
    query: str | None,
    sort: str,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    statuses: list[int],
    min_chapters: int,
) -> bool:
    """PR 295 (Catalog handoff.md): the catalog's default "editorial" mode - no search,
    the default sort and no filter - gets the featured inserts; anything else is the
    "results" mode, a plain listing of exactly what was asked for plus the criteria chips."""
    return (
        not (query or "").strip()
        and sort == DEFAULT_CATALOG_SORT
        and not genres
        and not countries
        and not tags
        and not statuses
        and not min_chapters
    )


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
        # A guest has no library to count - the switch renders without a number.
        context = {**_library_context([]), "library_count": None}
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
    """The library page's context, from library_items_for_user() in its "most recently
    read first" order. PR 297 (LibraryDesktop): one «Моя библиотека» grid of every title,
    with the most recently read one also as the «Продолжить чтение» hero."""
    today = datetime.now(UTC).date()
    items = [
        {**item, "last_read_label": _last_read_label(item["entry"].last_read_at, today)}  # type: ignore[union-attr]
        for item in items
    ]
    # A title never opened can't be continued - the hero is the first one that was.
    continue_item = next(
        (item for item in items if item["entry"].last_read_volume is not None),  # type: ignore[union-attr]
        None,
    )
    return {
        "active_nav": "library",
        "active_tab": "library",
        "items": items,
        "continue_item": continue_item,
        # PR 294: the «Библиотека» switch item counts every title, started or not.
        "library_count": len(items),
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
    user: Annotated[User | None, Depends(get_current_user)],
    query: str | None = None,
    sort: str = DEFAULT_CATALOG_SORT,
    genres: Annotated[list[int] | None, Query()] = None,
    countries: Annotated[list[int] | None, Query()] = None,
    tags: Annotated[list[int] | None, Query()] = None,
    statuses: Annotated[list[int] | None, Query()] = None,
    min_chapters: Annotated[int, Query(ge=0)] = 0,
    tag_name: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    shown: Annotated[int, Query(ge=0)] = 0,
    featured: Annotated[int, Query(ge=0)] = 0,
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

    `statuses` / `min_chapters` (PR 303): the «Статус» and «Количество глав» groups,
    straight through to `Catalog.list_titles(statuses=[...], min_chapters=...)` (SDK
    >=0.12.0) - statuses match OR-style (a title has one status), like `countries`, and
    their labels come from `list_statuses()`. `min_chapters=0` («Любое») is no filter.
    """
    genres = genres or []
    countries = countries or []
    tags = tags or []
    statuses = statuses or []
    if sort == "random":
        # PR 230: the no-JS path (catalog-random-redirect.js normally never lets the form
        # submit sort=random) - same one-random-title redirect, not a reshuffled list.
        params = _catalog_filter_params(
            query, genres, countries, tags, tag_name, statuses, min_chapters
        )
        return RedirectResponse(url=f"/catalog/random?{urlencode(params)}", status_code=303)
    in_library = await _in_library(user)
    # PR 294: the «Библиотека» switch item shows the library's size here too.
    library_count = len(in_library) if in_library is not None else None
    all_genres = await list_genres()
    all_countries = await list_countries()
    all_statuses = await list_statuses()
    editorial = _is_editorial(query, sort, genres, countries, tags, statuses, min_chapters)
    async with get_catalog() as catalog:
        stream = await catalog_stream(
            catalog,
            editorial=editorial,
            page=page,
            shown=shown,
            featured=featured,
            query=query or None,
            sort=sort,
            genres=genres,
            countries=countries,
            tags=tags,
            statuses=statuses,
            min_chapters=min_chapters or None,
        )
    genre_names_by_id = {genre.id: genre.name for genre in all_genres}
    country_names_by_id = {c.id: c.name for c in all_countries}
    selected_tag_names = (
        [tag_name] if len(tags) == 1 and tag_name else [str(t) for t in tags]
    )
    selected_genre_names = [genre_names_by_id.get(g, str(g)) for g in genres]
    selected_country_names = [country_names_by_id.get(c, str(c)) for c in countries]
    status_labels_by_id = {s.id: s.label for s in all_statuses}
    selected_status_labels = [status_labels_by_id.get(s, str(s)) for s in statuses]
    return templates.TemplateResponse(
        request,
        "catalog.html",
        {
            "active_nav": "library",
            "active_tab": "catalog",
            "stream": stream,
            "items": stream.items,
            "has_next_page": stream.has_next_page,
            "page": page,
            "query": query,
            "sort": sort,
            "sort_options": CATALOG_SORT_OPTIONS,
            "all_genres": all_genres,
            "genres": genres,
            "selected_genre_names": selected_genre_names,
            "all_countries": all_countries,
            "countries": countries,
            "selected_country_names": selected_country_names,
            "all_statuses": all_statuses,
            "statuses": statuses,
            "min_chapters": min_chapters,
            "min_chapters_options": CATALOG_MIN_CHAPTERS_OPTIONS,
            "tags": tags,
            "selected_tag_names": selected_tag_names,
            "tag_name": tag_name,
            "random_empty": random_empty,
            "editorial": editorial,
            "criteria_chips": [] if editorial else _criteria_chips(
                query=query,
                sort=sort,
                genres=genres,
                countries=countries,
                tags=tags,
                tag_name=tag_name,
                statuses=statuses,
                min_chapters=min_chapters,
                status_labels=selected_status_labels,
                genre_names=selected_genre_names,
                country_names=selected_country_names,
                tag_names=selected_tag_names,
            ),
            "library_count": library_count,
            "in_library": in_library,
        },
    )


@catalog_router.get("/random")
async def random_catalog_title(
    query: str | None = None,
    genres: Annotated[list[int] | None, Query()] = None,
    countries: Annotated[list[int] | None, Query()] = None,
    tags: Annotated[list[int] | None, Query()] = None,
    statuses: Annotated[list[int] | None, Query()] = None,
    min_chapters: Annotated[int, Query(ge=0)] = 0,
    tag_name: str | None = None,
) -> RedirectResponse:
    """PR 230, "Случайно": straight to one random title matching the current filters,
    not the catalog list reshuffled (what `sort=random` alone gives - Ошибка 6). If the
    filters match nothing, back to the catalog with those same filters and a
    "nothing found" notice (`random_empty`) instead of an error page."""
    genres = genres or []
    countries = countries or []
    tags = tags or []
    statuses = statuses or []
    async with get_catalog() as catalog:
        title = await pick_random_title(
            catalog,
            query=query or None,
            genres=genres,
            countries=countries,
            tags=tags,
            statuses=statuses,
            min_chapters=min_chapters or None,
        )
    if title is not None:
        return RedirectResponse(url=f"/titles/{title.slug_url}", status_code=303)
    params = _catalog_filter_params(
        query, genres, countries, tags, tag_name, statuses, min_chapters
    )
    params.append(("random_empty", "1"))
    return RedirectResponse(url=f"/catalog?{urlencode(params)}", status_code=303)


def _criteria_chips(
    *,
    query: str | None,
    sort: str,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    tag_name: str | None,
    statuses: list[int],
    min_chapters: int,
    status_labels: list[str],
    genre_names: list[str],
    country_names: list[str],
    tag_names: list[str],
) -> list[dict[str, str]]:
    """PR 295: the results mode's row of active criteria (Catalog handoff.md) - search,
    a non-default sort, each status and the chapter threshold (PR 303), each genre,
    country and tag. Each chip links to /catalog with every criterion but its own, so a
    click drops just that one; with none left the catalog is back in the editorial mode."""
    criteria: list[tuple[str, list[tuple[str, str | int]]]] = []
    if (query or "").strip():
        criteria.append((f"«{query.strip()}»", [("query", query.strip())]))  # type: ignore[union-attr]
    if sort != DEFAULT_CATALOG_SORT:
        criteria.append((CATALOG_SORT_OPTIONS.get(sort, sort), [("sort", sort)]))
    criteria += [
        (label, [("statuses", s)]) for s, label in zip(statuses, status_labels, strict=True)
    ]
    if min_chapters:
        criteria.append((f"от {min_chapters} глав", [("min_chapters", min_chapters)]))
    criteria += [(name, [("genres", g)]) for g, name in zip(genres, genre_names, strict=True)]
    criteria += [
        (name, [("countries", c)]) for c, name in zip(countries, country_names, strict=True)
    ]
    # tag_name only names a lone tag (see show_catalog) - it goes with that tag's chip.
    tag_extra: list[tuple[str, str | int]] = (
        [("tag_name", tag_name)] if tag_name and len(tags) == 1 else []
    )
    criteria += [
        (name, [("tags", t), *tag_extra]) for t, name in zip(tags, tag_names, strict=True)
    ]
    chips = []
    for index, (label, _) in enumerate(criteria):
        rest = [param for i, (_, params) in enumerate(criteria) if i != index for param in params]
        href = f"/catalog?{urlencode(rest)}" if rest else "/catalog"
        chips.append({"label": label, "href": href})
    return chips


def _catalog_filter_params(
    query: str | None,
    genres: list[int],
    countries: list[int],
    tags: list[int],
    tag_name: str | None,
    statuses: list[int],
    min_chapters: int,
) -> list[tuple[str, str | int]]:
    params: list[tuple[str, str | int]] = []
    if query:
        params.append(("query", query))
    params += [("genres", g) for g in genres]
    params += [("countries", c) for c in countries]
    params += [("tags", t) for t in tags]
    params += [("statuses", s) for s in statuses]
    if min_chapters:
        params.append(("min_chapters", min_chapters))
    if tag_name:
        params.append(("tag_name", tag_name))
    return params


async def _in_library(user: User | None) -> set[str] | None:
    """PR 298: the slug_urls already in the visitor's library - the «В библиотеку» /
    «В библиотеке» state of each catalog card; None for a guest (no toggle, a login
    link). Checked out only for a logged-in visitor (same reasoning as show_library)."""
    if user is None:
        return None
    async with connection() as conn:
        return await library_slugs(conn, user.id)


@catalog_router.get("/page", response_model=None)
async def catalog_page_fragment(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    query: str | None = None,
    sort: str = DEFAULT_CATALOG_SORT,
    genres: Annotated[list[int] | None, Query()] = None,
    countries: Annotated[list[int] | None, Query()] = None,
    tags: Annotated[list[int] | None, Query()] = None,
    statuses: Annotated[list[int] | None, Query()] = None,
    min_chapters: Annotated[int, Query(ge=0)] = 0,
    page: Annotated[int, Query(ge=1)] = 1,
    shown: Annotated[int, Query(ge=0)] = 0,
    featured: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """Just the card markup, no base.html - what catalog-scroll.js fetches and appends
    as the visitor scrolls (see app/static/js/catalog-scroll.js). PR 295: `shown` /
    `featured` are the feed cursor from the previous page (catalog_stream()), the new
    one goes back in X-Catalog-Shown / X-Catalog-Featured."""
    genres = genres or []
    countries = countries or []
    tags = tags or []
    statuses = statuses or []
    editorial = _is_editorial(query, sort, genres, countries, tags, statuses, min_chapters)
    async with get_catalog() as catalog:
        stream = await catalog_stream(
            catalog,
            editorial=editorial,
            page=page,
            shown=shown,
            featured=featured,
            query=query or None,
            sort=sort,
            genres=genres,
            countries=countries,
            tags=tags,
            statuses=statuses,
            min_chapters=min_chapters or None,
        )
    response = templates.TemplateResponse(
        request,
        "_catalog_cards.html",
        {
            "items": stream.items,
            "editorial": editorial,
            "page": page,
            "has_next_page": stream.has_next_page,
            "in_library": await _in_library(user),
        },
    )
    response.headers["X-Has-Next-Page"] = "true" if stream.has_next_page else "false"
    response.headers["X-Catalog-Shown"] = str(stream.shown)
    response.headers["X-Catalog-Featured"] = str(stream.featured)
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


def _wants_json(request: Request) -> bool:
    """PR 298: the library card menu and the catalog's «В библиотеку» toggle call add /
    remove with fetch() and `Accept: application/json` - they get the new state back
    instead of the redirect a plain form post follows."""
    return "application/json" in request.headers.get("accept", "")


@router.post("/{slug_url}/add", response_model=None)
async def add_to_library(
    request: Request,
    slug_url: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    next: Annotated[str | None, Form()] = None,
) -> Response:
    async with open_client(slug_url) as lib:
        await lib.get_info()  # 404s via the usual TitleNotFoundError mapping if bogus
    await add_entry(conn, user.id, slug_url)
    if _wants_json(request):
        return JSONResponse({"in_library": True})
    return RedirectResponse(url=_safe_next(next, f"/titles/{slug_url}"), status_code=303)


@router.post("/{slug_url}/remove", response_model=None)
async def remove_from_library(
    request: Request,
    slug_url: str,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    next: Annotated[str | None, Form()] = None,
) -> Response:
    await remove_entry(conn, user.id, slug_url)
    if _wants_json(request):
        return JSONResponse({"in_library": False})
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
