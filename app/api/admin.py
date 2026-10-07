"""/admin (PR 324): the panel's login, logout and landing page.

Every route sits behind ``require_admin_enabled`` (404 without ADMIN_PASSWORD), every page
but the login form behind ``require_admin`` (app/auth/admin.py). The overview and the
data browser come in PR 325; this PR only establishes who may see them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.auth.admin import (
    LOGIN_PATH,
    check_csrf,
    csrf_token,
    end_admin_session,
    is_admin,
    password_matches,
    require_admin,
    require_admin_enabled,
    start_admin_session,
)
from app.config import get_settings
from app.templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_enabled)])


@router.get("", response_model=None)
async def admin_home(request: Request, _: Annotated[None, Depends(require_admin)]) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "admin/index.html", {"csrf_token": csrf_token(request)}
    )


@router.get("/login", response_model=None)
async def admin_login_form(request: Request) -> Response:
    if is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return _login_page(request)


@router.post("/login", response_model=None)
async def admin_login(
    request: Request,
    password: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    check_csrf(request, csrf)
    if not password_matches(password):
        return _login_page(request, error="Неверный пароль", status_code=401)
    start_admin_session(request)
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout", response_model=None)
async def admin_logout(request: Request, csrf: Annotated[str, Form()] = "") -> Response:
    check_csrf(request, csrf)
    end_admin_session(request)
    return RedirectResponse(LOGIN_PATH, status_code=303)


def _login_page(
    request: Request, *, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    # The password is never put back into the form or the page.
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"csrf_token": csrf_token(request), "error": error},
        status_code=status_code,
    )


def install_admin_headers(app: FastAPI) -> None:
    """``Cache-Control: no-store`` and ``X-Robots-Tag: noindex`` on every /admin* response
    while the panel is enabled. Registered after install_html_no_cache() so this runs
    last on the way out and its Cache-Control wins. Without ADMIN_PASSWORD nothing is
    added - the 404 must look like any other unknown URL's."""

    @app.middleware("http")
    async def _admin_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if (path == "/admin" or path.startswith("/admin/")) and get_settings().admin_enabled:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response
