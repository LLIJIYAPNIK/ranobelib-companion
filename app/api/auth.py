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
from app.db.email_verification import check_code as check_verification_code
from app.db.email_verification import create_token as create_verification_token
from app.db.email_verification import get_valid_token as get_valid_verification_token
from app.db.email_verification import mark_token_used as mark_verification_token_used
from app.db.password_reset import create_token, get_valid_token, mark_token_used
from app.db.users import (
    User,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_nickname,
    mark_email_verified,
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
    # PR 246: not logged in yet - the account waits in the session until its email is
    # confirmed (see the email confirmation section below). Any older login in this
    # browser is dropped rather than left running alongside a different, pending account.
    await _send_verification_email(request, conn, user)
    request.session.pop("user_id", None)
    request.session.pop("session_version", None)
    request.session[PENDING_VERIFICATION_KEY] = user.id
    # A redirect for both the no-JS form and auth-modal.js (PR 218), which follows a
    # register redirect as a full navigation.
    return RedirectResponse(url="/verify-email", status_code=303)


@router.get("/register/avatar")
async def show_register_avatar(
    request: Request, user: Annotated[User, Depends(require_current_user)]
) -> HTMLResponse:
    """PR 106's optional avatar step. Since PR 246 it follows email confirmation (see
    _complete_verification()), the first moment a new account is logged in. A real GET,
    so reloading or bookmarking it still works like any other page."""
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


# --- PR 246: email confirmation -----------------------------------------------------------
#
# A new account can't log in until it confirms its email. Between registering (or logging
# in with the right password but an unconfirmed email) and confirming, the session holds
# only PENDING_VERIFICATION_KEY - never user_id - so nothing else on the site treats the
# visitor as logged in. Confirming either way (the link or the code from the same email)
# swaps that for a real session via _log_in().

PENDING_VERIFICATION_KEY = "pending_verification_user_id"

_INVALID_CODE_MESSAGE = "Неверный или устаревший код. Проверьте письмо или отправьте новое."


def _log_in(request: Request, user: User) -> None:
    request.session.pop(PENDING_VERIFICATION_KEY, None)
    request.session["user_id"] = user.id
    # PR 225: stashed alongside user_id and checked on every request (see
    # get_current_user(), app/auth/dependencies.py) against the account's current
    # session_version - lets a password reset invalidate sessions issued before it.
    request.session["session_version"] = user.session_version
    # `current_user` (see app/templating.py's context processor) is resolved once up front
    # by an app-level dependency (app/main.py), before this session write - refresh it so
    # whatever renders next already sees the visitor as logged in.
    request.state.current_user = user


async def _send_verification_email(request: Request, conn: AsyncConnection, user: User) -> None:
    ttl_seconds = get_settings().email_verification_token_ttl
    issued = await create_verification_token(conn, user.id, ttl_seconds)
    verify_url = str(request.url_for("verify_email_link", token=issued.token))
    ttl_hours = max(1, int(ttl_seconds // 3600))
    await send_email(
        user.email,
        "Подтверждение email — webnovells",
        f"Чтобы подтвердить email и войти, перейдите по ссылке:\n{verify_url}\n\n"
        f"Или введите код на странице подтверждения: {issued.code}\n\n"
        f"Ссылка и код действуют {ttl_hours} ч. Если вы не регистрировались на "
        "webnovells, просто проигнорируйте это письмо.",
    )


async def _pending_user(request: Request, conn: AsyncConnection) -> User | None:
    """The unconfirmed account waiting in this session, if any. An account confirmed
    since (e.g. by the link on another device) no longer counts as pending."""
    user_id = request.session.get(PENDING_VERIFICATION_KEY)
    if user_id is None:
        return None
    user = await get_user_by_id(conn, user_id)
    if user is None or user.is_email_verified:
        request.session.pop(PENDING_VERIFICATION_KEY, None)
        return None
    return user


def _verify_email_page(
    request: Request, user: User, *, status_code: int = 200, **context: object
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "verify_email.html",
        {"pending_email": user.email, **context},
        status_code=status_code,
    )


async def _complete_verification(
    request: Request, conn: AsyncConnection, token_id: int, user_id: int
) -> Response:
    await mark_verification_token_used(conn, token_id)
    user = await mark_email_verified(conn, user_id)
    _log_in(request, user)
    # PR 106's optional avatar step still follows registration - it just starts from here
    # now, the first moment the account is actually logged in.
    return RedirectResponse(url="/register/avatar", status_code=303)


@router.get("/verify-email", response_model=None)
async def show_verify_email(
    request: Request, conn: Annotated[AsyncConnection, Depends(get_connection)]
) -> Response:
    user = await _pending_user(request, conn)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return _verify_email_page(request, user)


@router.post("/verify-email", response_model=None)
async def verify_email_code(
    request: Request,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    code: str = Form(...),
) -> Response:
    user = await _pending_user(request, conn)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    # Per-IP on top of check_code()'s own per-email cap (see app/db/email_verification.py).
    if is_rate_limited(f"verify-email:{_client_ip(request)}"):
        return _verify_email_page(request, user, status_code=429, error=_RATE_LIMIT_MESSAGE)

    token = await check_verification_code(conn, user.id, code)
    if token is None:
        return _verify_email_page(request, user, status_code=400, error=_INVALID_CODE_MESSAGE)
    return await _complete_verification(request, conn, token.id, user.id)


@router.post("/verify-email/resend", response_model=None)
async def resend_verification_email(
    request: Request, conn: Annotated[AsyncConnection, Depends(get_connection)]
) -> Response:
    user = await _pending_user(request, conn)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    # Same limiter and bucket as POST /register (PR 188) - a resend is one more
    # "send this address an email" attempt, like the registration that sent the first.
    if is_rate_limited(f"register:{_client_ip(request)}:{user.email}"):
        return _verify_email_page(request, user, status_code=429, error=_RATE_LIMIT_MESSAGE)

    await _send_verification_email(request, conn, user)
    return _verify_email_page(request, user, message="Письмо отправлено ещё раз")


@router.get("/verify-email/{token}", response_model=None)
async def verify_email_link(
    request: Request,
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    token: str,
) -> Response:
    verification = await get_valid_verification_token(conn, token)
    if verification is None:
        pending = await _pending_user(request, conn)
        return templates.TemplateResponse(
            request,
            "verify_email.html",
            {"invalid": True, "pending_email": pending.email if pending else None},
            status_code=400,
        )
    return await _complete_verification(request, conn, verification.id, verification.user_id)


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

    if not user.is_email_verified:
        # PR 246: only reached with the right password, so this reveals nothing about
        # which emails are registered. No new email is sent automatically - the page
        # offers a resend instead, so repeated logins don't flood the inbox.
        request.session.pop("user_id", None)
        request.session.pop("session_version", None)
        request.session[PENDING_VERIFICATION_KEY] = user.id
        return RedirectResponse(url="/verify-email", status_code=303)

    _log_in(request, user)
    if remember_me:
        # PR 36: extends the session cookie's lifetime - see
        # app/auth/session_middleware.py, RememberMeSessionMiddleware.
        request.session[REMEMBER_ME_KEY] = True
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> Response:
    request.session.clear()
    response = RedirectResponse(url="/", status_code=303)
    # PR 333: the browser's HTTP cache may still hold this account's pages (back/forward
    # included) - dropped, so the next person on the device can't page back into them.
    # Only "cache": "storage" would also take the downloads the visitor chose to keep
    # (device-account.js clears the personal part of storage itself).
    response.headers["Clear-Site-Data"] = '"cache"'
    return response


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
    # PR 246: the reset link was emailed to this address, so using it proves the same
    # thing a confirmation link does - without this, an unconfirmed account that resets
    # its password would still be stuck at /verify-email on the next login.
    await mark_email_verified(conn, reset_token.user_id)
    return templates.TemplateResponse(request, "password_reset.html", {"success": True})
