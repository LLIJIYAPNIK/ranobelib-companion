"""GET /settings/* — settings pages, split into left-nav tabs (PR 89)."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.avatar import AvatarUploadError, save_avatar
from app.auth.dependencies import get_current_user, require_current_user
from app.auth.passwords import PasswordTooLongError, hash_password, verify_password
from app.db.connection import get_connection
from app.db.users import (
    User,
    get_user_by_email,
    update_user_account,
    update_user_avatar,
    update_user_password,
)
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


@router.post("/settings/account/avatar", response_model=None)
async def upload_avatar(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    avatar: Annotated[UploadFile, File(...)],
) -> HTMLResponse:
    try:
        avatar_path = await save_avatar(avatar, user.id)
    except AvatarUploadError as exc:
        return templates.TemplateResponse(
            request,
            "settings_account.html",
            {
                "active_nav": "settings",
                "active_settings_section": "account",
                "nickname": user.nickname,
                "email": user.email,
                "bio": user.bio,
                "avatar_error": str(exc),
            },
            status_code=400,
        )

    update_user_avatar(get_connection(), user.id, avatar_path)
    return templates.TemplateResponse(
        request,
        "settings_account.html",
        {
            "active_nav": "settings",
            "active_settings_section": "account",
            "nickname": user.nickname,
            "email": user.email,
            "bio": user.bio,
            "avatar_saved": True,
        },
    )


@router.get("/settings/security")
async def settings_security_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings_security.html",
        {"active_nav": "settings", "active_settings_section": "security"},
    )


@router.post("/settings/security", response_model=None)
async def update_password(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
) -> HTMLResponse:
    error: str | None = None
    if not verify_password(current_password, user.password_hash):
        error = "Неверный текущий пароль"
    elif new_password != new_password_confirm:
        error = "Пароли не совпадают"
    else:
        try:
            new_password_hash = hash_password(new_password)
        except PasswordTooLongError:
            error = "Пароль слишком длинный"

    if error is not None:
        return templates.TemplateResponse(
            request,
            "settings_security.html",
            {"active_nav": "settings", "active_settings_section": "security", "error": error},
            status_code=400,
        )

    update_user_password(get_connection(), user.id, new_password_hash)
    return templates.TemplateResponse(
        request,
        "settings_security.html",
        {"active_nav": "settings", "active_settings_section": "security", "saved": True},
    )
