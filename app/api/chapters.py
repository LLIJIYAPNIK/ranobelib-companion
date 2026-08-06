"""Online chapter reading."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.chapter_html import sanitize_chapter_html
from app.services.client import get_client
from app.templating import templates

router = APIRouter(prefix="/titles/{slug_url}/chapters")


@router.get("/{volume}/{number}")
async def read_chapter(
    request: Request, slug_url: str, volume: int, number: str
) -> HTMLResponse:
    async with get_client(slug_url) as lib:
        chapter = await lib.get_chapter(volume, number)
    content_html = sanitize_chapter_html(chapter.content or "")
    return templates.TemplateResponse(
        request,
        "chapter.html",
        {"slug_url": slug_url, "chapter": chapter, "content_html": content_html},
    )
