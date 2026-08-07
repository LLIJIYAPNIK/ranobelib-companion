"""Title lookup: resolve a pasted link/slug, fetch metadata, render the title page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.recent_titles import remember
from app.services.client import get_client
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
async def show_title(request: Request, slug_url: str) -> HTMLResponse:
    async with get_client(slug_url) as lib:
        title = await lib.get_info()
        volumes = await lib.get_table_of_contents()
    cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
    response = templates.TemplateResponse(
        request,
        "title.html",
        {
            "title": title,
            "cover_url": cover_url,
            "volumes": volumes,
            "export_formats": available_export_formats(),
        },
    )
    remember(response, request, slug_url=title.slug_url, name=title.name)
    return response
