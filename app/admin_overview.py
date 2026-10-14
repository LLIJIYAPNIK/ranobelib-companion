"""«Обзор» (PR 345): what the admin overview shows, shaped from the metrics layer
(app/db/admin_stats.py) for admin/index.html - the period switch, the KPI cards, the
recent rows. Presentation only, like app/admin_kit.py: the numbers come from app/db/.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.admin_kit import (
    Column,
    Delta,
    LineChart,
    Sparkline,
    admin_query,
    delta,
    format_number,
    line_chart,
    sparkline,
)
from app.db.admin_stats import DEFAULT_PERIOD, PERIODS, PeriodStats

PERIOD_LABELS = {"24h": "24 часа", "7d": "7 дней", "30d": "30 дней", "90d": "90 дней"}

_MONTHS_SHORT = (
    "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"
)  # fmt: skip


@dataclass(frozen=True)
class MetricInfo:
    label: str
    icon: str


# The six KPIs, in the order of app/db/admin_stats.METRICS.
METRIC_INFO = {
    "active_readers": MetricInfo("Активные читатели", "users"),
    "chapters_read": MetricInfo("Прочитано глав", "book-open"),
    "reading_seconds": MetricInfo("Время чтения", "clock"),
    "registrations": MetricInfo("Регистрации", "user-plus"),
    "downloads": MetricInfo("Загрузки", "download"),
    "comments": MetricInfo("Комментарии", "comments"),
}


def format_duration(seconds: float) -> str:
    """Reading time: «3 ч 20 мин», «45 мин», «< 1 мин»."""
    minutes = round(seconds / 60)
    if seconds > 0 and minutes == 0:
        return "< 1 мин"
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{format_number(hours)} ч {minutes} мин" if minutes else f"{format_number(hours)} ч"
    return f"{minutes} мин"


def format_metric(key: str, value: float) -> str:
    return format_duration(value) if key == "reading_seconds" else format_number(value)


def format_when(timestamp: str | None, tz: str) -> str | None:
    """A stored UTC ISO timestamp on the admin's clock: «18 мар 2026, 14:25»."""
    if not timestamp:
        return None
    try:
        local = datetime.fromisoformat(timestamp).astimezone(ZoneInfo(tz))
    except ValueError:
        return timestamp
    return f"{local.day} {_MONTHS_SHORT[local.month - 1]} {local.year}, {local:%H:%M}"


# --- the period switch --------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodLink:
    key: str
    label: str
    href: str
    current: bool


def period_links(args: Mapping[str, object], current: str) -> list[PeriodLink]:
    """24ч · 7д · 30д · 90д - links that keep the rest of the page's state (metric,
    comparison); the default period isn't spelled out in the URL."""
    return [
        PeriodLink(
            key=key,
            label=key.replace("h", " ч").replace("d", " д"),
            href=admin_query(args, period=None if key == DEFAULT_PERIOD else key),
            current=key == current,
        )
        for key in PERIODS
    ]


# --- KPIs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Kpi:
    key: str
    label: str
    icon: str
    value: str
    change: Delta | None
    previous: str
    spark: Sparkline | None


def kpis(stats: PeriodStats) -> list[Kpi]:
    """One card per metric: the period's total, its change against the previous period
    (no percentage from zero - app/admin_kit.py delta()) and the trend over the buckets."""
    before = f"за прошлые {PERIOD_LABELS[stats.period.key]}"
    cards = []
    for key, series in stats.metrics.items():
        info = METRIC_INFO[key]
        comparison = series.comparison
        cards.append(
            Kpi(
                key=key,
                label=info.label,
                icon=info.icon,
                value=format_metric(key, comparison.current),
                change=delta(comparison.current, comparison.previous),
                previous=f"{before}: {format_metric(key, comparison.previous)}",
                spark=sparkline([float(v) for v in series.current]),
            )
        )
    return cards


# --- the activity chart -------------------------------------------------------------------

DEFAULT_METRIC = "active_readers"


def parse_metric(raw: str | None) -> str:
    return raw if raw in METRIC_INFO else DEFAULT_METRIC


def parse_compare(raw: str | None) -> bool:
    """The comparison is on unless the link turned it off (compare=0)."""
    return raw != "0"


@dataclass(frozen=True)
class MetricTab:
    key: str
    label: str
    value: str
    change: Delta | None
    href: str
    current: bool


def metric_tabs(stats: PeriodStats, args: Mapping[str, object], current: str) -> list[MetricTab]:
    """The chips over the chart: each metric's total and change; picking one redraws the
    chart with it. One metric per chart - six series on different scales on one axis
    would mislead (ROADMAP.md, wave 40)."""
    tabs = []
    for key, series in stats.metrics.items():
        comparison = series.comparison
        tabs.append(
            MetricTab(
                key=key,
                label=METRIC_INFO[key].label,
                value=format_metric(key, comparison.current),
                change=delta(comparison.current, comparison.previous),
                href=admin_query(args, metric=None if key == DEFAULT_METRIC else key),
                current=key == current,
            )
        )
    return tabs


def compare_href(args: Mapping[str, object], compare: bool) -> str:
    return admin_query(args, compare="0" if compare else None)


def _with_unit(unit: str) -> Callable[[float], str]:
    return lambda value: f"{format_number(round(value, 1))} {unit}"


def _bucket_label(moment: datetime, granularity: str) -> str:
    if granularity == "hour":
        return f"{moment:%H:%M}"
    return f"{moment.day} {_MONTHS_SHORT[moment.month - 1]}"


def activity_chart(stats: PeriodStats, metric: str, compare: bool) -> LineChart:
    """«Активность чтения»: `metric` per hour (24h) or day, the previous period dashed
    on the same scale when `compare`. Reading time is drawn in minutes or hours, so the
    axis gets round steps."""
    series = stats.metrics[metric]
    current: list[float] = [float(v) for v in series.current]
    previous: list[float] = [float(v) for v in series.previous]
    fmt: Callable[[float], str] = format_number
    if metric == "reading_seconds":
        divisor, unit = (3600, "ч") if max([*current, *previous, 0]) >= 3 * 3600 else (60, "мин")
        current = [v / divisor for v in current]
        previous = [v / divisor for v in previous]
        fmt = _with_unit(unit)
    return line_chart(
        current,
        [_bucket_label(label, stats.period.granularity) for label in stats.period.labels],
        name=METRIC_INFO[metric].label,
        previous=previous if compare else None,
        previous_name=f"Прошлые {PERIOD_LABELS[stats.period.key]}",
        fmt=fmt,
    )


def as_of(stats: PeriodStats, tz: str) -> str:
    """When the numbers were computed, on the admin's clock - they may be up to a minute
    old (app/db/admin_stats.py CACHE_SECONDS)."""
    return f"{stats.at.astimezone(ZoneInfo(tz)):%H:%M}"


# --- recent rows (PR 325's tables, on the kit) --------------------------------------------

RECENT_USER_COLUMNS = (
    Column("id", "id", "mono", sortable=False),
    Column("email", "Email", sortable=False),
    Column("nickname", "Никнейм", sortable=False),
    Column("created_at", "Зарегистрирован", "muted", sortable=False),
    Column("verified", "Email", sortable=False),
)

DOWNLOAD_ERROR_COLUMNS = (
    Column("email", "Пользователь", sortable=False),
    Column("slug_url", "Тайтл", "mono", sortable=False),
    Column("fmt", "Формат", sortable=False),
    Column("error", "Ошибка", sortable=False),
    Column("finished_at", "Когда", "muted", sortable=False),
)


def recent_user_rows(rows: list[Mapping[str, object]], tz: str) -> list[dict[str, object]]:
    return [
        {
            "id": row["id"],
            "email": row["email"],
            "nickname": row["nickname"],
            "created_at": format_when(row["created_at"], tz),  # type: ignore[arg-type]
            "verified": bool(row["email_verified_at"]),
        }
        for row in rows
    ]


def download_error_rows(rows: list[Mapping[str, object]], tz: str) -> list[dict[str, object]]:
    return [
        {
            "email": row["email"],
            "slug_url": row["slug_url"],
            "fmt": row["fmt"],
            "error": row["error"],
            "finished_at": format_when(row["finished_at"], tz),  # type: ignore[arg-type]
        }
        for row in rows
    ]
