"""Title lookup: resolve a pasted link/slug, fetch metadata, render the title page."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from ranobelib import RanobeLibError

from app.auth.dependencies import get_current_user
from app.db.connection import get_connection
from app.db.library import get_entry
from app.db.users import User
from app.reading_progress import reading_progress_percent
from app.recent_titles import remember
from app.services.client import get_client, open_client
from app.services.exports import available_export_formats
from app.templating import templates

router = APIRouter(prefix="/titles")


@router.get("/open", response_model=None)
async def open_title(request: Request, url: str) -> Response:
    """Resolve the pasted URL/slug to its canonical slug_url and redirect there.

    `RanobeLib(url)` raises a plain `ValueError` (not a `RanobeLibError`) when `url`
    doesn't contain a recognizable `{id}--{slug}` segment at all - this is a client input
    problem, not something ranobelib.me could answer, so it's handled here rather than by
    the RanobeLibError -> HTTP mapping (app/exceptions.py).
    """
    try:
        async with get_client(url) as lib:
            title = await lib.get_info()
    except ValueError:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "active_nav": "home",
                "error": "Не удалось распознать ссылку на тайтл",
                "submitted_url": url,
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/titles/{title.slug_url}", status_code=302)


@router.get("/{slug_url}")
async def show_title(
    request: Request,
    slug_url: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
    finished: bool = False,
) -> HTMLResponse:
    async with open_client(slug_url) as lib:
        title = await lib.get_info()
        volumes = await lib.get_table_of_contents()
    cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
    library_entry = None
    if current_user is not None:
        library_entry = get_entry(get_connection(), current_user.id, title.slug_url)
    progress_percent = (
        reading_progress_percent(
            volumes, library_entry.last_read_volume, library_entry.last_read_number
        )
        if library_entry is not None
        else None
    )
    response = templates.TemplateResponse(
        request,
        "title.html",
        {
            "title": title,
            "cover_url": cover_url,
            "volumes": volumes,
            "export_formats": available_export_formats(),
            "in_library": library_entry is not None,
            "progress_percent": progress_percent,
            # PR 75: tap-to-read's own "past the last paragraph of the last chapter" tap
            # lands here with ?finished=1 - a plain query flag, not persisted state, so a
            # reload/bookmark of this same URL won't keep showing the notice forever.
            "finished": finished,
        },
    )
    remember(
        response,
        request,
        slug_url=title.slug_url,
        name=title.rus_name or title.name,
        cover_url=cover_url,
    )
    return response


@router.get("/{slug_url}/quickview", response_model=None)
async def title_quickview(request: Request, slug_url: str) -> HTMLResponse:
    """PR 117: just the metadata markup, no base.html - what the "quick view" eye icon
    on a title card (_title_card.html) fetches into its modal (title-quickview.js).
    Reuses the exact same open_client()/get_info() call show_title() makes above - no new
    metadata-assembly logic - just skips get_table_of_contents(), since the modal only
    shows the summary/badges/credits already available from get_info(), not the chapter
    list.
    """
    async with open_client(slug_url) as lib:
        title = await lib.get_info()
    cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
    return templates.TemplateResponse(
        request,
        "_title_quickview.html",
        {"title": title, "cover_url": cover_url},
    )


@router.get("/{slug_url}/size-estimate")
async def title_size_estimate(slug_url: str) -> dict[str, str | None]:
    """PR 43: `estimate_title_size()` samples several chapters' content, which can take
    a few seconds - fetched by the title page's own JS after the page has already
    rendered (title-size-estimate.js), instead of blocking `show_title()` on it.
    """
    async with open_client(slug_url) as lib:
        try:
            estimated_size = await lib.estimate_title_size()
        except RanobeLibError:
            # A sampled chapter failing (rate limit, needs auth, ...) shouldn't fail the
            # estimate outright - just omit it.
            estimated_size = 0
    return {"label": _format_size(estimated_size) if estimated_size else None}


def _format_size(size_bytes: int) -> str:
    """Human-readable rendering of `RanobeLib.estimate_title_size()`'s byte estimate -
    display-only web-layer formatting, not part of the estimate itself (see
    ranobelib.sizing's own docstring for why the estimate is approximate)."""
    size = float(size_bytes)
    for unit in ("Б", "КБ", "МБ"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"
