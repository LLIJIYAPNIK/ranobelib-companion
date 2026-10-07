"""The "Активность" section: today's reading/download activity for the logged-in user."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from psycopg import AsyncConnection
from ranobelib import RanobeLibError

from app.auth.dependencies import get_current_user, require_current_user
from app.db.activity import (
    ChapterReadEvent,
    daily_active_seconds,
    daily_reading_activity,
    daily_titles_read,
    list_chapters_read_today,
    list_recent_chapter_reads,
    record_heartbeat,
    total_active_seconds_today,
)
from app.db.connection import connection, get_connection
from app.db.downloads import (
    DownloadHistoryEntry,
    count_downloads_since,
    list_download_history,
    list_download_history_today,
)
from app.db.users import User
from app.jobs.models import DownloadJob
from app.jobs.store import list_active_jobs_for_user
from app.services.client import open_client
from app.templating import templates
from app.timezones import day_start_utc, local_today, user_timezone

router = APIRouter(prefix="/activity")

_MAX_HEARTBEAT_SECONDS = 60
"""Clamp on a single tick, matching activity-heartbeat.js's own interval (see
app/static/js/activity-heartbeat.js) - keeps a single request from inflating a user's own
active-time stat past what one real interval could ever produce."""


@dataclass(frozen=True)
class ReadToday:
    slug_url: str
    chapters_read: int
    name: str | None
    cover_url: str | None


@dataclass(frozen=True)
class ActivitySummary:
    read_today: list[ReadToday]
    chapters_read_today: int
    active_time_label: str
    active_jobs: list[DownloadJob]
    downloads_today: list[DownloadHistoryEntry]


async def build_activity_summary(user: User, conn: AsyncConnection) -> ActivitySummary:
    """Everything the "Активность" page (see the upcoming GET /activity route) shows for
    today - "today" meaning the user's own calendar day (PR 322, users.timezone; UTC
    when unset), matching app/db/activity.py."""
    tz = user_timezone(user.timezone)
    read_today = await _read_today_items(user, conn, tz)
    return ActivitySummary(
        read_today=read_today,
        chapters_read_today=sum(item.chapters_read for item in read_today),
        active_time_label=_format_active_time(await total_active_seconds_today(conn, user.id, tz)),
        active_jobs=list_active_jobs_for_user(user.id),
        downloads_today=await list_download_history_today(conn, user.id, tz),
    )


Period = Literal["today", "7d", "30d"]

PERIOD_LABELS: dict[str, str] = {"today": "Сегодня", "7d": "7 дней", "30d": "30 дней"}
_PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30}
_CHART_DAYS = 30
_EVENTS_LIMIT = 8
_MONTHS_SHORT = (
    "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"
)  # fmt: skip
_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


@dataclass(frozen=True)
class ActivityMetric:
    label: str
    value: str
    unit: str
    note: str
    accent: bool = False


@dataclass(frozen=True)
class ChartDay:
    day: date
    minutes: int
    height_percent: int
    is_today: bool
    tooltip: str


@dataclass(frozen=True)
class ActivityEvent:
    kind: Literal["read", "download", "download_error"]
    title: str
    sub: str
    time_label: str
    href: str
    sort_key: str


@dataclass(frozen=True)
class ReadTodayHighlight:
    item: ReadToday
    volume: str | None
    number: str | None


@dataclass(frozen=True)
class ActivityOverview:
    """The Webnovells Redesign Активность page (PR 276, screen A4), composed from the
    existing activity_events/download_history data - `summary` is the same
    build_activity_summary() the home page uses for "today"."""

    period: str
    summary: ActivitySummary
    metrics: list[ActivityMetric]
    chart_days: list[ChartDay]
    chart_axis: list[tuple[str, bool]]
    chart_total_label: str
    chart_reading_days: int
    read_today_highlight: ReadTodayHighlight | None
    last_download_label: str | None
    events: list[ActivityEvent]


async def build_activity_overview(
    user: User, conn: AsyncConnection, period: Period
) -> ActivityOverview:
    summary = await build_activity_summary(user, conn)
    titles = _TitleLookup()
    for item in summary.read_today:
        titles.remember(item.slug_url, item.name, item.cover_url)

    tz = user_timezone(user.timezone)
    today = local_today(tz)
    period_start_day = today - timedelta(days=_PERIOD_DAYS[period] - 1)
    period_start = period_start_day.isoformat()
    chapters_by_day = await daily_reading_activity(conn, user.id, tz=tz)
    seconds_by_day = await daily_active_seconds(conn, user.id, tz=tz)
    titles_by_day = await daily_titles_read(conn, user.id, tz=tz)
    recent_reads = await list_recent_chapter_reads(conn, user.id, limit=_EVENTS_LIMIT)
    recent_downloads = await list_download_history(conn, user.id, limit=_EVENTS_LIMIT)
    last_download = recent_downloads[0] if recent_downloads else None

    chapters_in_period = sum(n for day, n in chapters_by_day.items() if day >= period_start)
    seconds_in_period = sum(n for day, n in seconds_by_day.items() if day >= period_start)
    if period == "today":
        downloads_in_period = len(summary.downloads_today)
    else:
        downloads_in_period = await count_downloads_since(
            conn, user.id, day_start_utc(period_start_day, tz)
        )

    # Titles read in the period, most recently read first.
    period_slugs: list[str] = []
    for day in sorted((d for d in titles_by_day if d >= period_start), reverse=True):
        for slug in titles_by_day[day]:
            if slug not in period_slugs:
                period_slugs.append(slug)
    if period_slugs:
        chapters_note = (await titles.get(period_slugs[0]))[0]
        if len(period_slugs) > 1:
            chapters_note += f" и ещё {len(period_slugs) - 1}"
    else:
        chapters_note = "Сегодня ещё ничего не читали" if period == "today" else "Ничего не читали"

    # A day counts as a reading day with either a chapter opened or reading time logged,
    # so this agrees with the chart (minutes) and the "Глав прочитано" card (chapters).
    year_days = len(
        {day for day, n in chapters_by_day.items() if n > 0}
        | {day for day, n in seconds_by_day.items() if n > 0}
    )
    metrics = [
        ActivityMetric("Глав прочитано", str(chapters_in_period), "", chapters_note, accent=True),
        ActivityMetric(
            "Активное чтение",
            *_split_active_time(seconds_in_period),
            "Только время с открытой главой",
        ),
        ActivityMetric(
            "Скачано",
            str(downloads_in_period),
            "",
            f"Последняя загрузка {_short_date(last_download.finished_at, tz)}"
            if last_download
            else "Загрузок ещё не было",
        ),
        ActivityMetric(
            "За последний год",
            *_split_active_time(sum(seconds_by_day.values())),
            f"{year_days} {_plural(year_days, 'день', 'дня', 'дней')} с чтением",
        ),
    ]

    chart_days, chart_axis = _chart(seconds_by_day, today)
    chart_seconds = sum(
        seconds_by_day.get((today - timedelta(days=i)).isoformat(), 0) for i in range(_CHART_DAYS)
    )

    highlight = None
    if summary.read_today:
        first = summary.read_today[0]
        latest = next((r for r in recent_reads if r.slug_url == first.slug_url), None)
        highlight = ReadTodayHighlight(
            item=first,
            volume=latest.volume if latest else None,
            number=latest.number if latest else None,
        )

    return ActivityOverview(
        period=period,
        summary=summary,
        metrics=metrics,
        chart_days=chart_days,
        chart_axis=chart_axis,
        chart_total_label="".join(_split_active_time(chart_seconds)),
        chart_reading_days=sum(1 for day in chart_days if day.minutes > 0),
        read_today_highlight=highlight,
        last_download_label=_long_date(last_download.finished_at, tz) if last_download else None,
        events=await _events(recent_reads, recent_downloads, titles, today, tz),
    )


@router.get("")
async def show_activity(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    period: Period = "today",
) -> HTMLResponse:
    """Same locked-screen gate as /library and /downloads (PR 22): viewing the page
    itself doesn't require an account - build_activity_overview() (which resolves each
    read title's name/cover through the SDK) only runs for a logged-in user, so this
    doesn't spend ranobelib.me API quota just to render for an anonymous visitor. conn is
    checked out below, not taken as a route-level Depends(get_connection) parameter, so an
    anonymous visitor never checks one out of the pool at all (see get_current_user()'s
    own docstring for the same reasoning).

    `period` (PR 276) switches only the four metric cards - the chart always covers the
    last 30 days and the event feed is simply the latest events."""
    overview = None
    if user is not None:
        async with connection() as conn:
            overview = await build_activity_overview(user, conn, period)
    return templates.TemplateResponse(
        request,
        "activity.html",
        {
            "active_nav": "activity",
            "overview": overview,
            "summary": overview.summary if overview else None,
            "period": period,
            "period_labels": PERIOD_LABELS,
        },
    )


@router.post("/heartbeat", status_code=204)
async def heartbeat(
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    slug_url: Annotated[str, Form()],
    seconds: Annotated[int, Form(gt=0, le=_MAX_HEARTBEAT_SECONDS)],
) -> Response:
    await record_heartbeat(conn, user.id, slug_url, seconds)
    return Response(status_code=204)


async def _read_today_items(user: User, conn: AsyncConnection, tz: str) -> list[ReadToday]:
    """Each read-today title's display name/cover, fetched fresh through the SDK - same
    approach as `_library_items()` in app/api/library.py, for the same reason (avoid a
    second cache of SDK response data, see CLAUDE.md, "Архитектура"). A title that's
    gone/unreachable on ranobelib.me still shows up, just with its slug_url as a fallback
    label and no cover.
    """
    items: list[ReadToday] = []
    for count in await list_chapters_read_today(conn, user.id, tz):
        name: str | None = None
        cover_url: str | None = None
        try:
            async with open_client(count.slug_url) as lib:
                title = await lib.get_info()
            name = title.rus_name or title.name
            cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
        except RanobeLibError:
            pass
        items.append(
            ReadToday(
                slug_url=count.slug_url,
                chapters_read=count.chapters_read,
                name=name,
                cover_url=cover_url,
            )
        )
    return items


def _format_active_time(seconds: int) -> str:
    minutes = seconds // 60
    if minutes == 0:
        return "< 1 мин"
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _split_active_time(seconds: int) -> tuple[str, str]:
    """_format_active_time() split into the big number and its smaller unit for the
    metric cards: "0"/" мин", "<1"/" мин", "19"/" мин", "1 ч 30"/" мин". Unlike
    _format_active_time() ("< 1 мин" for "today" on the home page), no time at all is
    "0" here - these cards also cover whole periods with nothing in them."""
    if seconds == 0:
        return "0", " мин"
    minutes = seconds // 60
    if minutes == 0:
        return "<1", " мин"
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes}", " мин"
    return str(minutes), " мин"


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _parse_utc(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _local(timestamp: str, tz: str) -> datetime:
    """A stored UTC timestamp on the user's own clock (PR 322)."""
    return _parse_utc(timestamp).astimezone(ZoneInfo(tz))


def _short_date(timestamp: str, tz: str) -> str:
    return _local(timestamp, tz).strftime("%d.%m")


def _long_date(timestamp: str, tz: str) -> str:
    day = _local(timestamp, tz).date()
    return f"{day.day} {_MONTHS_GENITIVE[day.month - 1]}"


def _event_time(timestamp: str, today: date, tz: str) -> str:
    """The time column of "История событий": "Сегодня · 14:05", "Вчера · 09:12" or
    "06.09 · 19:17" - on the user's own clock and calendar (PR 322), the same days the
    stats above it count in."""
    moment = _local(timestamp, tz)
    clock = moment.strftime("%H:%M")
    if moment.date() == today:
        return f"Сегодня · {clock}"
    if moment.date() == today - timedelta(days=1):
        return f"Вчера · {clock}"
    return f"{moment.strftime('%d.%m')} · {clock}"


def _chart(
    seconds_by_day: dict[str, int], today: date
) -> tuple[list[ChartDay], list[tuple[str, bool]]]:
    """The last 30 days as bars scaled to the busiest one, plus an axis label every
    week ending with "Сегодня". Minutes round up, so any reading at all shows a bar."""
    days = [today - timedelta(days=offset) for offset in range(_CHART_DAYS - 1, -1, -1)]
    minutes = [-(-seconds_by_day.get(day.isoformat(), 0) // 60) for day in days]
    peak = max(minutes) or 1
    chart = [
        ChartDay(
            day=day,
            minutes=m,
            height_percent=round(m * 100 / peak),
            is_today=day == today,
            tooltip=f"{_day_month(day)}: " + (f"{m} мин" if m else "не читали"),
        )
        for day, m in zip(days, minutes, strict=True)
    ]
    axis = [(_day_month(days[i]), False) for i in range(0, _CHART_DAYS - 2, 7)]
    axis.append(("Сегодня", True))
    return chart, axis


def _day_month(day: date) -> str:
    return f"{day.day} {_MONTHS_SHORT[day.month - 1]}"


class _TitleLookup:
    """Each title's display name/cover (and table of contents, for chapter names)
    resolved through the SDK at most once per page - the same lookup _read_today_items()
    does, shared by the metric note and the event feed instead of repeated per event."""

    def __init__(self) -> None:
        self._titles: dict[str, tuple[str, str | None]] = {}
        self._chapter_names: dict[str, dict[tuple[str, str], str]] = {}

    def remember(self, slug_url: str, name: str | None, cover_url: str | None) -> None:
        self._titles[slug_url] = (name or slug_url, cover_url)

    async def get(self, slug_url: str) -> tuple[str, str | None]:
        if slug_url not in self._titles:
            name: str | None = None
            cover_url: str | None = None
            try:
                async with open_client(slug_url) as lib:
                    title = await lib.get_info()
                name = title.rus_name or title.name
                cover_url = title.cover.default or title.cover.md or title.cover.thumbnail
            except RanobeLibError:
                pass
            self.remember(slug_url, name, cover_url)
        return self._titles[slug_url]

    async def chapter_name(self, slug_url: str, volume: str, number: str) -> str | None:
        if slug_url not in self._chapter_names:
            names: dict[tuple[str, str], str] = {}
            try:
                async with open_client(slug_url) as lib:
                    for vol in await lib.get_table_of_contents():
                        for chapter in vol.chapters:
                            if chapter.name:
                                names[(chapter.volume, chapter.number)] = chapter.name
            except RanobeLibError:
                pass
            self._chapter_names[slug_url] = names
        return self._chapter_names[slug_url].get((volume, number))


async def _events(
    reads: list[ChapterReadEvent],
    downloads: list[DownloadHistoryEntry],
    titles: _TitleLookup,
    today: date,
    tz: str,
) -> list[ActivityEvent]:
    """"История событий": the latest chapter reads and downloads, merged newest first."""
    events: list[ActivityEvent] = []
    for read in reads:
        name, _ = await titles.get(read.slug_url)
        sub = f"Том {read.volume}"
        if read.volume is not None and read.number is not None:
            chapter_name = await titles.chapter_name(read.slug_url, read.volume, read.number)
            if chapter_name:
                sub += f" · {chapter_name}"
        events.append(
            ActivityEvent(
                kind="read",
                title=f"Прочитана глава {read.number} · {name}",
                sub=sub,
                time_label=_event_time(read.created_at, today, tz),
                href=f"/titles/{read.slug_url}/chapters/{read.volume}/{read.number}",
                sort_key=_parse_utc(read.created_at).isoformat(),
            )
        )
    for entry in downloads:
        name, _ = await titles.get(entry.slug_url)
        fmt = entry.fmt.upper()
        count = entry.chapter_count
        chapters = f"{count} {_plural(count, 'глава', 'главы', 'глав')}" if count else fmt
        if entry.status == "error":
            kind: Literal["read", "download", "download_error"] = "download_error"
            title, sub = f"Не удалось скачать {fmt} · {name}", "Ошибка загрузки"
        elif entry.status == "cancelled":
            kind, title, sub = "download", f"Загрузка {fmt} отменена · {name}", chapters
        else:
            kind, title, sub = "download", f"Скачан {fmt} · {name}", chapters
        events.append(
            ActivityEvent(
                kind=kind,
                title=title,
                sub=sub,
                time_label=_event_time(entry.finished_at, today, tz),
                href=f"/titles/{entry.slug_url}",
                sort_key=_parse_utc(entry.finished_at).isoformat(),
            )
        )
    events.sort(key=lambda event: event.sort_key, reverse=True)
    return events[:_EVENTS_LIMIT]
