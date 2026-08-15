"""GET /profile — read-only account profile (PR 92), now a public page (PR 122).

Separate from /settings/account (PR 90), which is where these same fields are actually
edited - this page only displays them.

/profile (no id) is the logged-in visitor's own shortcut - it stays gated behind login,
since there's no "own profile" to show an anonymous visitor. /profile/{user_id} is the
public page itself: any visitor, logged in or not, can view any registered user's
profile by id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.library import library_items_for_user
from app.auth.dependencies import get_current_user
from app.db.connection import get_connection
from app.db.users import User, get_user_by_id
from app.templating import templates

router = APIRouter()


@router.get("/profile")
async def own_profile_page(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Same locked-screen gate as /library, /downloads, /activity and /settings/*
    (PR 22/90/91) - there's nothing to redirect an anonymous visitor's *own* profile to."""
    if user is None:
        return templates.TemplateResponse(request, "profile.html", {"profile_user": None})
    return await _render_profile(request, profile_user=user, is_own_profile=True)


@router.get("/profile/{user_id}")
async def public_profile_page(
    request: Request,
    user_id: int,
    viewer: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    profile_user = get_user_by_id(get_connection(), user_id)
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    is_own_profile = viewer is not None and viewer.id == profile_user.id
    return await _render_profile(request, profile_user=profile_user, is_own_profile=is_own_profile)


async def _render_profile(
    request: Request, *, profile_user: User, is_own_profile: bool
) -> HTMLResponse:
    items = await library_items_for_user(profile_user)
    # Most recently read first (see library_items_for_user/list_entries's own ordering) -
    # but the top entry might just be the most recently *added*, never actually opened,
    # so "Читает сейчас" only shows up once that entry genuinely has a read position.
    currently_reading = items[0] if items and items[0]["entry"].last_read_at else None
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "profile_user": profile_user,
            "is_own_profile": is_own_profile,
            "registered_at": _format_date(profile_user.created_at),
            "currently_reading": currently_reading,
        },
    )


def _format_date(iso_timestamp: str) -> str:
    return datetime.fromisoformat(iso_timestamp).strftime("%d.%m.%Y")
