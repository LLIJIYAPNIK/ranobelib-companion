"""Online chapter reading."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from ranobelib.models import Volume

from app.auth.dependencies import get_current_user
from app.db.activity import record_chapter_read
from app.db.connection import get_connection
from app.db.library import add_entry, record_progress
from app.db.users import User
from app.services.client import open_client
from app.services.exports import available_export_formats
from app.templating import templates

router = APIRouter(prefix="/titles/{slug_url}/chapters")


@router.get("/{volume}/{number}")
async def read_chapter(
    request: Request,
    slug_url: str,
    volume: int,
    number: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
    branch_id: int | None = Query(default=None),
) -> HTMLResponse:
    async with open_client(slug_url) as lib:
        chapter = await lib.get_chapter(volume, number, branch_id=branch_id)
        volumes = await lib.get_table_of_contents()
    if current_user is not None:
        # PR 35: opening any chapter adds the title to the library if it isn't there
        # yet, same as clicking "Добавить в библиотеку" - add_entry() is idempotent
        # (INSERT OR IGNORE), so repeat opens don't duplicate the entry or touch its
        # added_at (see app/db/library.py).
        add_entry(get_connection(), current_user.id, slug_url)
        record_progress(get_connection(), current_user.id, slug_url, str(volume), number)
        # Unlike record_progress, this always writes - it's an activity feed entry, not a
        # library-membership check (see app/db/activity.py).
        record_chapter_read(get_connection(), current_user.id, slug_url, str(volume), number)
    prev_url, next_url = _adjacent_chapter_urls(slug_url, volumes, str(volume), number)
    return templates.TemplateResponse(
        request,
        "chapter.html",
        {
            "slug_url": slug_url,
            "chapter": chapter,
            "prev_url": prev_url,
            "next_url": next_url,
            "branch_id": branch_id,
            "export_formats": available_export_formats(),
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
