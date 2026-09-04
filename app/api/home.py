"""GET / — the search/open-title landing page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from ranobelib import RanobeLibError

from app.auth.dependencies import get_current_user
from app.db.connection import connection
from app.db.library import get_entry
from app.db.users import User
from app.reading_progress import reading_progress_percent
from app.recent_titles import forget, read_recent
from app.services.client import open_client
from app.templating import templates

router = APIRouter()


@router.get("/")
async def home(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {"active_nav": "home", "recent": await _recent_with_progress(request, user)},
    )


@router.post("/recent/{slug_url}/forget", response_model=None)
async def forget_recent_title(request: Request, slug_url: str) -> Response:
    """The "×" on a "Недавние" card (PR 69) - rewrites the cookie without this entry and
    returns 204 so recent-titles-forget.js can just drop the card from the page, no
    reload needed."""
    response = Response(status_code=204)
    forget(response, request, slug_url=slug_url)
    return response


async def _recent_with_progress(
    request: Request, user: User | None
) -> list[dict[str, str | int | None]]:
    """"Недавние" entries, each with a `progress_percent` alongside the usual `slug_url`/
    `name`/`cover_url` (see recent_titles.read_recent) - PR 68.

    Only computed for a logged-in user whose personal library (PR 14) has a matching
    `LibraryEntry` with recorded progress: an anonymous visitor has no `LibraryEntry` to
    match against at all, and a title only ever opened from the description page (never
    read) has no recorded position either. Neither case is a reason to start a parallel
    progress store on top of the one PR 27 already reads from `db/library.py`. conn is
    checked out here, not taken as a route-level Depends(get_connection) parameter, so an
    anonymous request never checks one out of the pool at all (see get_current_user()'s
    own docstring for the same reasoning).
    """
    recent = read_recent(request)
    if user is None:
        return [dict(item, progress_percent=None) for item in recent]
    result: list[dict[str, str | int | None]] = []
    async with connection() as conn:
        for item in recent:
            progress_percent = None
            entry = await get_entry(conn, user.id, item["slug_url"])
            if entry is not None and entry.last_read_volume is not None:
                try:
                    async with open_client(item["slug_url"]) as lib:
                        volumes = await lib.get_table_of_contents()
                    progress_percent = reading_progress_percent(
                        volumes, entry.last_read_volume, entry.last_read_number
                    )
                except RanobeLibError:
                    pass
            result.append(dict(item, progress_percent=progress_percent))
    return result
