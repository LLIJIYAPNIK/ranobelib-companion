"""/admin: the panel's login/logout (PR 324), overview and read-only data browser (PR 325),
the shared sidebar/top-bar shell (PR 341), the UI kit's dev-only showcase (PR 342), the
action log screen and the global search (PR 353).

Every route sits behind ``require_admin_enabled`` (404 without ADMIN_PASSWORD), every page
but the login form behind ``require_admin`` (app/auth/admin.py). The data pages only read
(app/db/admin_data.py); changing data is a separate, later decision.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import httpx
import psycopg
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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
from app.admin_audit import audit_csv, audit_filters, audit_rows, parse_action
from app.admin_audit import csv_filename as audit_csv_filename
from app.admin_audit import csv_href as audit_csv_href
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
from app.admin_moderation import Card as ModerationCard
from app.admin_moderation import comment_card, moderation_href, profile_card, queue_rows
from app.admin_moderation import item_key as moderation_item_key
from app.admin_moderation import parse_item as parse_moderation_item
from app.admin_moderation import parse_type as parse_moderation_type
from app.admin_moderation import safe_back as moderation_safe_back
from app.admin_moderation import tabs as moderation_tabs
from app.admin_moderation import with_history as moderation_with_history
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
from app.admin_search import (
    MIN_QUERY,
    PAGE_LIMIT,
    SUGGEST_LIMIT,
    Group,
    as_json,
    comment_hits,
    groups,
    normalize,
    screen_hits,
    searchable,
    table_hits,
    user_hits,
)
from app.admin_system import (
    JOB_COLUMNS,
    SDK_CACHE_CLEAR_ISSUE,
    app_service,
    database_service,
    disk_summary,
    job_rows,
    latency_chart,
    ranobelib_service,
    request_kpis,
    storage_rows,
    versions,
)
from app.admin_tables import (
    CELL_PREVIEW,
    TABLE_LIST_COLUMNS,
    browse_columns,
    csv_filename,
    csv_href,
    table_csv,
    table_id,
    table_list_rows,
)
from app.admin_users import (
    BAN_TERM_LABELS,
    BanState,
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
    limit_admin_exports,
    limit_admin_writes,
    login_locked_for,
    password_matches,
    record_failed_login,
    require_admin,
    require_admin_enabled,
    require_admin_fetch,
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
    AUDIT_EXPORT,
    COMMENT_DELETE,
    COMMENT_HIDE,
    COMMENT_SHOW,
    DATA_EXPORT,
    REPORTS_BAN_AUTHOR,
    REPORTS_CLEAR_PROFILE,
    REPORTS_DISMISS,
    REPORTS_HIDE_COMMENT,
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
    EXPORT_LIMIT,
    MASK,
    QUERY_TIMEOUT_MS,
    Overview,
    _read_only,
    browse_table,
    export_table,
    list_tables,
    overview,
    table_columns,
    user_id_columns,
)
from app.db.admin_data import PAGE_SIZE as TABLE_PAGE_SIZE
from app.db.admin_reports import QUEUE_LIMIT, author_record, object_reports, reported_profile
from app.db.admin_reports import queue as report_queue
from app.db.admin_search import search_comments, search_users
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
from app.db.admin_system import DatabaseStatus, database_status
from app.db.admin_users import DEFAULT_SORT as USER_DEFAULT_SORT
from app.db.admin_users import PAGE_SIZE as USERS_PAGE_SIZE
from app.db.admin_users import SORTS as USER_SORTS
from app.db.admin_users import list_users, user_summary
from app.db.bans import BAN_TERMS, AlreadyBanned, active_ban, ban_history, ban_user, lift_ban
from app.db.bans import REASON_LIMIT as BAN_REASON_LIMIT
from app.db.comments import hide_comment, purge_comment, show_comment
from app.db.connection import get_connection
from app.db.reports import ACTIONED, DISMISSED, resolve_reports
from app.db.users import (
    User,
    clear_profile,
    end_all_sessions,
    get_user_by_id,
    mark_email_verified,
    restore_profile,
)
from app.jobs.store import list_active_jobs
from app.request_metrics import MAX_SAMPLES as REQUEST_SAMPLES
from app.request_metrics import per_minute, summarize
from app.request_metrics import samples as request_samples
from app.services.paragraphs import paragraph_text
from app.services.probe import probe_ranobelib
from app.services.titles import (
    TitleSummary,
    tables_of_contents,
    title_details,
    title_summaries,
)
from app.system_info import (
    STARTED_AT,
    app_version,
    directory_usage,
    disk_usage,
    python_version,
    sdk_version,
    uptime_seconds,
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

# PR 355: every value a request can send the panel has a size limit - none reaches a query,
# a comparison or the log unbounded. A token is 43 characters, a Postgres name at most 63
# bytes; a sort direction is "asc" or "desc", anything else is read as "desc" (not refused).
Order = Annotated[str, Query(max_length=20)]
Csrf = Annotated[str, Form(max_length=128)]
TableName = Annotated[str, Path(max_length=63)]
SortKey = Annotated[str | None, Query(max_length=63)]

# PR 355: every POST that changes data, and every CSV export, is rate-limited per IP on top
# of the admin check (app/auth/admin.py).
WRITES = [Depends(limit_admin_writes)]
EXPORTS = [Depends(limit_admin_exports)]


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
    NavItem("moderation", "Модерация", "shield-alert", "/admin/moderation", "Сообщество"),
    NavItem("tables", "Данные БД", "database", "/admin/tables", "Данные"),
    NavItem("system", "Система", "server", "/admin/system", "Платформа"),
    NavItem("audit", "Журнал", "history", "/admin/audit", "Платформа"),
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


async def _read[T](conn: AsyncConnection, load: Callable[[], Awaitable[T]]) -> T:
    """PR 355: a lookup borrowed from the site's own modules (bans, time zones) run like
    every admin query - read-only, under the admin timeout."""
    async with _read_only(conn):
        return await load()


async def _bans(conn: AsyncConnection, user_id: int, tz: str) -> BanState:
    return ban_state(await active_ban(conn, user_id), await ban_history(conn, user_id), tz)


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
    order: Order = "desc",
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
    if not await _read(conn, lambda: is_valid_timezone(conn, tz)):
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
    order: Order = "desc",
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
        bans = await _read(conn, lambda: _bans(conn, summary.id, tz)) if summary else None
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


@router.post("/users/{user_id}/sessions", response_model=None, dependencies=WRITES)
async def admin_user_end_sessions(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
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


@router.post("/users/{user_id}/verify-email", response_model=None, dependencies=WRITES)
async def admin_user_verify_email(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
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


@router.post("/users/{user_id}/password-reset", response_model=None, dependencies=WRITES)
async def admin_user_password_reset(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
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


@router.post("/users/{user_id}/ban", response_model=None, dependencies=WRITES)
async def admin_user_ban(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
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


@router.post("/users/{user_id}/unban", response_model=None, dependencies=WRITES)
async def admin_user_unban(
    request: Request,
    user_id: UserId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
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
    order: Order = "desc",
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
        history = (
            await _read(conn, lambda: ban_history(conn, detail.comment.user_id)) if detail else []
        )
        bans = len(history)
    except QueryCanceled:
        return _timed_out(request, "comments")
    slugs = [row.slug_url for row in data.rows]
    if detail:
        slugs.append(detail.comment.slug_url)
    titles = await _title_summaries(slugs) if slugs else {}
    quote = None
    if detail:
        row = detail.comment
        quote = await paragraph_text(
            row.slug_url, row.volume, row.number, row.branch_id, row.paragraph_index
        )
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
        drawer=drawer_context(detail, titles, tz, args, bans, quote) if detail else None,
        drawer_missing=comment is not None and detail is None,
        close_href=comments_back_href(list_args),
        back=comments_back_href(args),
    )


# --- «Комментарии»: hide, show, delete for good (PR 350) -----------------------------------
# Same shape as the users' actions above: a CSRF-checked POST from the drawer, then the
# list again with a toast. «Скрыть» is the soft one - no dialog, an «Отменить» in its
# toast instead; «Удалить навсегда» sits behind a confirmation that offers hiding first.

CommentId = Annotated[int, Path(ge=1, le=2_147_483_647)]


@router.post("/comments/{comment_id}/hide", response_model=None, dependencies=WRITES)
async def admin_comment_hide(
    request: Request,
    comment_id: CommentId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
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


@router.post("/comments/{comment_id}/show", response_model=None, dependencies=WRITES)
async def admin_comment_show(
    request: Request,
    comment_id: CommentId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
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


@router.post("/comments/{comment_id}/delete", response_model=None, dependencies=WRITES)
async def admin_comment_delete(
    request: Request,
    comment_id: CommentId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
    confirm: Annotated[str, Form(max_length=5)] = "",
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


# --- «Модерация»: the queue of reports (PR 358) --------------------------------------------


@router.get("/moderation", response_model=None)
async def admin_moderation(
    request: Request,
    _: Admin,
    conn: Connection,
    kind: Annotated[str | None, Query(alias="type", max_length=10)] = None,
    item: Annotated[str | None, Query(max_length=24)] = None,
) -> HTMLResponse:
    """«Модерация»: the objects readers reported (POST /reports, PR 356), the most
    reported first, then the one waiting longest - tabs by type, the list on the left
    and one object's card on the right (``item``, or the top of the queue)."""
    tz = get_settings().admin_timezone
    current = parse_moderation_type(kind)
    asked = parse_moderation_item(item)
    try:
        data = await report_queue(conn, current)
        if asked is None and data.items:
            asked_or_top = (data.items[0].target_type, data.items[0].target_id)
        else:
            asked_or_top = asked
        card = await _moderation_card(conn, *asked_or_top, tz) if asked_or_top else None
    except QueryCanceled:
        return _timed_out(request, "moderation")
    selected = moderation_item_key(*asked_or_top) if asked_or_top else None
    return _page(
        request,
        "admin/moderation.html",
        "moderation",
        tabs=moderation_tabs(data, current),
        rows=queue_rows(data.items, current, selected, tz),
        data=data,
        selected=selected,
        card=card,
        detail_asked=asked is not None,
        back_href=moderation_href(current),
        queue_limit=QUEUE_LIMIT,
        ban_terms=BAN_TERM_LABELS,
        reason_limit=BAN_REASON_LIMIT,
    )


# --- «Модерация»: the decisions (PR 358) ---------------------------------------------------
# Each is a CSRF-checked POST from a confirmation dialog on the card: the change made with
# the site's own function (PR 349's block, PR 350's hide), the object's open reports
# closed, one entry in the action log - whose id the toast's «Отменить» sends back - and
# the queue again, on the next object.

TargetType = Annotated[Literal["comment", "profile"], Path()]
TargetId = Annotated[int, Path(ge=1, le=2_147_483_647)]
Back = Annotated[str, Form(max_length=2000)]


@dataclass(frozen=True)
class ModerationTarget:
    label: str  # the action log's name for it
    author_id: int | None  # None when the object is gone
    author: str | None
    comment: CommentRow | None = None
    details: dict[str, object] | None = None  # what the log keeps about it


async def _moderation_target(
    conn: AsyncConnection, target_type: str, target_id: int
) -> ModerationTarget:
    if target_type == "comment":
        detail = await comment_detail(conn, target_id)
        if detail is None:
            return ModerationTarget(f"#{target_id}", None, None)
        row = detail.comment
        return ModerationTarget(
            comment_label(row), row.user_id, row.author, row, comment_audit_details(row)
        )
    user = await get_user_by_id(conn, target_id)
    if user is None:
        return ModerationTarget(f"#{target_id}", None, None)
    return ModerationTarget(_label(user), user.id, _label(user))


def _decided(request: Request, message: str, entry: int, back: str, item: str) -> RedirectResponse:
    """The toast with «Отменить» (back to this object, its reports reopened) and the
    queue - whose top is now the next object."""
    push_toast(
        request,
        message,
        undo=Undo("/admin/moderation/undo", {"entry": str(entry), "back": back, "item": item}),
    )
    return RedirectResponse(back, status_code=303)


def _nothing_open(request: Request, back: str) -> RedirectResponse:
    push_toast(request, "Жалоб на этот объект уже нет — решение принято раньше", tone="error")
    return RedirectResponse(back, status_code=303)


@router.post(
    "/moderation/{target_type}/{target_id}/dismiss", response_model=None, dependencies=WRITES
)
async def admin_moderation_dismiss(
    request: Request,
    target_type: TargetType,
    target_id: TargetId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
    back: Back = "",
) -> Response:
    """«Одобрить»: the object stays as it is; its reports close as dismissed."""
    check_csrf(request, csrf)
    target = moderation_safe_back(back)
    found = await _moderation_target(conn, target_type, target_id)
    ids = await resolve_reports(
        conn, target_type, target_id, status=DISMISSED, resolution="dismissed"
    )
    if not ids:
        return _nothing_open(request, target)
    entry = await admin_audit.record_change(
        conn,
        REPORTS_DISMISS,
        ip=client_ip(request),
        entity=target_type,
        entity_id=target_id,
        entity_label=found.label,
        before={"status": "open"},
        after={"status": "dismissed"},
        extra={"reports": len(ids), "report_ids": ids, **(found.details or {})},
    )
    return _decided(
        request,
        "Одобрено: жалобы закрыты, контент остаётся",
        entry,
        target,
        moderation_item_key(target_type, target_id),
    )


@router.post("/moderation/{target_type}/{target_id}/hide", response_model=None, dependencies=WRITES)
async def admin_moderation_hide(
    request: Request,
    target_type: TargetType,
    target_id: TargetId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
    bio: Annotated[str, Form(max_length=5)] = "",
    avatar: Annotated[str, Form(max_length=5)] = "",
    back: Back = "",
) -> Response:
    """«Скрыть»: a comment is hidden from readers (PR 350 - its author still sees it,
    marked); a profile has «о себе» and/or the avatar cleared (the ticked ones). The
    reports close as actioned."""
    check_csrf(request, csrf)
    target = moderation_safe_back(back)
    found = await _moderation_target(conn, target_type, target_id)
    if found.author_id is None:
        push_toast(request, "Объекта больше нет — жалобы можно только закрыть", tone="error")
        return RedirectResponse(target, status_code=303)
    if target_type == "comment":
        if found.comment is not None and found.comment.status == COMMENT_DELETED:
            push_toast(request, "Автор уже удалил комментарий — закройте жалобы", tone="error")
            return RedirectResponse(target, status_code=303)
        hidden_at = await hide_comment(conn, target_id)
        ids = await resolve_reports(
            conn, target_type, target_id, status=ACTIONED, resolution="comment_hidden"
        )
        if not ids:
            if hidden_at is not None:
                await show_comment(conn, target_id)
            return _nothing_open(request, target)
        entry = await admin_audit.record_change(
            conn,
            REPORTS_HIDE_COMMENT,
            ip=client_ip(request),
            entity="comment",
            entity_id=target_id,
            entity_label=found.label,
            # No hidden_at «after» when it was hidden already: «Отменить» then leaves it so.
            before={"hidden_at": None} if hidden_at else None,
            after={"hidden_at": hidden_at} if hidden_at else None,
            extra={"reports": len(ids), "report_ids": ids, **(found.details or {})},
        )
        message = (
            "Комментарий скрыт от читателей"
            if hidden_at
            else "Комментарий уже был скрыт — жалобы закрыты"
        )
        return _decided(
            request, message, entry, target, moderation_item_key(target_type, target_id)
        )
    if bio != "1" and avatar != "1":
        push_toast(request, "Не очищено: отметьте «о себе» или аватар", tone="error")
        return RedirectResponse(target, status_code=303)
    cleared = await clear_profile(conn, target_id, bio=bio == "1", avatar=avatar == "1")
    if not cleared:
        push_toast(request, "Не очищено: в профиле нечего убирать", tone="error")
        return RedirectResponse(target, status_code=303)
    ids = await resolve_reports(
        conn, target_type, target_id, status=ACTIONED, resolution="profile_cleared"
    )
    if not ids:
        await restore_profile(conn, target_id, cleared)
        return _nothing_open(request, target)
    entry = await admin_audit.record_change(
        conn,
        REPORTS_CLEAR_PROFILE,
        ip=client_ip(request),
        entity="profile",
        entity_id=target_id,
        entity_label=found.label,
        before=cleared,
        after=dict.fromkeys(cleared),
        extra={"reports": len(ids), "report_ids": ids},
    )
    what = " и ".join(
        name for key, name in (("bio", "«о себе»"), ("avatar_path", "аватар")) if key in cleared
    )
    return _decided(
        request,
        f"Профиль «{found.label}»: {what} очищено",
        entry,
        target,
        moderation_item_key(target_type, target_id),
    )


@router.post("/moderation/{target_type}/{target_id}/ban", response_model=None, dependencies=WRITES)
async def admin_moderation_ban(
    request: Request,
    target_type: TargetType,
    target_id: TargetId,
    _: Admin,
    conn: Connection,
    csrf: Csrf = "",
    term: Annotated[str, Form(max_length=10)] = "",
    reason: Annotated[str, Form(max_length=2000)] = "",
    back: Back = "",
) -> Response:
    """«Заблокировать автора» (PR 349's block: a term and an optional reason the person
    sees). The object itself is left as it is; the reports close as actioned."""
    check_csrf(request, csrf)
    target = moderation_safe_back(back)
    found = await _moderation_target(conn, target_type, target_id)
    if found.author_id is None:
        push_toast(request, "Объекта больше нет — жалобы можно только закрыть", tone="error")
        return RedirectResponse(target, status_code=303)
    if term not in BAN_TERMS:
        push_toast(request, "Не заблокировано: выберите срок", tone="error")
        return RedirectResponse(target, status_code=303)
    try:
        ban = await ban_user(conn, found.author_id, term=term, reason=reason)
    except AlreadyBanned:
        push_toast(request, f"«{found.author}» уже заблокирован", tone="error")
        return RedirectResponse(target, status_code=303)
    ids = await resolve_reports(
        conn, target_type, target_id, status=ACTIONED, resolution="author_banned"
    )
    if not ids:
        await lift_ban(conn, found.author_id)
        return _nothing_open(request, target)
    entry = await admin_audit.record_change(
        conn,
        REPORTS_BAN_AUTHOR,
        ip=client_ip(request),
        entity=target_type,
        entity_id=target_id,
        entity_label=found.label,
        before={"banned": False},
        after={"banned": True, "expires_at": ban.expires_at},
        extra={
            "author_id": found.author_id,
            "author": found.author,
            "term": term,
            "reason": ban.reason,
            "ban_id": ban.id,
            "reports": len(ids),
            "report_ids": ids,
        },
    )
    return _decided(
        request,
        f"«{found.author}» заблокирован ({dict(BAN_TERM_LABELS)[term]})",
        entry,
        target,
        moderation_item_key(target_type, target_id),
    )


async def _moderation_card(
    conn: AsyncConnection, target_type: str, target_id: int, tz: str
) -> ModerationCard | None:
    """One object's card, or None when nothing about it is open any more (already
    decided, or a stale link)."""
    reports = await object_reports(conn, target_type, target_id)
    if not reports.count:
        return None
    if target_type == "comment":
        detail = await comment_detail(conn, target_id)
        quote = None
        titles: dict[str, TitleSummary] = {}
        if detail:
            row = detail.comment
            titles = await _title_summaries([row.slug_url])
            quote = await paragraph_text(
                row.slug_url, row.volume, row.number, row.branch_id, row.paragraph_index
            )
        card = comment_card(detail, target_id, reports, titles, quote, tz)
    else:
        profile = await reported_profile(conn, target_id)
        card = profile_card(profile, target_id, reports, tz)
    if card.author_id is None:
        return card
    record = await author_record(conn, card.author_id)
    bans = await _read(conn, lambda: _bans(conn, card.author_id, tz))
    ban = f"заблокирован {bans.until}" if bans.active else None
    return moderation_with_history(card, record, bans.total, ban, tz)


# How many of the latest failed downloads «Система» lists.
DOWNLOAD_ERRORS_SHOWN = 10


@router.get("/system", response_model=None)
async def admin_system(request: Request, _: Admin) -> HTMLResponse:
    """«Система» (PR 351): this process, its versions and the services it depends on -
    measured on each visit, since the process started (no external monitoring). The
    connection is taken here, not as a dependency, so with the database down the page
    still opens and says so."""
    tz = get_settings().admin_timezone
    database: DatabaseStatus | None = None
    recent: Overview | None = None
    timed_out = False
    try:
        async with db_connection.connection() as conn:
            database = await database_status(conn)
            recent = await overview(conn, recent=DOWNLOAD_ERRORS_SHOWN)
    except QueryCanceled:
        logger.warning("The admin system page's database check timed out")
        timed_out = True
    except (psycopg.OperationalError, PoolTimeout):
        logger.exception("The admin system page could not reach the database")
    services = [
        app_service(STARTED_AT, uptime_seconds(), tz),
        database_service(database, db_connection.pool_stats(), timed_out),
        ranobelib_service(await probe_ranobelib(), tz),
    ]
    window = request_samples()
    requests = summarize(window)
    settings = get_settings()
    directories = [
        ("Кеш SDK", settings.cache_dir),
        ("Аватарки", settings.avatar_dir),
        ("Вложения комментариев", settings.comment_attachment_dir),
    ]
    usages = [(label, path, await directory_usage(path)) for label, path in directories]
    return _page(
        request,
        "admin/system.html",
        "system",
        timezone=tz,
        checked_at=f"{_server_now():%H:%M}",
        services=services,
        versions=versions(app_version(), sdk_version(), python_version()),
        requests=requests,
        request_kpis=request_kpis(requests, tz),
        latency=latency_chart(per_minute(window, time.time()), tz),
        sample_limit=REQUEST_SAMPLES,
        jobs=job_rows(list_active_jobs(), time.time()),
        job_columns=JOB_COLUMNS,
        database_down=recent is None,
        download_errors=download_error_rows(recent.recent_download_errors, tz) if recent else [],
        error_columns=DOWNLOAD_ERROR_COLUMNS,
        errors_shown=DOWNLOAD_ERRORS_SHOWN,
        storage=storage_rows(usages),
        disk=disk_summary(disk_usage(settings.cache_dir)),
        cache_clear_issue=SDK_CACHE_CLEAR_ISSUE,
    )


@router.get("/tables", response_model=None)
async def admin_tables(request: Request, _: Admin, conn: Connection) -> HTMLResponse:
    try:
        tables = await list_tables(conn)
    except QueryCanceled:
        return _timed_out(request, "tables")
    return _page(
        request,
        "admin/tables.html",
        "tables",
        columns=TABLE_LIST_COLUMNS,
        rows=table_list_rows(tables),
    )


@router.get("/tables/{name}", response_model=None)
async def admin_table(
    request: Request,
    name: TableName,
    _: Admin,
    conn: Connection,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    sort: SortKey = None,
    order: Order = "desc",
    q: Annotated[str, Query(max_length=200)] = "",
) -> HTMLResponse:
    columns = (await table_columns(conn)).get(name)
    if columns is None:
        raise HTTPException(status_code=404)
    try:
        data = await browse_table(
            conn, name, columns, page=page, sort=sort, descending=order != "asc", query=q
        )
        links = (await user_id_columns(conn)).get(name, set())
    except QueryCanceled:
        return _timed_out(request, "tables")
    return _page(
        request,
        "admin/table.html",
        "tables",
        data=data,
        mask=MASK,
        args=dict(request.query_params),
        columns=browse_columns(data),
        table_id=table_id(name),
        per_page=TABLE_PAGE_SIZE,
        preview=CELL_PREVIEW,
        csv_href=csv_href(name, request.query_params),
        user_links=links - data.secret,
        export_limit=EXPORT_LIMIT,
    )


@router.get("/tables/{name}/csv", response_model=None, dependencies=EXPORTS)
async def admin_table_csv(
    request: Request,
    name: TableName,
    _: Admin,
    conn: Connection,
    sort: SortKey = None,
    order: Order = "desc",
    q: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    """PR 352: the browser's current selection (its search and sort, every page up to
    EXPORT_LIMIT rows) as CSV - built from the same whitelist and select list as the page,
    so secrets are masked here too. Recorded in the action log: nothing changes, but data
    leaves the panel."""
    columns = (await table_columns(conn)).get(name)
    if columns is None:
        raise HTTPException(status_code=404)
    try:
        export = await export_table(
            conn, name, columns, sort=sort, descending=order != "asc", query=q
        )
    except QueryCanceled:
        return _timed_out(request, "tables")
    await admin_audit.record_admin_action(
        conn,
        DATA_EXPORT,
        ip=client_ip(request),
        entity="table",
        entity_id=name,
        entity_label=name,
        details={
            "rows": len(export.rows),
            "matching": export.total,
            "query": q.strip() or None,
            "sort": sort,
            "order": "asc" if order == "asc" else "desc",
        },
    )
    return Response(
        table_csv(export, MASK),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{csv_filename(name, _server_now())}"'
        },
    )


@router.get("/audit", response_model=None)
async def admin_audit_log(
    request: Request,
    _: Admin,
    conn: Connection,
    q: Annotated[str, Query(max_length=200)] = "",
    action: Annotated[str | None, Query(max_length=40)] = None,
    actor: Annotated[str | None, Query(max_length=40)] = None,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
) -> HTMLResponse:
    """«Журнал действий» (PR 353): the log PR 343 writes, newest first, with search by
    object or IP and the «Действие» filter. Read-only - the log has no edit or delete,
    here or anywhere."""
    settings = get_settings()
    tz = settings.admin_timezone
    try:
        names = await admin_audit.actors(conn)
        data = await admin_audit.list_page(
            conn, query=q, action=parse_action(action), actor=_known(actor, names), page=page
        )
    except QueryCanceled:
        return _timed_out(request, "audit")
    args = dict(request.query_params)
    filters = audit_filters(args, names)
    return _page(
        request,
        "admin/audit.html",
        "audit",
        args=args,
        filters=filters,
        filtered=bool(q.strip()) or any(item.value for item in filters),
        rows=audit_rows(data.entries, tz, _server_now()),
        data=data,
        per_page=admin_audit.PAGE_SIZE,
        retention_days=settings.admin_audit_retention_days,
        csv_href=audit_csv_href(args),
        export_limit=admin_audit.EXPORT_LIMIT,
        timezone=tz,
    )


def _known(actor: str | None, names: list[str]) -> str | None:
    return actor if actor in names else None


@router.get("/audit/csv", response_model=None, dependencies=EXPORTS)
async def admin_audit_csv(
    request: Request,
    _: Admin,
    conn: Connection,
    q: Annotated[str, Query(max_length=200)] = "",
    action: Annotated[str | None, Query(max_length=40)] = None,
    actor: Annotated[str | None, Query(max_length=40)] = None,
) -> Response:
    """The log's current selection (search and filters, every page up to EXPORT_LIMIT
    entries) as CSV. The export itself goes into the log, like «Данные БД»'s."""
    try:
        names = await admin_audit.actors(conn)
        export = await admin_audit.export_entries(
            conn, query=q, action=parse_action(action), actor=_known(actor, names)
        )
    except QueryCanceled:
        return _timed_out(request, "audit")
    await admin_audit.record_admin_action(
        conn,
        AUDIT_EXPORT,
        ip=client_ip(request),
        details={
            "rows": len(export.entries),
            "matching": export.total,
            "query": q.strip() or None,
            "action": parse_action(action),
            "actor": _known(actor, names),
        },
    )
    return Response(
        audit_csv(export.entries, get_settings().admin_timezone),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{audit_csv_filename(_server_now())}"'
        },
    )


# --- the global search (PR 353) -------------------------------------------------------------


async def _search(conn: AsyncConnection, query: str, limit: int) -> list[Group]:
    """Everything the top bar's search finds for ``query``: the panel's screens, accounts
    (email, nickname, id), comments (text, id) and «Данные БД»'s tables - never a hash or
    token column, and nothing from the SDK's catalog."""
    if not searchable(query):
        return []
    return groups(
        query,
        screens=screen_hits(query, ADMIN_NAV),
        users=user_hits(await search_users(conn, query, limit)),
        comments=comment_hits(await search_comments(conn, query, limit)),
        tables=table_hits(query, await table_columns(conn)),
        limit=limit,
    )


@router.get("/search", response_model=None)
async def admin_search(
    request: Request,
    _: Admin,
    conn: Connection,
    q: Annotated[str, Query(max_length=200)] = "",
) -> HTMLResponse:
    """The full results - where the box goes on Enter, and what it is without a script."""
    query = normalize(q)
    try:
        found = await _search(conn, query, PAGE_LIMIT)
    except QueryCanceled:
        return _timed_out(request, "search")
    return _page(
        request,
        "admin/search.html",
        "search",
        query=query,
        search_query=query,
        searchable=searchable(query),
        min_query=MIN_QUERY,
        groups=found,
    )


@router.get(
    "/search/suggest",
    response_model=None,
    dependencies=[Depends(require_admin_fetch)],
)
async def admin_search_suggest(
    conn: Connection, q: Annotated[str, Query(max_length=200)] = ""
) -> JSONResponse:
    """The drop-down under the box (js/admin-search.js): a few hits per group. Without
    the admin login it's a 404, like the panel's own URLs without ADMIN_PASSWORD."""
    query = normalize(q)
    try:
        found = await _search(conn, query, SUGGEST_LIMIT)
    except QueryCanceled:
        return JSONResponse({"query": query, "error": "timeout", "groups": []}, status_code=503)
    return JSONResponse(as_json(query, found))


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
    csrf: Csrf = "",
    action: Annotated[str, Form(max_length=20)] = "",
    confirm: Annotated[str, Form(max_length=5)] = "",
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
    password: Annotated[str, Form(max_length=1024)] = "",
    csrf: Csrf = "",
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
async def admin_logout(request: Request, csrf: Csrf = "") -> Response:
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
