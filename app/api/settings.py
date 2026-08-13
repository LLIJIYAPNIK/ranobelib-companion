"""GET /settings/* — settings pages, split into left-nav tabs (PR 89)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.templating import templates

router = APIRouter()


@router.get("/settings")
async def settings_page() -> RedirectResponse:
    """No content of its own - just points at the default tab."""
    return RedirectResponse(url="/settings/reading", status_code=302)


@router.get("/settings/reading")
async def settings_reading_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings_reading.html",
        {"active_nav": "settings", "active_settings_section": "reading"},
    )
