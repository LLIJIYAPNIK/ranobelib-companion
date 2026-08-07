"""Online chapter reading."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from ranobelib.models import Volume

from app.services.client import get_client
from app.templating import templates

router = APIRouter(prefix="/titles/{slug_url}/chapters")


@router.get("/{volume}/{number}")
async def read_chapter(
    request: Request, slug_url: str, volume: int, number: str
) -> HTMLResponse:
    async with get_client(slug_url) as lib:
        chapter = await lib.get_chapter(volume, number)
        volumes = await lib.get_table_of_contents()
    prev_url, next_url = _adjacent_chapter_urls(slug_url, volumes, str(volume), number)
    return templates.TemplateResponse(
        request,
        "chapter.html",
        {
            "slug_url": slug_url,
            "chapter": chapter,
            "prev_url": prev_url,
            "next_url": next_url,
        },
    )


def _adjacent_chapter_urls(
    slug_url: str, volumes: list[Volume], volume: str, number: str
) -> tuple[str | None, str | None]:
    """Previous/next chapter URLs across the whole title, in the SDK's own chapter order."""
    flat = [(vol.number, chapter.number) for vol in volumes for chapter in vol.chapters]
    try:
        index = flat.index((volume, number))
    except ValueError:
        return None, None
    prev_url = _chapter_url(slug_url, flat[index - 1]) if index > 0 else None
    next_url = _chapter_url(slug_url, flat[index + 1]) if index < len(flat) - 1 else None
    return prev_url, next_url


def _chapter_url(slug_url: str, key: tuple[str, str]) -> str:
    volume, number = key
    return f"/titles/{slug_url}/chapters/{volume}/{number}"
