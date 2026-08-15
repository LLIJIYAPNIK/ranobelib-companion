"""Online chapter reading."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from ranobelib.models import Volume

from app.auth.dependencies import get_current_user, require_current_user
from app.db.activity import record_chapter_read
from app.db.connection import get_connection
from app.db.library import add_entry, record_progress
from app.db.reactions import (
    ALLOWED_EMOJI,
    count_reactions,
    count_reactions_for_paragraph,
    toggle_reaction,
    user_reactions,
)
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


@router.get("/{volume}/{number}/reactions")
async def get_reactions(
    slug_url: str,
    volume: int,
    number: str,
    current_user: Annotated[User | None, Depends(get_current_user)],
    branch_id: str = Query(default=""),
) -> JSONResponse:
    """Aggregated reaction counts for every paragraph in this chapter, plus - for a
    logged-in visitor - which single emoji is their own current pick per paragraph, so
    the picker in paragraph-menu.js can highlight it. Public (no login required) the same
    way the chapter's own text is: reading who reacted what needs no account, only adding
    a reaction does (see post_reaction below)."""
    conn = get_connection()
    counts = count_reactions(conn, slug_url, str(volume), number, branch_id)
    mine = (
        user_reactions(conn, current_user.id, slug_url, str(volume), number, branch_id)
        if current_user is not None
        else {}
    )
    return JSONResponse(
        {
            "counts": {str(index): counts[index] for index in counts},
            "mine": {str(index): emoji for index, emoji in mine.items()},
        }
    )


@router.post("/{volume}/{number}/reactions")
async def post_reaction(
    slug_url: str,
    volume: int,
    number: str,
    user: Annotated[User, Depends(require_current_user)],
    paragraph_index: Annotated[int, Form()],
    emoji: Annotated[str, Form()],
    branch_id: Annotated[str, Form()] = "",
) -> JSONResponse:
    """Toggles the logged-in visitor's reaction on one paragraph - the only bit of this
    feature that needs a session (see get_reactions above), hence require_current_user
    rather than the optional get_current_user."""
    if emoji not in ALLOWED_EMOJI:
        raise HTTPException(status_code=400, detail="Недопустимая реакция")
    conn = get_connection()
    mine = toggle_reaction(
        conn, user.id, slug_url, str(volume), number, branch_id, paragraph_index, emoji
    )
    counts = count_reactions_for_paragraph(
        conn, slug_url, str(volume), number, branch_id, paragraph_index
    )
    return JSONResponse({"paragraph_index": paragraph_index, "counts": counts, "mine": mine})


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
