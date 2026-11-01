"""The "Активность" section: the reader's month of reading, friends' reading and what
people read on the site."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from psycopg import AsyncConnection
from ranobelib import RanobeLibError

from app.api.library import currently_reading_for_users
from app.api.profile import calendar_level
from app.auth.dependencies import get_current_user, require_current_user
from app.db.activity import (
    ChapterReadEvent,
    TitleDay,
    daily_active_seconds,
    daily_reading_activity,
    daily_title_activity,
    hourly_reading_seconds,
    last_chapter_read_at,
    list_chapters_read_today,
    list_recent_chapter_reads,
    reading_streak_days,
    record_heartbeat,
    site_top_titles,
    total_active_seconds_today,
)
from app.db.comments import RecentComment, list_recent_comments_by_user
from app.db.connection import connection, get_connection
from app.db.downloads import (
    DownloadHistoryEntry,
    list_download_history,
    list_download_history_today,
)
from app.db.friendships import FriendEntry, FriendUser, list_friends
from app.db.users import User, get_user_by_id
from app.jobs.models import DownloadJob
from app.jobs.store import list_active_jobs_for_user
from app.queued_events import EVENT_ID_PATTERN, MAX_EVENT_AGE_MS, occurred_at
from app.services.catalog import popular_titles
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


_EVENTS_LIMIT = 6
# How far back the month calendar goes (the activity queries' own 52-week window).
_MONTHS_BACK = 11
# Friends shown in full; the rest are a compact list under «Ещё N», capped (the full
# list is /friends).
_FRIENDS_LIMIT = 5
_FRIENDS_MORE_LIMIT = 60
# A friend who hasn't opened a chapter in this many days is «без активности».
_FRIEND_ACTIVE_DAYS = 30
# A top five: the usual length of a «top», and one row of covers.
_POPULAR_LIMIT = 5
# Below this many titles read on the site this week, «Читают на сайте» shows ranobelib.me's
# most viewed instead - a list of one or two isn't «what people read».
_POPULAR_MIN_SITE = 3
_POPULAR_DAYS = 7
# A friend's comment older than this is not «activity» any more.
_FRIEND_COMMENT_DAYS = 7
_MONTHS_NOMINATIVE = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)  # fmt: skip
_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_DAY_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"


@dataclass(frozen=True)
class ActivityEvent:
    kind: Literal["read", "download", "download_error"]
    title: str
    sub: str
    time_label: str
    href: str
    sort_key: str
    slug_url: str = ""
    cover_url: str | None = None


@dataclass(frozen=True)
class CalendarCell:
    """A day of the month calendar. `day` is None for the blank cells before the 1st and
    after the last day, which keep the grid in whole Monday-to-Sunday weeks."""

    day: date | None
    level: int = 0  # 0 (nothing) - 4, calendar_level() - the same scale as the profile's
    is_today: bool = False
    is_future: bool = False
    label: str = ""  # "14 октября: 4 главы, 23 мин"
    heading: str = ""  # "Сегодня", "Вчера", "14 октября" - the picked day's title


@dataclass(frozen=True)
class MonthCalendar:
    month: date  # its 1st
    title: str  # "Октябрь 2026"
    weeks: list[list[CalendarCell]]
    prev_month: str | None  # "2026-09", None at the window's start
    next_month: str | None  # None for the current month


@dataclass(frozen=True)
class HourBar:
    hour: int
    minutes: int
    height_percent: int  # against the busiest hour
    is_peak: bool
    tooltip: str  # "21:00–22:00 · 3 ч 10 мин"


@dataclass(frozen=True)
class ReadingHours:
    """How much this reader read in the month and when in the day: reading time by hour,
    and the part of the day that holds most of it."""

    time_label: str  # "12 ч 17 мин"
    chapters: int
    bars: list[HourBar]  # 24, from 0:00
    part_label: str | None  # "вечером" - None with no reading time
    part_share: int  # that part's share of the month's reading time, %
    peak_label: str | None  # "21:00–22:00"


@dataclass(frozen=True)
class DayTitle:
    slug_url: str
    name: str
    cover_url: str | None
    chapters: int
    time_label: str | None


@dataclass(frozen=True)
class DayReading:
    """What was read on one day of the calendar - shown under it when the day is picked."""

    day: date
    heading: str  # "Сегодня", "Вчера", "14 октября"
    titles: list[DayTitle]
    chapters: int
    time_label: str | None
    span_label: str | None  # when in the day: "09:12–14:05", "в 21:40"
    continue_href: str | None  # today only: the last chapter opened
    continue_number: str | None = None  # that chapter's number, for «Читать дальше · Глава N»


@dataclass(frozen=True)
class FriendReading:
    slug_url: str
    name: str
    cover_url: str | None
    chapter_label: str | None  # "Том 2 · глава 54"


@dataclass(frozen=True)
class FriendActivity:
    user: FriendUser
    reading: FriendReading | None  # None when nothing is being read or the friend hides it
    streak_days: int
    last_read_label: str | None  # "сегодня в 14:05", "вчера", "12 октября"
    comment: RecentComment | None


@dataclass(frozen=True)
class FriendBrief:
    """A friend past the first few: who and when they last read - no SDK lookups, so a
    long friends list stays cheap."""

    user: FriendUser
    last_read_label: str | None


@dataclass(frozen=True)
class PopularTitle:
    rank: int
    slug_url: str
    name: str
    cover_url: str | None
    readers: int | None  # None for ranobelib.me's list, which has no reader counts
    chapters: int | None
    share: int  # readers against the first title's, 0-100 - the row's bar


@dataclass(frozen=True)
class ActivityOverview:
    """«Активность»: the reader's month as a calendar of reading days, what was read on
    the picked day and when, how much and at what hours the month was read, friends'
    reading and what people read on the site - composed from activity_events, the
    friendships and the library, names and covers from the SDK."""

    summary: ActivitySummary
    streak_days: int
    read_today: bool
    today_time_label: str | None
    last_read_label: str | None  # the latest reading day, when it isn't today
    calendar: MonthCalendar
    hours: ReadingHours
    days: list[DayReading]  # every day of the month with reading, newest first
    selected: date | None
    selected_heading: str | None
    friends: list[FriendActivity] | None  # None: the reader turned the block off
    friends_more: list[FriendBrief]  # active past the first few, most recent first
    friends_idle: list[FriendBrief]  # no reading in _FRIEND_ACTIVE_DAYS days
    friends_total: int
    popular: list[PopularTitle]
    popular_source: Literal["site", "ranobelib"] | None
    events: list[ActivityEvent]


async def build_activity_overview(
    user: User, conn: AsyncConnection, month: date | None = None, day: date | None = None
) -> ActivityOverview:
    tz = user_timezone(user.timezone)
    today = local_today(tz)
    this_month = today.replace(day=1)
    first_month = _add_months(this_month, -_MONTHS_BACK)
    if day is not None:
        month = day.replace(day=1)
    month = min(max(month or this_month, first_month), this_month)
    month_end = _add_months(month, 1) - timedelta(days=1)

    summary = await build_activity_summary(user, conn)
    titles = _TitleLookup()
    for item in summary.read_today:
        titles.remember(item.slug_url, item.name, item.cover_url)

    # A reading day has a chapter opened or reading time logged (PR 323), here as on the
    # profile's heatmap - the streak and the calendar's marks count the same days.
    chapters_by_day = await daily_reading_activity(conn, user.id, tz=tz)
    seconds_by_day = await daily_active_seconds(conn, user.id, tz=tz)
    reading_days = {d for d, n in chapters_by_day.items() if n > 0} | {
        d for d, n in seconds_by_day.items() if n > 0
    }
    streak = reading_streak_days(dict.fromkeys(reading_days, 1), tz)
    month_titles = await daily_title_activity(conn, user.id, month, month_end, tz)
    recent_reads = await list_recent_chapter_reads(conn, user.id, limit=_EVENTS_LIMIT)
    recent_downloads = await list_download_history(conn, user.id, limit=_EVENTS_LIMIT)

    calendar = _month_calendar(month, month_end, month_titles, today, first_month)
    hours = _reading_hours(
        month_titles, await hourly_reading_seconds(conn, user.id, month, month_end, tz)
    )
    days = [
        await _day_reading(date.fromisoformat(key), entries, titles, today, recent_reads, tz)
        for key, entries in sorted(month_titles.items(), reverse=True)
    ]
    selected = _selected_day(day, days, month, today)

    last_reading_day = max(reading_days, default=None)
    friends, friends_more, friends_idle, friends_total = await _friends_activity(
        user, conn, today, tz
    )
    popular, popular_source = await _popular(conn, titles, today, tz)
    return ActivityOverview(
        summary=summary,
        streak_days=streak,
        read_today=today.isoformat() in reading_days,
        today_time_label=_minutes_label(seconds_by_day.get(today.isoformat(), 0)),
        last_read_label=(
            _day_heading(date.fromisoformat(last_reading_day), today).lower()
            if last_reading_day and last_reading_day != today.isoformat()
            else None
        ),
        calendar=calendar,
        hours=hours,
        days=days,
        selected=selected,
        selected_heading=_day_heading(selected, today) if selected else None,
        friends=friends,
        friends_more=friends_more,
        friends_idle=friends_idle,
        friends_total=friends_total,
        popular=popular,
        popular_source=popular_source,
        events=await _events(recent_reads, recent_downloads, titles, today, tz),
    )


@router.get("")
async def show_activity(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    month: Annotated[str | None, Query(pattern=_MONTH_PATTERN)] = None,
    day: Annotated[str | None, Query(pattern=_DAY_PATTERN)] = None,
) -> HTMLResponse:
    """Same locked-screen gate as /library and /downloads (PR 22): viewing the page
    itself doesn't require an account - build_activity_overview() (which resolves titles
    through the SDK) only runs for a logged-in user. conn is checked out below, not taken
    as a route-level Depends(get_connection), so an anonymous visitor never checks one out
    of the pool at all (see get_current_user()'s own docstring).

    `month` (YYYY-MM) picks the calendar's month, `day` (YYYY-MM-DD) the day shown under
    it - what the calendar's arrows and days link to without JS (activity-calendar.js
    switches days in place). Both are clamped to the calendar's own window."""
    overview = None
    if user is not None:
        picked_day = _parse_day(day)
        picked_month = date.fromisoformat(f"{month}-01") if month else None
        async with connection() as conn:
            overview = await build_activity_overview(user, conn, picked_month, picked_day)
    return templates.TemplateResponse(
        request,
        "activity.html",
        {
            "active_nav": "activity",
            "overview": overview,
            "weekdays": WEEKDAYS,
        },
    )


def _parse_day(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:  # 2026-02-31 passes the pattern
        raise HTTPException(status_code=422, detail="Неверная дата") from None


def _add_months(first: date, months: int) -> date:
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _month_calendar(
    month: date,
    month_end: date,
    month_titles: dict[str, list[TitleDay]],
    today: date,
    first_month: date,
) -> MonthCalendar:
    """The month in whole Monday-to-Sunday weeks. A day's level is calendar_level() of its
    chapters and seconds, as on the profile."""
    totals = {
        key: (sum(t.chapters for t in entries), sum(t.seconds for t in entries))
        for key, entries in month_titles.items()
    }
    read = set(totals)
    cells: list[CalendarCell] = [CalendarCell(day=None)] * month.weekday()
    current = month
    while current <= month_end:
        key = current.isoformat()
        chapters, seconds = totals.get(key, (0, 0))
        level = calendar_level(chapters, seconds) if key in read else 0
        label = f"{current.day} {_MONTHS_GENITIVE[current.month - 1]}"
        if level:
            label += f": {chapters} {_plural(chapters, 'глава', 'главы', 'глав')}"
            if seconds:
                label += f", {_minutes_label(seconds)}"
        cells.append(
            CalendarCell(
                day=current,
                level=level,
                is_today=current == today,
                is_future=current > today,
                label=label,
                heading=_day_heading(current, today),
            )
        )
        current += timedelta(days=1)
    cells += [CalendarCell(day=None)] * (-len(cells) % 7)
    this_month = today.replace(day=1)
    return MonthCalendar(
        month=month,
        title=f"{_MONTHS_NOMINATIVE[month.month - 1]} {month.year}",
        weeks=[cells[i : i + 7] for i in range(0, len(cells), 7)],
        prev_month=_add_months(month, -1).strftime("%Y-%m") if month > first_month else None,
        next_month=_add_months(month, 1).strftime("%Y-%m") if month < this_month else None,
    )


# Parts of the day for «Когда вы читаете», by local hour.
_DAY_PARTS = (("ночью", 0, 6), ("утром", 6, 12), ("днём", 12, 18), ("вечером", 18, 24))


def _reading_hours(month_titles: dict[str, list[TitleDay]], hours: list[int]) -> ReadingHours:
    """The month's reading time and chapters (the same days the calendar marks) and its
    reading time by hour of the day. The total is the sum of the hours shown, so the bars
    and the figure agree."""
    total = sum(hours)
    peak = max(hours)
    minutes = [-(-seconds // 60) for seconds in hours]  # any reading at all shows a bar
    bars = [
        HourBar(
            hour=hour,
            minutes=m,
            height_percent=round(seconds * 100 / peak) if peak else 0,
            is_peak=bool(peak) and seconds == peak,
            tooltip=f"{hour:02d}:00–{(hour + 1) % 24:02d}:00 · "
            + (_minutes_label(seconds) or "не читали"),
        )
        for hour, (seconds, m) in enumerate(zip(hours, minutes, strict=True))
    ]
    part_label, part_share = None, 0
    if total:
        name, start, end = max(_DAY_PARTS, key=lambda part: sum(hours[part[1] : part[2]]))
        part_label = name
        part_share = round(sum(hours[start:end]) * 100 / total)
    peak_hour = hours.index(peak) if peak else None
    return ReadingHours(
        time_label=_format_duration(total),
        chapters=sum(t.chapters for entries in month_titles.values() for t in entries),
        bars=bars,
        part_label=part_label,
        part_share=part_share,
        peak_label=(
            f"{peak_hour:02d}:00–{(peak_hour + 1) % 24:02d}:00" if peak_hour is not None else None
        ),
    )


async def _day_reading(
    day: date,
    entries: list[TitleDay],
    titles: _TitleLookup,
    today: date,
    recent_reads: list[ChapterReadEvent],
    tz: str,
) -> DayReading:
    items = []
    for entry in entries:
        name, cover_url = await titles.get(entry.slug_url)
        items.append(
            DayTitle(
                slug_url=entry.slug_url,
                name=name,
                cover_url=cover_url,
                chapters=entry.chapters,
                time_label=_minutes_label(entry.seconds),
            )
        )
    continue_href = continue_number = None
    if day == today and recent_reads:
        last = recent_reads[0]
        if last.volume is not None and last.number is not None:
            continue_href = f"/titles/{last.slug_url}/chapters/{last.volume}/{last.number}"
            continue_number = last.number
    return DayReading(
        day=day,
        heading=_day_heading(day, today),
        titles=items,
        chapters=sum(entry.chapters for entry in entries),
        time_label=_minutes_label(sum(entry.seconds for entry in entries)),
        span_label=_span_label(entries, tz),
        continue_href=continue_href,
        continue_number=continue_number,
    )


def _span_label(entries: list[TitleDay], tz: str) -> str | None:
    """When in the day the reading was: from the first event to the last, on the
    reader's clock."""
    firsts = [entry.first_at for entry in entries if entry.first_at]
    lasts = [entry.last_at for entry in entries if entry.last_at]
    if not firsts or not lasts:
        return None
    start = _local(min(firsts, key=_parse_utc), tz).strftime("%H:%M")
    end = _local(max(lasts, key=_parse_utc), tz).strftime("%H:%M")
    return f"в {start}" if start == end else f"{start}–{end}"


def _selected_day(
    picked: date | None, days: list[DayReading], month: date, today: date
) -> date | None:
    """The day shown under the calendar: the one picked, else today in the current month,
    else the month's latest reading day."""
    if picked is not None and picked.replace(day=1) == month and picked <= today:
        return picked
    if month == today.replace(day=1):
        return today
    return days[0].day if days else None


def _day_heading(day: date, today: date) -> str:
    if day == today:
        return "Сегодня"
    if day == today - timedelta(days=1):
        return "Вчера"
    label = f"{day.day} {_MONTHS_GENITIVE[day.month - 1]}"
    return label if day.year == today.year else f"{label} {day.year}"


def _minutes_label(seconds: int) -> str | None:
    if seconds <= 0:
        return None
    if seconds < 60:
        return "< 1 мин"
    return _format_duration(seconds)


def _format_duration(seconds: int) -> str:
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


async def _friends_activity(
    user: User, conn: AsyncConnection, today: date, tz: str
) -> tuple[list[FriendActivity] | None, list[FriendBrief], list[FriendBrief], int]:
    """Friends who read in the last _FRIEND_ACTIVE_DAYS days, most recent first: the
    first _FRIENDS_LIMIT in full (their book, streak, comment - an SDK lookup each), the
    rest as name and when they last read; and, apart, the friends without activity -
    up to _FRIENDS_MORE_LIMIT in each list. None when the reader turned friends' activity
    off for themselves (show_friends_activity_home, PR 202 - a preference about this
    reader's own screens). What a friend reads stays hidden when they hide it on their
    profile (show_currently_reading, PR 90/124)."""
    if not user.show_friends_activity_home:
        return None, [], [], 0
    friends = await list_friends(conn, user.id)
    if not friends:
        return [], [], [], 0
    last_read = await last_chapter_read_at(conn, [entry.user.id for entry in friends])
    friends.sort(key=lambda entry: last_read.get(entry.user.id, ""), reverse=True)
    active_since = day_start_utc(today - timedelta(days=_FRIEND_ACTIVE_DAYS - 1), tz)
    active = [
        entry
        for entry in friends
        if entry.user.id in last_read
        and _parse_utc(last_read[entry.user.id]).isoformat() >= active_since
    ]
    idle = [entry for entry in friends if entry not in active]
    shown = active[:_FRIENDS_LIMIT]
    reading_by_user = await currently_reading_for_users([entry.user.id for entry in shown], conn)
    comment_since = day_start_utc(today - timedelta(days=_FRIEND_COMMENT_DAYS - 1), tz)
    cards = []
    for entry in shown:
        friend = await get_user_by_id(conn, entry.user.id)
        friend_tz = user_timezone(friend.timezone if friend else None)
        reading = reading_by_user.get(entry.user.id)
        if friend is not None and not friend.show_currently_reading:
            reading = None
        comments = await list_recent_comments_by_user(conn, entry.user.id, limit=1)
        recent = [c for c in comments if _parse_utc(c.created_at).isoformat() >= comment_since]
        streak = reading_streak_days(
            await daily_reading_activity(conn, entry.user.id, tz=friend_tz), friend_tz
        )
        at = last_read.get(entry.user.id)
        cards.append(
            FriendActivity(
                user=entry.user,
                reading=_friend_reading(reading) if reading else None,
                streak_days=streak,
                last_read_label=_ago(at, today, tz) if at else None,
                comment=recent[0] if recent else None,
            )
        )

    def brief(entry: FriendEntry) -> FriendBrief:
        at = last_read.get(entry.user.id)
        return FriendBrief(user=entry.user, last_read_label=_ago(at, today, tz) if at else None)

    more = [brief(entry) for entry in active[_FRIENDS_LIMIT : _FRIENDS_LIMIT + _FRIENDS_MORE_LIMIT]]
    return cards, more, [brief(entry) for entry in idle[:_FRIENDS_MORE_LIMIT]], len(friends)


def _friend_reading(item: dict[str, object]) -> FriendReading:
    entry = item["entry"]
    volume = entry.last_read_volume  # type: ignore[attr-defined]
    number = entry.last_read_number  # type: ignore[attr-defined]
    slug_url = entry.slug_url  # type: ignore[attr-defined]
    return FriendReading(
        slug_url=slug_url,
        name=str(item["name"] or slug_url),
        cover_url=item["cover_url"],  # type: ignore[arg-type]
        chapter_label=f"Том {volume} · глава {number}" if volume and number else None,
    )


def _ago(timestamp: str, today: date, tz: str) -> str:
    moment = _local(timestamp, tz)
    if moment.date() == today:
        return f"сегодня в {moment.strftime('%H:%M')}"
    return _day_heading(moment.date(), today).lower()


async def _popular(
    conn: AsyncConnection, titles: _TitleLookup, today: date, tz: str
) -> tuple[list[PopularTitle], Literal["site", "ranobelib"] | None]:
    """What people on the site read this week, by how many of them opened a chapter;
    ranobelib.me's most viewed while the site's own list is too short to mean much."""
    since = day_start_utc(today - timedelta(days=_POPULAR_DAYS - 1), tz)
    rows = await site_top_titles(conn, since, _POPULAR_LIMIT)
    if len(rows) >= _POPULAR_MIN_SITE:
        top = rows[0].readers or 1
        items = []
        for rank, row in enumerate(rows, start=1):
            name, cover_url = await titles.get(row.slug_url)
            items.append(
                PopularTitle(
                    rank=rank,
                    slug_url=row.slug_url,
                    name=name,
                    cover_url=cover_url,
                    readers=row.readers,
                    chapters=row.chapters,
                    share=round(row.readers * 100 / top),
                )
            )
        return items, "site"
    viewed = await popular_titles(_POPULAR_LIMIT)
    if not viewed:
        return [], None
    return [
        PopularTitle(
            rank=rank,
            slug_url=title.slug_url,
            name=title.rus_name or title.name,
            cover_url=title.cover.default or title.cover.md or title.cover.thumbnail,
            readers=None,
            chapters=None,
            share=0,
        )
        for rank, title in enumerate(viewed, start=1)
    ], "ranobelib"


@router.post("/heartbeat", status_code=204)
async def heartbeat(
    user: Annotated[User, Depends(require_current_user)],
    conn: Annotated[AsyncConnection, Depends(get_connection)],
    slug_url: Annotated[str, Form()],
    seconds: Annotated[int, Form(gt=0, le=_MAX_HEARTBEAT_SECONDS)],
    event_id: Annotated[
        str | None, Form(min_length=8, max_length=64, pattern=EVENT_ID_PATTERN)
    ] = None,
    age_ms: Annotated[int | None, Form(ge=0, le=MAX_EVENT_AGE_MS)] = None,
) -> Response:
    """PR 332: a tick read offline arrives later from the device's queue (sync-queue.js)
    with its `event_id` - a resend of the same one is ignored - and `age_ms`, so its
    seconds land on the day they were read."""
    await record_heartbeat(conn, user.id, slug_url, seconds, event_id, occurred_at(age_ms))
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
    """«Недавно»: the latest chapter reads and downloads, merged newest first."""
    events: list[ActivityEvent] = []
    for read in reads:
        name, cover_url = await titles.get(read.slug_url)
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
                slug_url=read.slug_url,
                cover_url=cover_url,
            )
        )
    for entry in downloads:
        name, cover_url = await titles.get(entry.slug_url)
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
                slug_url=entry.slug_url,
                cover_url=cover_url,
            )
        )
    events.sort(key=lambda event: event.sort_key, reverse=True)
    return events[:_EVENTS_LIMIT]
