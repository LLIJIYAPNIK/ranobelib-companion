"""/admin: the panel's login/logout (PR 324), overview and read-only data browser (PR 325),
the shared sidebar/top-bar shell (PR 341), the UI kit's dev-only showcase (PR 342).

Every route sits behind ``require_admin_enabled`` (404 without ADMIN_PASSWORD), every page
but the login form behind ``require_admin`` (app/auth/admin.py). The data pages only read
(app/db/admin_data.py); changing data is a separate, later decision.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg import AsyncConnection
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout

from app.admin_kit import Undo, pop_toast, push_toast
from app.admin_kit_demo import charts_context, table_context
from app.auth.admin import (
    LOGIN_PATH,
    check_csrf,
    client_ip,
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
from app.db import admin_audit
from app.db import connection as db_connection
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


logger = logging.getLogger(__name__)

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


def require_development() -> None:
    """PR 342: the UI kit's showcase exists only outside production - there it's a 404
    like any unknown URL, even for the logged-in admin."""
    if get_settings().is_production:
        raise HTTPException(status_code=404)


@router.get("/_kit", response_model=None, dependencies=[Depends(require_development)])
async def admin_kit_showcase(request: Request, _: Admin) -> HTMLResponse:
    args = dict(request.query_params)
    return _page(
        request,
        "admin/kit.html",
        "kit",
        table=table_context(args),
        charts=charts_context(_server_now().date()),
    )


@router.post("/_kit/demo", response_model=None, dependencies=[Depends(require_development)])
async def admin_kit_demo(
    request: Request,
    _: Admin,
    csrf: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
    name: Annotated[str, Form(max_length=100)] = "",
) -> Response:
    """The showcase's dialogs post here so the toast flow can be tried end to end. It
    changes nothing - the demo rows are a fixed list."""
    check_csrf(request, csrf)
    if action == "delete":
        if confirm != "1":
            push_toast(request, "Не удалено: нужно подтвердить «Я понимаю…»", tone="error")
        else:
            push_toast(
                request,
                f"Демо: «{name}» удалён (на самом деле ничего не изменилось)",
                undo=Undo("/admin/_kit/demo", {"action": "undo", "name": name}),
            )
    elif action == "undo":
        push_toast(request, f"Демо: удаление «{name}» отменено")
    elif action == "save":
        push_toast(request, f"Демо: «{name}» сохранён")
    return RedirectResponse("/admin/_kit", status_code=303)


_WEEKDAYS = (
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"
)  # fmt: skip
_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


@dataclass(frozen=True)
class ServerTime:
    """The top bar's "now" (PR 341): the server's clock in ADMIN_TIMEZONE, as of render."""

    iso: str
    date: str
    time: str
    timezone: str


def _server_now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().admin_timezone))


def server_time() -> ServerTime:
    now = _server_now()
    day = f"{_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS_GENITIVE[now.month - 1]} {now.year}"
    return ServerTime(
        iso=now.isoformat(timespec="minutes"),
        date=day,
        time=f"{now:%H:%M}",
        timezone=get_settings().admin_timezone,
    )


def _page(request: Request, template: str, section: str, **context: object) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        {
            "csrf_token": csrf_token(request),
            "admin_section": section,
            "admin_nav": ADMIN_NAV,
            "server_time": server_time(),
            "toast": pop_toast(request),
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
    ip = client_ip(request)
    wait = login_locked_for(ip)
    if wait > 0:
        # Not logged: while locked the password isn't even checked, and a flood of
        # refused attempts would only fill the log.
        return _locked_page(request, wait)
    if not password_matches(password):
        record_failed_login(ip)
        wait = login_locked_for(ip)
        details = {"locked_for_seconds": math.ceil(wait)} if wait > 0 else {}
        await _audit_login_event(request, admin_audit.LOGIN_FAILED, details)
        if wait > 0:
            return _locked_page(request, wait)
        return _login_page(request, error="Неверный пароль", status_code=401)
    reset_failed_logins(ip)
    start_admin_session(request)
    await _audit_login_event(request, admin_audit.LOGIN)
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout", response_model=None)
async def admin_logout(request: Request, csrf: Annotated[str, Form()] = "") -> Response:
    check_csrf(request, csrf)
    if is_admin(request):
        await _audit_login_event(request, admin_audit.LOGOUT)
    end_admin_session(request)
    return RedirectResponse(LOGIN_PATH, status_code=303)


async def _audit_login_event(
    request: Request, action: str, details: dict[str, object] | None = None
) -> None:
    """PR 343: login, failed login and logout go to the action log. On its own
    connection, not a route dependency, so the login form works even with the database
    down - then the event is only in the server log. Never the password (it isn't
    passed), only the IP."""
    try:
        async with db_connection.connection() as conn:
            await admin_audit.record_admin_action(
                conn, action, ip=client_ip(request), details=details
            )
    except (psycopg.Error, PoolTimeout):
        logger.exception("Could not write the admin action log entry %r", action)


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
