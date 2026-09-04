"""GET /settings/* — settings pages, split into left-nav tabs (PR 89)."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg import AsyncConnection

from app.auth.avatar import AvatarUploadError, save_avatar
from app.auth.dependencies import get_current_user, require_current_user
from app.auth.passwords import (
    PasswordTooLongError,
    PasswordTooWeakError,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.db.connection import get_connection
from app.db.users import (
    User,
    get_user_by_email,
    get_user_by_nickname,
    update_notification_settings,
    update_privacy_settings,
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


def _account_context(user: User, **extra: object) -> dict[str, object]:
    """Shared base for every settings_account.html render below - the "Приватность" form
    (PR 124) needs the three show_* flags on every one of them, not just its own POST
    handler, since a save from the account-fields or avatar form re-renders this same
    template and its checkboxes have to reflect the visitor's actual saved state, not
    default back to "show everything"."""
    return {
        "active_nav": "settings",
        "active_settings_section": "account",
        "nickname": user.nickname,
        "email": user.email,
        "bio": user.bio,
        "show_currently_reading": user.show_currently_reading,
        "show_favorite": user.show_favorite,
        "show_library": user.show_library,
        **extra,
    }


@router.get("/settings/account")
async def settings_account_page(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Viewing the page doesn't require an account - same locked-screen gate as
    /library/downloads/activity (PR 22) - there's just nothing to edit without one, which
    settings_account.html shows instead of the form."""
    context = (
        _account_context(user)
        if user is not None
        else {"active_nav": "settings", "active_settings_section": "account"}
    )
    return templates.TemplateResponse(request, "settings_account.html", context)


@router.post("/settings/account", response_model=None)
async def update_account(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    email: str = Form(...),
    nickname: str = Form(default=""),
    bio: str = Form(default=""),
) -> HTMLResponse:
    nickname_clean = nickname.strip() or None

    existing_email = await get_user_by_email(conn, email)
    existing_nickname = (
        await get_user_by_nickname(conn, nickname_clean) if nickname_clean is not None else None
    )
    error: str | None = None
    if existing_email is not None and existing_email.id != user.id:
        error = "Этот email уже используется другим аккаунтом"
    elif existing_nickname is not None and existing_nickname.id != user.id:
        error = "Этот никнейм уже занят"

    if error is not None:
        context = _account_context(user, nickname=nickname, email=email, bio=bio, error=error)
        return templates.TemplateResponse(
            request, "settings_account.html", context, status_code=400
        )

    try:
        updated = await update_user_account(
            conn, user.id, email=email, nickname=nickname_clean, bio=bio.strip() or None
        )
    except psycopg.errors.UniqueViolation as exc:
        # Race-safe backstop behind the pre-check above - see create_user()'s docstring
        # (app/db/users.py) for why the pre-check alone can't be trusted.
        if exc.diag.constraint_name == "users_nickname_lower_unique":
            context = _account_context(
                user,
                nickname=nickname,
                email=email,
                bio=bio,
                error="Этот никнейм уже занят",
            )
            return templates.TemplateResponse(
                request, "settings_account.html", context, status_code=400
            )
        raise
    return templates.TemplateResponse(
        request, "settings_account.html", _account_context(updated, saved=True)
    )


@router.post("/settings/account/avatar", response_model=None)
async def upload_avatar(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    avatar: Annotated[UploadFile, File(...)],
) -> HTMLResponse:
    try:
        avatar_path = await save_avatar(avatar, user.id)
    except AvatarUploadError as exc:
        context = _account_context(user, avatar_error=str(exc))
        return templates.TemplateResponse(
            request, "settings_account.html", context, status_code=400
        )

    await update_user_avatar(conn, user.id, avatar_path)
    return templates.TemplateResponse(
        request, "settings_account.html", _account_context(user, avatar_saved=True)
    )


@router.post("/settings/account/privacy", response_model=None)
async def update_privacy(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    show_currently_reading: bool = Form(default=False),
    show_favorite: bool = Form(default=False),
    show_library: bool = Form(default=False),
) -> HTMLResponse:
    """Unchecked checkboxes simply aren't sent by the browser at all, so every submit of
    this form carries the visitor's complete intended state for all three - no partial
    update, matching update_privacy_settings()'s own "always write all three" shape."""
    updated = await update_privacy_settings(
        conn,
        user.id,
        show_currently_reading=show_currently_reading,
        show_favorite=show_favorite,
        show_library=show_library,
    )
    return templates.TemplateResponse(
        request, "settings_account.html", _account_context(updated, privacy_saved=True)
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
    conn: Annotated[AsyncConnection, Depends(get_connection)],
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
            validate_password_strength(new_password, user.email)
            new_password_hash = hash_password(new_password)
        except PasswordTooWeakError:
            error = "Пароль слишком простой или короткий (минимум 8 символов)"
        except PasswordTooLongError:
            error = "Пароль слишком длинный"

    if error is not None:
        return templates.TemplateResponse(
            request,
            "settings_security.html",
            {"active_nav": "settings", "active_settings_section": "security", "error": error},
            status_code=400,
        )

    await update_user_password(conn, user.id, new_password_hash)
    return templates.TemplateResponse(
        request,
        "settings_security.html",
        {"active_nav": "settings", "active_settings_section": "security", "saved": True},
    )


def _notifications_context(user: User, **extra: object) -> dict[str, object]:
    return {
        "active_nav": "settings",
        "active_settings_section": "notifications",
        "notifications_enabled": user.notifications_enabled,
        "do_not_disturb": user.do_not_disturb,
        **extra,
    }


@router.get("/settings/notifications")
async def settings_notifications_page(
    request: Request, user: Annotated[User | None, Depends(get_current_user)]
) -> HTMLResponse:
    """Viewing doesn't require an account - same locked-screen gate as the other settings
    tabs above."""
    context = (
        _notifications_context(user)
        if user is not None
        else {"active_nav": "settings", "active_settings_section": "notifications"}
    )
    return templates.TemplateResponse(request, "settings_notifications.html", context)


@router.post("/settings/notifications", response_model=None)
async def update_notifications(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    notifications_enabled: bool = Form(default=False),
    do_not_disturb: bool = Form(default=False),
) -> HTMLResponse:
    """Unchecked checkboxes aren't sent by the browser at all, so every submit carries
    the visitor's complete intended state for both - same "always write all together"
    shape as update_privacy/update_account above."""
    updated = await update_notification_settings(
        conn,
        user.id,
        notifications_enabled=notifications_enabled,
        do_not_disturb=do_not_disturb,
    )
    return templates.TemplateResponse(
        request, "settings_notifications.html", _notifications_context(updated, saved=True)
    )
