"""Application-level accounts: registration, login, logout.

Unrelated to ranobelib.me - this is our own email+password login, not an integration
with a ranobelib.me account (see CLAUDE.md, "Обязательные решения из ТЗ", "Авторизация").
"""

from __future__ import annotations

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg import AsyncConnection

from app.auth.avatar import AvatarUploadError, save_avatar
from app.auth.dependencies import require_current_user
from app.auth.passwords import (
    PasswordTooLongError,
    PasswordTooWeakError,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.auth.rate_limit import is_rate_limited
from app.auth.session_middleware import REMEMBER_ME_KEY
from app.db.connection import get_connection
from app.db.users import (
    User,
    create_user,
    get_user_by_email,
    get_user_by_nickname,
    update_user_avatar,
)
from app.templating import templates

router = APIRouter()

_RATE_LIMIT_MESSAGE = "Слишком много попыток, попробуйте позже"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


@router.get("/register")
async def show_register(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register", response_model=None)
async def register(
    request: Request,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    nickname: str = Form(default=""),
) -> Response:
    if is_rate_limited(f"register:{_client_ip(request)}:{email}"):
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "error": _RATE_LIMIT_MESSAGE,
                "submitted_email": email,
                "submitted_nickname": nickname,
            },
            status_code=429,
        )

    nickname_clean = nickname.strip() or None

    error: str | None = None
    if password != password_confirm:
        error = "Пароли не совпадают"
    elif await get_user_by_email(conn, email) is not None:
        error = "Этот email уже зарегистрирован"
    elif (
        nickname_clean is not None and await get_user_by_nickname(conn, nickname_clean) is not None
    ):
        error = "Этот никнейм уже занят"
    else:
        try:
            validate_password_strength(password, email)
            password_hash = hash_password(password)
        except PasswordTooWeakError:
            error = "Пароль слишком простой или короткий (минимум 8 символов)"
        except PasswordTooLongError:
            error = "Пароль слишком длинный"

    if error is not None:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": error, "submitted_email": email, "submitted_nickname": nickname},
            status_code=400,
        )

    try:
        user = await create_user(conn, email, password_hash, nickname_clean)
    except psycopg.errors.UniqueViolation as exc:
        # Race-safe backstop behind the pre-check above (see migrations/
        # 0017_users_nickname_unique.sql and create_user()'s own docstring) - two
        # registrations for the same nickname landing concurrently.
        if exc.diag.constraint_name == "users_nickname_lower_unique":
            return templates.TemplateResponse(
                request,
                "register.html",
                {
                    "error": "Этот никнейм уже занят",
                    "submitted_email": email,
                    "submitted_nickname": nickname,
                },
                status_code=400,
            )
        raise
    request.session["user_id"] = user.id
    # PR 106: one more screen before home, offering an avatar upload. `current_user` (see
    # app/templating.py's context processor) is resolved once up front by an app-level
    # dependency (app/main.py), before this route body - and therefore this session write
    # - ever runs, so it has to be refreshed explicitly here too for the sidebar to reflect
    # the new account immediately rather than on the next request.
    request.state.current_user = user
    return templates.TemplateResponse(request, "register_avatar.html", {})


@router.post("/register/avatar", response_model=None)
async def register_avatar(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    avatar: Annotated[UploadFile, File(...)],
) -> Response:
    """Same save_avatar/update_user_avatar pair as /settings/account/avatar (PR 96) - just
    a different destination on success, since this is a one-shot step in the registration
    flow rather than a settings form the visitor stays on."""
    try:
        avatar_path = await save_avatar(avatar, user.id)
    except AvatarUploadError as exc:
        return templates.TemplateResponse(
            request, "register_avatar.html", {"avatar_error": str(exc)}, status_code=400
        )

    await update_user_avatar(conn, user.id, avatar_path)
    return RedirectResponse(url="/", status_code=303)


@router.get("/login")
async def show_login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login", response_model=None)
async def login(
    request: Request,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    email: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(default=False),
) -> Response:
    if is_rate_limited(f"login:{_client_ip(request)}:{email}"):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": _RATE_LIMIT_MESSAGE, "submitted_email": email},
            status_code=429,
        )

    user = await get_user_by_email(conn, email)
    # Same message either way - not confirming/denying whether an email is registered.
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный email или пароль", "submitted_email": email},
            status_code=400,
        )

    request.session["user_id"] = user.id
    if remember_me:
        # PR 36: extends the session cookie's lifetime - see
        # app/auth/session_middleware.py, RememberMeSessionMiddleware.
        request.session[REMEMBER_ME_KEY] = True
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)
