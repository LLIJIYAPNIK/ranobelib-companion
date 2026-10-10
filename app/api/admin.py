"""/admin: the panel's login/logout (PR 324), overview and read-only data browser (PR 325),
the shared sidebar/top-bar shell (PR 341).

Every route sits behind ``require_admin_enabled`` (404 without ADMIN_PASSWORD), every page
but the login form behind ``require_admin`` (app/auth/admin.py). The data pages only read
(app/db/admin_data.py); changing data is a separate, later decision.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg import AsyncConnection
from psycopg.errors import QueryCanceled

from app.auth.admin import (
    LOGIN_PATH,
    check_csrf,
    csrf_token,
    end_admin_session,
    is_admin,
    login_locked_for,
    password_matches,
    record_failed_login,
    require_admin,
    require_admin_enabled,
    reset_failed_logins,
    start_admin_session,
)
from app.config import get_settings
from app.db.admin_data import (
    MASK,
    QUERY_TIMEOUT_MS,
    browse_table,
    list_tables,
    overview,
    table_columns,
)
from app.db.connection import get_connection
from app.templating import templates

# PR 326: out of the public /openapi.json and /docs - listing the paths there would
# announce the panel even with ADMIN_PASSWORD unset, when it must look like it isn't there.
router = APIRouter(
    prefix="/admin", dependencies=[Depends(require_admin_enabled)], include_in_schema=False
)


Admin = Annotated[None, Depends(require_admin)]
Connection = Annotated[AsyncConnection, Depends(get_connection)]


@dataclass(frozen=True)
class NavItem:
    """One sidebar entry (PR 341). ``href`` is None for a screen whose PR hasn't landed
    yet - shown, but not a link, so the sidebar already has its final shape."""

    section: str
    label: str
    icon: str
    href: str | None
    group: str = ""


# The sidebar's screens and groups (RanobeLib Admin.dc.html, cut down to what wave 40
# builds - see ROADMAP.md, PR 341). Each later PR fills in its href.
ADMIN_NAV: tuple[NavItem, ...] = (
    NavItem("overview", "Обзор", "dashboard", "/admin"),
    NavItem("analytics", "Аналитика", "chart", None, "Контент"),
    NavItem("users", "Пользователи", "users", None, "Сообщество"),
    NavItem("comments", "Комментарии", "comments", None, "Сообщество"),
    NavItem("tables", "Данные БД", "database", "/admin/tables", "Данные"),
    NavItem("system", "Система", "server", None, "Платформа"),
    NavItem("audit", "Журнал", "history", None, "Платформа"),
)


@router.get("", response_model=None)
async def admin_home(request: Request, _: Admin, conn: Connection) -> HTMLResponse:
    try:
        data = await overview(conn)
    except QueryCanceled:
        return _timed_out(request, "overview")
    return _page(request, "admin/index.html", "overview", overview=data)


@router.get("/tables", response_model=None)
async def admin_tables(request: Request, _: Admin, conn: Connection) -> HTMLResponse:
    try:
        tables = await list_tables(conn)
    except QueryCanceled:
        return _timed_out(request, "tables")
    return _page(request, "admin/tables.html", "tables", tables=tables)


@router.get("/tables/{name}", response_model=None)
async def admin_table(
    request: Request,
    name: str,
    _: Admin,
    conn: Connection,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    sort: str | None = None,
    order: str = "desc",
    q: Annotated[str, Query(max_length=200)] = "",
) -> HTMLResponse:
    columns = (await table_columns(conn)).get(name)
    if columns is None:
        raise HTTPException(status_code=404)
    try:
        data = await browse_table(
            conn, name, columns, page=page, sort=sort, descending=order != "asc", query=q
        )
    except QueryCanceled:
        return _timed_out(request, "tables")
    return _page(request, "admin/table.html", "tables", data=data, mask=MASK)


def _timed_out(request: Request, section: str) -> HTMLResponse:
    """PR 326: a query that hit statement_timeout (app/db/admin_data.py) - a plain error
    page instead of a 500, so the panel stays usable on a big table."""
    response = _page(request, "admin/timeout.html", section, seconds=QUERY_TIMEOUT_MS // 1000)
    response.status_code = 503
    return response


def _page(request: Request, template: str, section: str, **context: object) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        {
            "csrf_token": csrf_token(request),
            "admin_section": section,
            "admin_nav": ADMIN_NAV,
            **context,
        },
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
    ip = request.client.host if request.client is not None else "unknown"
    wait = login_locked_for(ip)
    if wait > 0:
        return _locked_page(request, wait)
    if not password_matches(password):
        record_failed_login(ip)
        wait = login_locked_for(ip)
        if wait > 0:
            return _locked_page(request, wait)
        return _login_page(request, error="Неверный пароль", status_code=401)
    reset_failed_logins(ip)
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


def _locked_page(request: Request, wait: float) -> HTMLResponse:
    seconds = max(1, math.ceil(wait))
    response = _login_page(
        request,
        error=f"Слишком много попыток. Повторите через {seconds} с.",
        status_code=429,
    )
    response.headers["Retry-After"] = str(seconds)
    return response


def install_admin_headers(app: FastAPI) -> None:
    """``Cache-Control: no-store`` and ``X-Robots-Tag: noindex`` on every /admin* response
    while the panel is enabled. Registered after install_cache_policy() so this runs
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
