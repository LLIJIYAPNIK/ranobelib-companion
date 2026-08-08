"""Personal library: add/remove a title, list what's in it (the "Читаю" tab)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse

from app.auth.dependencies import require_current_user
from app.db.connection import get_connection
from app.db.library import add_entry, remove_entry
from app.db.users import User
from app.services.client import open_client

router = APIRouter(prefix="/library")


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
