"""Personal library: add/remove a title, list what's in it (the "Читаю" tab)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from ranobelib import RanobeLibError

from app.auth.dependencies import get_current_user, require_current_user
from app.db.connection import get_connection
from app.db.library import LibraryEntry, add_entry, list_entries, remove_entry
from app.db.users import User
from app.services.catalog import get_catalog
from app.services.client import get_client, open_client
from app.templating import templates

router = APIRouter(prefix="/library")

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


@router.get("")
async def show_library(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Viewing the library page itself doesn't require an account - only an anonymous
    visitor can't have a personal reading list, so that's the one thing the page won't
    show them (library.html prompts them to log in/register instead of the list)."""
    items = await _library_items(user) if user is not None else []
    return templates.TemplateResponse(
        request,
        "library.html",
        {"active_nav": "library", "active_tab": "reading", "items": items},
    )


@router.get("/catalog")
async def show_catalog(
    request: Request,
    query: str | None = None,
    sort: str = DEFAULT_CATALOG_SORT,
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    """The catalog tab - unlike "Читаю", browsing it has never needed an account (see
    the "Список читаемого скрыт" copy on library.html's locked state)."""
    async with get_catalog() as catalog:
        result = await catalog.list_titles(page=page, query=query or None, sort=sort)
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
        },
    )


@router.get("/catalog/page", response_model=None)
async def catalog_page_fragment(
    request: Request,
    query: str | None = None,
    sort: str = DEFAULT_CATALOG_SORT,
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    """Just the card markup, no base.html - what catalog-scroll.js fetches and appends
    as the visitor scrolls (see app/static/js/catalog-scroll.js)."""
    async with get_catalog() as catalog:
        result = await catalog.list_titles(page=page, query=query or None, sort=sort)
    response = templates.TemplateResponse(request, "_catalog_cards.html", {"items": result.items})
    response.headers["X-Has-Next-Page"] = "true" if result.has_next_page else "false"
    return response


@router.post("/add", response_model=None)
async def add_to_library_by_url(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    url: Annotated[str, Form()],
) -> Response:
    """The library page's own "paste a link" form - same URL resolution as `open_title`
    in app/api/titles.py (PR 4), just followed by adding the resolved title instead of
    only redirecting to it."""
    try:
        async with get_client(url) as lib:
            title = await lib.get_info()
    except ValueError:
        items = await _library_items(user)
        return templates.TemplateResponse(
            request,
            "library.html",
            {
                "active_nav": "library",
                "items": items,
                "error": "Не удалось распознать ссылку на тайтл",
                "submitted_url": url,
            },
            status_code=400,
        )
    add_entry(get_connection(), user.id, title.slug_url)
    return RedirectResponse(url=f"/titles/{title.slug_url}", status_code=303)


@router.post("/{slug_url}/add")
async def add_to_library(
    slug_url: str,
    user: Annotated[User, Depends(require_current_user)],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    async with open_client(slug_url) as lib:
        await lib.get_info()  # 404s via the usual TitleNotFoundError mapping if bogus
    add_entry(get_connection(), user.id, slug_url)
    return RedirectResponse(url=_safe_next(next, f"/titles/{slug_url}"), status_code=303)


@router.post("/{slug_url}/remove")
async def remove_from_library(
    slug_url: str,
    user: Annotated[User, Depends(require_current_user)],
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    remove_entry(get_connection(), user.id, slug_url)
    return RedirectResponse(url=_safe_next(next, "/library"), status_code=303)


def _safe_next(next_url: str | None, default: str) -> str:
    """Only a same-site path is accepted as a redirect target - `next` comes straight
    from the request body, so anything else (an absolute URL, `//evil.example`) would be
    an open redirect."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default


async def _library_items(user: User) -> list[dict[str, LibraryEntry | str | None]]:
    """Each entry's display name, fetched fresh through the SDK (cheap - cache_dir makes
    it a local cache hit after the first request) rather than stored in our own DB, which
    would duplicate SDK response data. A title that's gone/unreachable on ranobelib.me
    doesn't take the whole page down with it - it just renders with its slug_url as a
    fallback label instead of a name.
    """
    items: list[dict[str, LibraryEntry | str | None]] = []
    for entry in list_entries(get_connection(), user.id):
        name: str | None = None
        try:
            async with open_client(entry.slug_url) as lib:
                title = await lib.get_info()
            name = title.name
        except RanobeLibError:
            name = None
        items.append({"entry": entry, "name": name})
    return items
