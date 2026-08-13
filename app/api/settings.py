"""GET /settings/* — settings pages, split into left-nav tabs (PR 89)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.dependencies import get_current_user, require_current_user
from app.db.connection import get_connection
from app.db.users import User, get_user_by_email, update_user_account
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


@router.get("/settings/account")
async def settings_account_page(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Viewing the page doesn't require an account - same locked-screen gate as
    /library/downloads/activity (PR 22) - there's just nothing to edit without one, which
    settings_account.html shows instead of the form."""
    return templates.TemplateResponse(
        request,
        "settings_account.html",
        {
            "active_nav": "settings",
            "active_settings_section": "account",
            "nickname": user.nickname if user else None,
            "email": user.email if user else None,
            "bio": user.bio if user else None,
        },
    )


@router.post("/settings/account", response_model=None)
async def update_account(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    email: str = Form(...),
    nickname: str = Form(default=""),
    bio: str = Form(default=""),
) -> HTMLResponse:
    conn = get_connection()
    existing = get_user_by_email(conn, email)
    if existing is not None and existing.id != user.id:
        return templates.TemplateResponse(
            request,
            "settings_account.html",
            {
                "active_nav": "settings",
                "active_settings_section": "account",
                "nickname": nickname,
                "email": email,
                "bio": bio,
                "error": "Этот email уже используется другим аккаунтом",
            },
            status_code=400,
        )

    updated = update_user_account(
        conn, user.id, email=email, nickname=nickname.strip() or None, bio=bio.strip() or None
    )
    return templates.TemplateResponse(
        request,
        "settings_account.html",
        {
            "active_nav": "settings",
            "active_settings_section": "account",
            "nickname": updated.nickname,
            "email": updated.email,
            "bio": updated.bio,
            "saved": True,
        },
    )


@router.get("/settings/security")
async def settings_security_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings_security.html",
        {"active_nav": "settings", "active_settings_section": "security"},
    )
