"""GET / — the search/open-title landing page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.recent_titles import read_recent
from app.templating import templates

router = APIRouter()


@router.get("/")
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {"active_nav": "home", "recent": read_recent(request)},
    )
