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
from app.config import get_settings
from app.db.connection import get_connection
from app.db.password_reset import create_token, get_valid_token, mark_token_used
from app.db.users import (
    User,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_nickname,
    update_user_avatar,
    update_user_password_from_reset,
)
from app.email import send_email
from app.templating import templates

router = APIRouter()

_RATE_LIMIT_MESSAGE = "Слишком много попыток, попробуйте позже"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _is_modal_request(request: Request) -> bool:
    """True for a fetch() call from auth-modal.js (PR 218), false for an ordinary
    navigation/no-JS form submission - distinguishes "render just the _auth_card.html
    fragment the modal can swap in" from "render the full page", the same signal both the
    GET routes and the error branches of the POST routes below branch on."""
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _auth_template(request: Request, full_page_template: str) -> str:
    return "_auth_card.html" if _is_modal_request(request) else full_page_template


@router.get("/register")
async def show_register(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        _auth_template(request, "register.html"),
        {"mode": "register", "active_tab": "register"},
    )


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
            _auth_template(request, "register.html"),
            {
                "mode": "register",
                "active_tab": "register",
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
            _auth_template(request, "register.html"),
            {
                "mode": "register",
                "active_tab": "register",
                "error": error,
                "submitted_email": email,
                "submitted_nickname": nickname,
            },
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
                _auth_template(request, "register.html"),
                {
                    "mode": "register",
                    "active_tab": "register",
                    "error": "Этот никнейм уже занят",
                    "submitted_email": email,
                    "submitted_nickname": nickname,
                },
                status_code=400,
            )
        raise
    request.session["user_id"] = user.id
    # PR 225: stashed alongside user_id and checked on every request (see
    # get_current_user(), app/auth/dependencies.py) against the account's current
    # session_version - lets a password reset invalidate sessions issued before it.
    request.session["session_version"] = user.session_version
    # PR 106: one more screen before home, offering an avatar upload. `current_user` (see
    # app/templating.py's context processor) is resolved once up front by an app-level
    # dependency (app/main.py), before this route body - and therefore this session write
    # - ever runs, so it has to be refreshed explicitly here too for the sidebar to reflect
    # the new account immediately rather than on the next request.
    request.state.current_user = user
    if _is_modal_request(request):
        # PR 218: auth-modal.js only knows how to detect success via a redirect (see its
        # own comment on why - unlike the login form, this success case has no error/OK
        # fragment distinction to sniff). A plain, no-JS submission keeps rendering the
        # avatar prompt directly instead (the branch below, unchanged since PR 106) - only
        # the modal's own fetch() needs somewhere to redirect *to*, hence GET
        # /register/avatar just below existing solely to give this a target.
        return RedirectResponse(url="/register/avatar", status_code=303)
    return templates.TemplateResponse(request, "register_avatar.html", {})


@router.get("/register/avatar")
async def show_register_avatar(
    request: Request, user: Annotated[User, Depends(require_current_user)]
) -> HTMLResponse:
    """Only reachable today via the redirect above (a modal-driven registration) - a
    plain, no-JS registration renders register_avatar.html directly from POST /register
    instead and never hits this route. Exists as a real GET regardless (not, say, folded
    into the redirect target as a query string) so reloading it or bookmarking it still
    works like any other page."""
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
    return templates.TemplateResponse(
        request,
        _auth_template(request, "login.html"),
        {"mode": "login", "active_tab": "login"},
    )


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
            _auth_template(request, "login.html"),
            {
                "mode": "login",
                "active_tab": "login",
                "error": _RATE_LIMIT_MESSAGE,
                "submitted_email": email,
            },
            status_code=429,
        )

    user = await get_user_by_email(conn, email)
    # Same message either way - not confirming/denying whether an email is registered.
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            _auth_template(request, "login.html"),
            {
                "mode": "login",
                "active_tab": "login",
                "error": "Неверный email или пароль",
                "submitted_email": email,
            },
            status_code=400,
        )

    request.session["user_id"] = user.id
    # PR 225: see the matching comment in register() above.
    request.session["session_version"] = user.session_version
    if remember_me:
        # PR 36: extends the session cookie's lifetime - see
        # app/auth/session_middleware.py, RememberMeSessionMiddleware.
        request.session[REMEMBER_ME_KEY] = True
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


# --- PR 225: "Забыли пароль?" ------------------------------------------------------------

_PASSWORD_RESET_SENT_MESSAGE = (
    "Если такой email зарегистрирован, на него отправлена ссылка для восстановления пароля"
)


@router.get("/password-reset")
async def show_password_reset_request(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "password_reset_request.html", {})


@router.post("/password-reset", response_model=None)
async def request_password_reset(
    request: Request,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    email: str = Form(...),
) -> Response:
    if is_rate_limited(f"password-reset:{_client_ip(request)}:{email}"):
        return templates.TemplateResponse(
            request,
            "password_reset_request.html",
            {"error": _RATE_LIMIT_MESSAGE, "submitted_email": email},
            status_code=429,
        )

    # Always the same response whether or not the email is registered - same protection
    # against account enumeration already applied to POST /login's error message.
    user = await get_user_by_email(conn, email)
    if user is not None:
        raw_token = await create_token(conn, user.id, get_settings().password_reset_token_ttl)
        reset_url = str(request.url_for("show_password_reset_confirm", token=raw_token))
        ttl_hours = max(1, int(get_settings().password_reset_token_ttl // 3600))
        await send_email(
            user.email,
            "Восстановление пароля — webnovells",
            f"Чтобы задать новый пароль, перейдите по ссылке:\n{reset_url}\n\n"
            f"Ссылка действует {ttl_hours} ч. Если вы не запрашивали восстановление "
            "пароля, просто проигнорируйте это письмо.",
        )

    return templates.TemplateResponse(
        request,
        "password_reset_request.html",
        {"message": _PASSWORD_RESET_SENT_MESSAGE, "submitted_email": email},
    )


@router.get("/password-reset/{token}")
async def show_password_reset_confirm(
    request: Request,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    token: str,
) -> HTMLResponse:
    reset_token = await get_valid_token(conn, token)
    if reset_token is None:
        return templates.TemplateResponse(
            request, "password_reset.html", {"invalid": True}, status_code=400
        )
    return templates.TemplateResponse(request, "password_reset.html", {"token": token})


@router.post("/password-reset/{token}", response_model=None)
async def confirm_password_reset(
    request: Request,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    token: str,
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
) -> Response:
    # Re-checked here, not just trusted from the GET above - the token could have been
    # consumed (or could have expired) by a concurrent request in between.
    reset_token = await get_valid_token(conn, token)
    if reset_token is None:
        return templates.TemplateResponse(
            request, "password_reset.html", {"invalid": True}, status_code=400
        )

    error: str | None = None
    new_password_hash = ""
    if new_password != new_password_confirm:
        error = "Пароли не совпадают"
    else:
        user = await get_user_by_id(conn, reset_token.user_id)
        assert user is not None  # the token's own FK guarantees this
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
            "password_reset.html",
            {"token": token, "error": error},
            status_code=400,
        )

    await update_user_password_from_reset(conn, reset_token.user_id, new_password_hash)
    await mark_token_used(conn, reset_token.id)
    return templates.TemplateResponse(request, "password_reset.html", {"success": True})
