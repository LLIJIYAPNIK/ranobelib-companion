"""GET /profile — read-only account profile (PR 92).

Separate from /settings/account (PR 90), which is where these same fields are actually
edited - this page only displays them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth.dependencies import get_current_user
from app.db.users import User
from app.templating import templates

router = APIRouter()


@router.get("/profile")
async def profile_page(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Same locked-screen gate as /library, /downloads, /activity and /settings/*
    (PR 22/90/91) - there's nothing to show without an account."""
    registered_at = _format_date(user.created_at) if user is not None else None
    return templates.TemplateResponse(
        request,
        "profile.html",
        {"registered_at": registered_at},
    )


def _format_date(iso_timestamp: str) -> str:
    return datetime.fromisoformat(iso_timestamp).strftime("%d.%m.%Y")
