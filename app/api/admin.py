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

import httpx
import psycopg
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg import AsyncConnection
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout

from app.admin_analytics import (
    DROP_MIN_READERS,
    FINISHED_TOP,
    HEATMAP_MIN_READS,
    RETENTION_CHART_LIMIT,
    TITLE_COLUMNS,
    analytics_tabs,
    behaviour_period_links,
    biggest_drops,
    hints,
    new_and_returning_chart,
    parse_behaviour_period,
    reading_times,
    retention,
    retention_chart,
    title_kpis,
    title_list_rows,
)
from app.admin_comments import COLUMNS as COMMENT_COLUMNS
from app.admin_comments import back_href as comments_back_href
from app.admin_comments import (
    comment_audit_details,
    comment_filters,
    comment_label,
    comment_rows,
    drawer_context,
    parse_kind,
    parse_status,
    without_drawer,
)
from app.admin_comments import safe_back as comments_safe_back
from app.admin_kit import Undo, pop_toast, push_toast
from app.admin_kit_demo import charts_context, table_context
from app.admin_overview import (
    DOWNLOAD_ERROR_COLUMNS,
    METRIC_INFO,
    PERIOD_LABELS,
    RECENT_USER_COLUMNS,
    activity_chart,
    as_of,
    attention_items,
    chart_has_data,
    compare_href,
    download_error_rows,
    kpis,
    metric_tabs,
    parse_compare,
    parse_metric,
    period_links,
    popular_titles,
    recent_user_rows,
)
from app.admin_users import (
    BAN_TERM_LABELS,
    back_href,
    ban_state,
    parse_flag,
    safe_back,
    summary_items,
    user_columns,
    user_filters,
    user_rows,
)
from app.api.auth import send_password_reset_email
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
from app.comment_attachment import remove_comment_attachment
from app.config import get_settings
from app.db import admin_audit
from app.db import connection as db_connection
from app.db.admin_analytics import (
    DEFAULT_SORT,
    NEW_AND_RETURNING_DAYS,
    SORTS,
    TITLES_LIMIT,
    cached_title_rows,
    chapter_reach,
    last_read_positions,
    new_and_returning,
    reading_heatmap,
    title_totals,
)
from app.db.admin_audit import (
    COMMENT_DELETE,
    COMMENT_HIDE,
    COMMENT_SHOW,
    USER_BAN,
    USER_CONFIRM_EMAIL,
    USER_END_SESSIONS,
    USER_SEND_PASSWORD_RESET,
    USER_UNBAN,
)
from app.db.admin_comments import DEFAULT_SORT as COMMENT_DEFAULT_SORT
from app.db.admin_comments import DELETED as COMMENT_DELETED
from app.db.admin_comments import PAGE_SIZE as COMMENTS_PAGE_SIZE
from app.db.admin_comments import SORTS as COMMENT_SORTS
from app.db.admin_comments import CommentRow, comment_detail, list_comments
from app.db.admin_data import (
    MASK,
    QUERY_TIMEOUT_MS,
    Overview,
    browse_table,
    list_tables,
    overview,
    table_columns,
)
from app.db.admin_stats import (
    AttentionCounts,
    PeriodStats,
    TitleReaders,
    cached,
    cached_attention_counts,
    cached_period_stats,
    cached_top_titles,
    parse_period,
)
from app.db.admin_users import DEFAULT_SORT as USER_DEFAULT_SORT
from app.db.admin_users import PAGE_SIZE as USERS_PAGE_SIZE
from app.db.admin_users import SORTS as USER_SORTS
from app.db.admin_users import list_users, user_summary
from app.db.bans import BAN_TERMS, AlreadyBanned, active_ban, ban_history, ban_user, lift_ban
from app.db.bans import REASON_LIMIT as BAN_REASON_LIMIT
from app.db.comments import hide_comment, purge_comment, show_comment
from app.db.connection import get_connection
from app.db.users import User, end_all_sessions, get_user_by_id, mark_email_verified
from app.services.titles import (
    TitleSummary,
    tables_of_contents,
    title_details,
    title_summaries,
)
from app.templating import templates
from app.timezones import DEFAULT_TIMEZONE, is_valid_timezone

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
    NavItem("analytics", "Аналитика", "chart", "/admin/analytics", "Контент"),
    NavItem("users", "Пользователи", "users", "/admin/users", "Сообщество"),
    NavItem("comments", "Комментарии", "comments", "/admin/comments", "Сообщество"),
    NavItem("tables", "Данные БД", "database", "/admin/tables", "Данные"),
    NavItem("system", "Система", "server", None, "Платформа"),
    NavItem("audit", "Журнал", "history", None, "Платформа"),
)


@router.get("", response_model=None)
async def admin_home(
    request: Request,
    _: Admin,
    period: Annotated[str | None, Query(max_length=10)] = None,
    metric: Annotated[str | None, Query(max_length=40)] = None,
    compare: Annotated[str | None, Query(max_length=5)] = None,
) -> HTMLResponse:
    """«Обзор» (PR 345). Each block is loaded on its own: a query that hits the admin
    timeout leaves an error state in its block and the rest of the page still shows.
    The connection is taken here rather than as a route dependency, so with the database
    down the page still opens and says so instead of failing as a whole."""
    tz = get_settings().admin_timezone
    key = parse_period(period)
    failed: set[str] = set()
    stats: PeriodStats | None = None
    top: list[TitleReaders] | None = None
    recent: Overview | None = None
    counts: AttentionCounts | None = None
    database_down = False
    try:
        async with db_connection.connection() as conn:
            stats = await _block(failed, "stats", lambda: cached_period_stats(conn, key, tz))
            top = await _block(failed, "titles", lambda: cached_top_titles(conn, key, tz))
            recent = await _block(failed, "recent", lambda: overview(conn))
            counts = await _block(failed, "attention", lambda: cached_attention_counts(conn))
    except (psycopg.OperationalError, PoolTimeout):
        logger.exception("The admin overview could not reach the database")
        database_down = True
    titles = (
        popular_titles(top, await _title_summaries([row.slug_url for row in top]))
        if top is not None
        else []
    )
    # After the title lookups, so a failure reaching ranobelib.me just now counts too.
    attention = attention_items(counts, database_down)
    args = dict(request.query_params)
    chart_metric = parse_metric(metric)
    chart_compare = parse_compare(compare)
    return _page(
        request,
        "admin/index.html",
        "overview",
        timezone=tz,
        metric_tabs=metric_tabs(stats, args, chart_metric) if stats else [],
        chart=activity_chart(stats, chart_metric, chart_compare) if stats else None,
        chart_has_data=chart_has_data(stats, chart_metric, chart_compare) if stats else False,
        chart_metric_label=METRIC_INFO[chart_metric].label,
        compare=chart_compare,
        compare_href=compare_href(args, chart_compare),
        popular=titles,
        attention=attention,
        database_down=database_down,
        failed=failed,
        period_key=key,
        period_label=PERIOD_LABELS[key],
        periods=period_links(args, key),
        as_of=as_of(stats, tz) if stats else None,
        kpis=kpis(stats) if stats else [],
        user_columns=RECENT_USER_COLUMNS,
        recent_users=recent_user_rows(recent.recent_users, tz) if recent else [],
        error_columns=DOWNLOAD_ERROR_COLUMNS,
        download_errors=download_error_rows(recent.recent_download_errors, tz) if recent else [],
    )


async def _title_summaries(slugs: list[str]) -> dict[str, TitleSummary]:
    """Names and covers for a page's titles, each looked up once per page through
    app/services/ (the SDK's cache makes a repeat a local hit). title_summaries() already
    skips a title the SDK can't resolve; a network failure reaching ranobelib.me isn't an
    SDK error, so it's caught here - the list then shows slug_urls rather than failing
    the whole page."""
    try:
        return await title_summaries(set(slugs))
    except httpx.HTTPError:
        logger.warning("Could not reach ranobelib.me for the admin panel's title names")
        return {}


async def _block[T](failed: set[str], name: str, load: Callable[[], Awaitable[T]]) -> T | None:
    """One overview block's data, or None (and `name` in `failed`) if its query hit the
    admin timeout - the block then shows an error state with «Повторить»."""
    try:
        return await load()
    except QueryCanceled:
        logger.warning("The admin overview's %r block timed out", name)
        failed.add(name)
        return None


@router.get("/analytics", response_model=None)
async def admin_analytics(
    request: Request,
    _: Admin,
    conn: Connection,
    period: Annotated[str | None, Query(max_length=10)] = None,
    sort: Annotated[str | None, Query(max_length=20)] = None,
    order: str = "desc",
) -> HTMLResponse:
    """«Аналитика» (PR 346): the most active titles of a period. Names and covers come
    from the SDK through app/services/; «Дочитали» from the SDK's table of contents for
    the first FINISHED_TOP rows only."""
    tz = get_settings().admin_timezone
    key = parse_period(period)
    sort_key = sort if sort in SORTS else DEFAULT_SORT
    descending = order != "asc"
    try:
        rows = await cached_title_rows(conn, key, tz, sort_key, descending)
        top = [row.slug_url for row in rows[:FINISHED_TOP]]
        positions = await last_read_positions(conn, top)
    except QueryCanceled:
        return _timed_out(request, "analytics")
    summaries = await _title_summaries([row.slug_url for row in rows])
    contents = await tables_of_contents(top)
    args = dict(request.query_params)
    return _page(
        request,
        "admin/analytics.html",
        "analytics",
        tabs=analytics_tabs("titles"),
        args=args,
        period_label=PERIOD_LABELS[key],
        periods=period_links(args, key),
        columns=TITLE_COLUMNS,
        rows=title_list_rows(rows, summaries, contents, positions),
        sort=sort_key,
        descending=descending,
        limit=TITLES_LIMIT,
        finished_top=FINISHED_TOP,
    )


# Before /analytics/{slug_url} - a fixed path has to win over the title page's pattern.
@router.get("/analytics/behaviour", response_model=None)
async def admin_analytics_behaviour(
    request: Request,
    _: Admin,
    conn: Connection,
    period: Annotated[str | None, Query(max_length=10)] = None,
) -> HTMLResponse:
    """«Поведение читателей» (PR 347): when people read (weekday x hour) and new against
    returning readers over 14 days, both on the admin's clock. The zone has to be one
    Postgres knows as well (it groups by local time); one only Python knows falls back
    to UTC, and the page says which zone it used."""
    tz = get_settings().admin_timezone
    if not await is_valid_timezone(conn, tz):
        logger.warning("ADMIN_TIMEZONE=%r is unknown to Postgres - analytics use UTC", tz)
        tz = DEFAULT_TIMEZONE
    key = parse_behaviour_period(period)
    try:
        grid = await cached(("heatmap", key, tz), lambda: reading_heatmap(conn, key, tz))
        days = await cached(("new_returning", tz), lambda: new_and_returning(conn, tz))
    except QueryCanceled:
        return _timed_out(request, "analytics")
    args = dict(request.query_params)
    return _page(
        request,
        "admin/analytics_behaviour.html",
        "analytics",
        tabs=analytics_tabs("behaviour"),
        timezone=tz,
        period_label=PERIOD_LABELS[key],
        periods=behaviour_period_links(args, key),
        times=reading_times(grid),
        visitors=new_and_returning_chart(days),
        visitor_days=NEW_AND_RETURNING_DAYS,
        min_reads=HEATMAP_MIN_READS,
    )


@router.get("/analytics/{slug_url}", response_model=None)
async def admin_analytics_title(
    request: Request,
    slug_url: Annotated[str, Path(max_length=200)],
    _: Admin,
    conn: Connection,
    period: Annotated[str | None, Query(max_length=10)] = None,
) -> HTMLResponse:
    """A title's page (PR 346): its numbers for a period and, over all time, how many of
    the first chapter's readers reached each next chapter - chapters in the SDK's order.
    Without the SDK (title gone, ranobelib.me unreachable) the page still shows the
    application's own numbers; a title neither knows is a 404."""
    tz = get_settings().admin_timezone
    key = parse_period(period)
    details = await title_details(slug_url)
    chapters = [c for volume in details.volumes for c in volume.chapters] if details else []
    try:
        totals = await cached(
            ("title_totals", slug_url, key, tz),
            lambda: title_totals(conn, slug_url, key, tz),
        )
        reach = (
            await cached(
                ("chapter_reach", slug_url),
                lambda: chapter_reach(conn, slug_url, (chapters[0].volume, chapters[0].number)),
            )
            if chapters
            else None
        )
    except QueryCanceled:
        return _timed_out(request, "analytics")
    if details is None and not any(
        c.current or c.previous
        for c in (totals.readers, totals.chapters, totals.seconds, totals.library_adds)
    ):
        raise HTTPException(status_code=404)
    points = retention(details.volumes, reach.cohort, reach.reached) if details and reach else []
    args = dict(request.query_params)
    return _page(
        request,
        "admin/analytics_title.html",
        "analytics",
        slug_url=slug_url,
        details=details,
        period_label=PERIOD_LABELS[key],
        periods=period_links(args, key),
        kpis=title_kpis(totals),
        cohort=reach.cohort if reach else 0,
        points=points,
        chart=retention_chart(points) if reach and reach.cohort else None,
        drops=biggest_drops(points),
        drop_min_readers=DROP_MIN_READERS,
        hints=hints(details.volumes, points) if details else [],
        chart_limit=RETENTION_CHART_LIMIT,
    )


@router.get("/users", response_model=None)
async def admin_users(
    request: Request,
    _: Admin,
    conn: Connection,
    q: Annotated[str, Query(max_length=200)] = "",
    verified: Annotated[str | None, Query(max_length=5)] = None,
    active: Annotated[str | None, Query(max_length=5)] = None,
    library: Annotated[str | None, Query(max_length=5)] = None,
    period: Annotated[str | None, Query(max_length=10)] = None,
    sort: Annotated[str | None, Query(max_length=20)] = None,
    order: str = "desc",
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    user: Annotated[int | None, Query(ge=1, le=2_147_483_647)] = None,
) -> HTMLResponse:
    """«Пользователи» (PR 348): accounts with search (email, nickname, id), filters and
    sorting. Read-only; the actions are the POST routes below. ``user`` opens that
    account's drawer over the list."""
    tz = get_settings().admin_timezone
    key = parse_period(period)
    sort_key = sort if sort in USER_SORTS else USER_DEFAULT_SORT
    descending = order != "asc"
    try:
        data = await list_users(
            conn,
            key=key,
            tz=tz,
            query=q,
            verified=parse_flag(verified),
            active=parse_flag(active),
            library=parse_flag(library),
            sort=sort_key,
            descending=descending,
            page=page,
        )
        summary = await user_summary(conn, user, key, tz) if user is not None else None
        bans = (
            ban_state(await active_ban(conn, summary.id), await ban_history(conn, summary.id), tz)
            if summary
            else None
        )
    except QueryCanceled:
        return _timed_out(request, "users")
    args = dict(request.query_params)
    filters = user_filters(args, key)
    list_args = {name: value for name, value in args.items() if name != "user"}
    return _page(
        request,
        "admin/users.html",
        "users",
        args=args,
        period_label=PERIOD_LABELS[key],
        periods=period_links(args, key),
        columns=user_columns(key),
        filters=filters,
        filtered=bool(q.strip()) or any(item.value for item in filters),
        rows=user_rows(data.rows, tz, args),
        data=data,
        per_page=USERS_PAGE_SIZE,
        sort=sort_key,
        descending=descending,
        summary=summary,
        summary_items=summary_items(summary, tz, key) if summary else [],
        bans=bans,
        ban_terms=BAN_TERM_LABELS,
        reason_limit=BAN_REASON_LIMIT,
        drawer_missing=user is not None and summary is None,
        close_href=back_href(list_args),
        back=back_href(args),
    )


# --- «Пользователи»: the three safe actions (PR 348) ---------------------------------------
# Each is a POST from a confirmation dialog in the drawer: CSRF checked, the change made
# with the site's own function, then recorded in the action log (PR 343) on the same
# connection, and the list shown again with a toast. ``back`` only ever leads to the
# users list (safe_back()).

UserId = Annotated[int, Path(ge=1, le=2_147_483_647)]


async def _target_user(conn: AsyncConnection, user_id: int) -> User:
    user = await get_user_by_id(conn, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    return user


def _label(user: User) -> str:
    return user.nickname or user.email


@router.post("/users/{user_id}/sessions", response_model=None)
async def admin_user_end_sessions(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Завершить все сессии»: every device the account is signed in on is signed out on
    its next request - session_version + 1, as after a password reset."""
    check_csrf(request, csrf)
    user = await _target_user(conn, user_id)
    await end_all_sessions(conn, user.id)
    await admin_audit.record_change(
        conn,
        USER_END_SESSIONS,
        ip=client_ip(request),
        entity="user",
        entity_id=user.id,
        entity_label=_label(user),
        extra={"effect": "все входы, выданные до этого момента, больше не действуют"},
    )
    push_toast(request, f"Все сессии «{_label(user)}» завершены")
    return RedirectResponse(safe_back(back), status_code=303)


@router.post("/users/{user_id}/verify-email", response_model=None)
async def admin_user_verify_email(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Подтвердить email вручную»: for someone whose letter never arrived. An address
    that's already confirmed is left alone - nothing changes, nothing is logged."""
    check_csrf(request, csrf)
    user = await _target_user(conn, user_id)
    if user.email_verified_at is not None:
        push_toast(request, f"Email «{_label(user)}» уже подтверждён", tone="error")
        return RedirectResponse(safe_back(back), status_code=303)
    updated = await mark_email_verified(conn, user.id)
    await admin_audit.record_change(
        conn,
        USER_CONFIRM_EMAIL,
        ip=client_ip(request),
        entity="user",
        entity_id=user.id,
        entity_label=_label(user),
        before={"email_verified_at": None},
        after={"email_verified_at": updated.email_verified_at},
    )
    push_toast(request, f"Email «{_label(user)}» подтверждён")
    return RedirectResponse(safe_back(back), status_code=303)


@router.post("/users/{user_id}/password-reset", response_model=None)
async def admin_user_password_reset(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Отправить письмо сброса пароля»: the same letter «Забыли пароль?» sends. The admin
    never sees or sets the password, and the link isn't shown or logged."""
    check_csrf(request, csrf)
    user = await _target_user(conn, user_id)
    await send_password_reset_email(request, conn, user)
    await admin_audit.record_change(
        conn,
        USER_SEND_PASSWORD_RESET,
        ip=client_ip(request),
        entity="user",
        entity_id=user.id,
        entity_label=_label(user),
        extra={"sent_to": user.email},
    )
    push_toast(request, f"Письмо для сброса пароля отправлено на {user.email}")
    return RedirectResponse(safe_back(back), status_code=303)


# --- blocking (PR 349) ---------------------------------------------------------------------


@router.post("/users/{user_id}/ban", response_model=None)
async def admin_user_ban(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    term: Annotated[str, Form(max_length=10)] = "",
    reason: Annotated[str, Form(max_length=2000)] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Заблокировать»: for 1, 7 or 30 days or until lifted, with an optional reason the
    person sees. Their open sessions turn into guest ones on the next request
    (get_current_user()); the term runs out by itself."""
    check_csrf(request, csrf)
    user = await _target_user(conn, user_id)
    if term not in BAN_TERMS:
        push_toast(request, "Не заблокировано: выберите срок", tone="error")
        return RedirectResponse(safe_back(back), status_code=303)
    try:
        ban = await ban_user(conn, user.id, term=term, reason=reason)
    except AlreadyBanned:
        push_toast(request, f"«{_label(user)}» уже заблокирован", tone="error")
        return RedirectResponse(safe_back(back), status_code=303)
    await admin_audit.record_change(
        conn,
        USER_BAN,
        ip=client_ip(request),
        entity="user",
        entity_id=user.id,
        entity_label=_label(user),
        before={"banned": False},
        after={"banned": True, "expires_at": ban.expires_at},
        extra={"term": term, "reason": ban.reason, "ban_id": ban.id},
    )
    push_toast(request, f"«{_label(user)}» заблокирован")
    return RedirectResponse(safe_back(back), status_code=303)


@router.post("/users/{user_id}/unban", response_model=None)
async def admin_user_unban(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    reason: Annotated[str, Form(max_length=2000)] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Снять блокировку» now, with an optional note why. A block that already ran out
    or was lifted changes nothing and isn't logged."""
    check_csrf(request, csrf)
    user = await _target_user(conn, user_id)
    ban = await lift_ban(conn, user.id, reason=reason)
    if ban is None:
        push_toast(request, f"«{_label(user)}» не заблокирован", tone="error")
        return RedirectResponse(safe_back(back), status_code=303)
    await admin_audit.record_change(
        conn,
        USER_UNBAN,
        ip=client_ip(request),
        entity="user",
        entity_id=user.id,
        entity_label=_label(user),
        before={"banned": True, "expires_at": ban.expires_at},
        after={"banned": False},
        extra={"reason": ban.lifted_reason, "ban_id": ban.id},
    )
    push_toast(request, f"Блокировка «{_label(user)}» снята")
    return RedirectResponse(safe_back(back), status_code=303)


@router.get("/comments", response_model=None)
async def admin_comments(
    request: Request,
    _: Admin,
    conn: Connection,
    q: Annotated[str, Query(max_length=200)] = "",
    status: Annotated[str | None, Query(max_length=10)] = None,
    kind: Annotated[str | None, Query(max_length=10)] = None,
    sort: Annotated[str | None, Query(max_length=20)] = None,
    order: str = "desc",
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    comment: Annotated[int | None, Query(ge=1, le=2_147_483_647)] = None,
) -> HTMLResponse:
    """«Комментарии» (PR 350): every comment with search (text, author, title, id),
    filters and sorting. Read-only; hiding, showing and deleting are the POST routes
    below. ``comment`` opens that comment's drawer over the list."""
    tz = get_settings().admin_timezone
    sort_key = sort if sort in COMMENT_SORTS else COMMENT_DEFAULT_SORT
    descending = order != "asc"
    try:
        data = await list_comments(
            conn,
            query=q,
            status=parse_status(status),
            kind=parse_kind(kind),
            sort=sort_key,
            descending=descending,
            page=page,
        )
        detail = await comment_detail(conn, comment) if comment is not None else None
        bans = len(await ban_history(conn, detail.comment.user_id)) if detail else 0
    except QueryCanceled:
        return _timed_out(request, "comments")
    slugs = [row.slug_url for row in data.rows]
    if detail:
        slugs.append(detail.comment.slug_url)
    titles = await _title_summaries(slugs) if slugs else {}
    args = dict(request.query_params)
    filters = comment_filters(args)
    list_args = {name: value for name, value in args.items() if name != "comment"}
    return _page(
        request,
        "admin/comments.html",
        "comments",
        args=args,
        columns=COMMENT_COLUMNS,
        filters=filters,
        filtered=bool(q.strip()) or any(item.value for item in filters),
        rows=comment_rows(data.rows, titles, tz, args),
        data=data,
        per_page=COMMENTS_PAGE_SIZE,
        sort=sort_key,
        descending=descending,
        drawer=drawer_context(detail, titles, tz, args, bans) if detail else None,
        drawer_missing=comment is not None and detail is None,
        close_href=comments_back_href(list_args),
        back=comments_back_href(args),
    )


# --- «Комментарии»: hide, show, delete for good (PR 350) -----------------------------------
# Same shape as the users' actions above: a CSRF-checked POST from the drawer, then the
# list again with a toast. «Скрыть» is the soft one - no dialog, an «Отменить» in its
# toast instead; «Удалить навсегда» sits behind a confirmation that offers hiding first.

CommentId = Annotated[int, Path(ge=1, le=2_147_483_647)]


@router.post("/comments/{comment_id}/hide", response_model=None)
async def admin_comment_hide(
    request: Request,
    comment_id: CommentId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Скрыть»: readers stop seeing it at once; its author still does, marked «скрыт
    модератором». An already hidden or deleted comment is left alone."""
    check_csrf(request, csrf)
    target = comments_safe_back(back)
    comment = await _target_comment(conn, comment_id)
    hidden_at = await hide_comment(conn, comment_id)
    if hidden_at is None:
        push_toast(request, f"Комментарий #{comment_id} уже скрыт или удалён", tone="error")
        return RedirectResponse(target, status_code=303)
    await admin_audit.record_change(
        conn,
        COMMENT_HIDE,
        ip=client_ip(request),
        entity="comment",
        entity_id=comment_id,
        entity_label=comment_label(comment),
        before={"hidden_at": None},
        after={"hidden_at": hidden_at},
        extra=comment_audit_details(comment),
    )
    # Back to the list with the drawer closed: the drawer is a modal dialog, and over the
    # toast it would leave «Отменить» unreachable.
    target = without_drawer(target)
    push_toast(
        request,
        f"Комментарий #{comment_id} скрыт",
        undo=Undo(f"/admin/comments/{comment_id}/show", {"back": target}),
    )
    return RedirectResponse(target, status_code=303)


@router.post("/comments/{comment_id}/show", response_model=None)
async def admin_comment_show(
    request: Request,
    comment_id: CommentId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Показать» (and the «Отменить» of «Скрыть»): back in the thread for everyone."""
    check_csrf(request, csrf)
    target = comments_safe_back(back)
    comment = await _target_comment(conn, comment_id)
    hidden_at = await show_comment(conn, comment_id)
    if hidden_at is None:
        push_toast(request, f"Комментарий #{comment_id} не скрыт", tone="error")
        return RedirectResponse(target, status_code=303)
    await admin_audit.record_change(
        conn,
        COMMENT_SHOW,
        ip=client_ip(request),
        entity="comment",
        entity_id=comment_id,
        entity_label=comment_label(comment),
        before={"hidden_at": hidden_at},
        after={"hidden_at": None},
        extra=comment_audit_details(comment),
    )
    push_toast(request, f"Комментарий #{comment_id} снова виден читателям")
    return RedirectResponse(target, status_code=303)


@router.post("/comments/{comment_id}/delete", response_model=None)
async def admin_comment_delete(
    request: Request,
    comment_id: CommentId,
    _: Admin,
    conn: Connection,
    csrf: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
    back: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """«Удалить навсегда»: the text, attachment, reactions and their notifications go for
    good. With replies under it the row stays as «Комментарий удалён», so the thread
    keeps its shape. Needs «Я понимаю…» ticked."""
    check_csrf(request, csrf)
    target = comments_safe_back(back)
    detail = await comment_detail(conn, comment_id)
    if detail is None:
        raise HTTPException(status_code=404)
    if confirm != "1":
        push_toast(request, "Не удалено: нужно подтвердить «Я понимаю…»", tone="error")
        return RedirectResponse(target, status_code=303)
    if detail.comment.status == COMMENT_DELETED and detail.replies:
        push_toast(request, f"Комментарий #{comment_id} уже удалён", tone="error")
        return RedirectResponse(target, status_code=303)
    purged = await purge_comment(conn, comment_id)
    if purged is None:
        raise HTTPException(status_code=404)
    if purged.attachment_path:
        remove_comment_attachment(purged.attachment_path)
    await admin_audit.record_change(
        conn,
        COMMENT_DELETE,
        ip=client_ip(request),
        entity="comment",
        entity_id=comment_id,
        entity_label=comment_label(detail.comment),
        before={"status": detail.comment.status},
        after={"status": "removed" if purged.removed else "deleted_kept_for_replies"},
        extra={
            **comment_audit_details(detail.comment),
            "replies": detail.replies,
            "attachment_removed": bool(purged.attachment_path),
        },
    )
    push_toast(request, f"Комментарий #{comment_id} удалён навсегда")
    return RedirectResponse(without_drawer(target) if purged.removed else target, status_code=303)


async def _target_comment(conn: AsyncConnection, comment_id: int) -> CommentRow:
    detail = await comment_detail(conn, comment_id)
    if detail is None:
        raise HTTPException(status_code=404)
    return detail.comment


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
