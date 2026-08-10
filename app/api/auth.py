"""Application-level accounts: registration, login, logout.

Unrelated to ranobelib.me - this is our own email+password login, not an integration
with a ranobelib.me account (see CLAUDE.md, "Обязательные решения из ТЗ", "Авторизация").
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.auth.passwords import PasswordTooLongError, hash_password, verify_password
from app.auth.session_middleware import REMEMBER_ME_KEY
from app.db.connection import get_connection
from app.db.users import create_user, get_user_by_email
from app.templating import templates

router = APIRouter()


@router.get("/register")
async def show_register(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register", response_model=None)
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
) -> Response:
    conn = get_connection()
    error: str | None = None
    if password != password_confirm:
        error = "Пароли не совпадают"
    elif get_user_by_email(conn, email) is not None:
        error = "Этот email уже зарегистрирован"
    else:
        try:
            password_hash = hash_password(password)
        except PasswordTooLongError:
            error = "Пароль слишком длинный"

    if error is not None:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": error, "submitted_email": email},
            status_code=400,
        )

    user = create_user(conn, email, password_hash)
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@router.get("/login")
async def show_login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login", response_model=None)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(default=False),
) -> Response:
    user = get_user_by_email(get_connection(), email)
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
