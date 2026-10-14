"""«Обзор» (PR 345): what the admin overview shows, shaped from the metrics layer
(app/db/admin_stats.py) for admin/index.html - the period switch, the KPI cards, the
recent rows. Presentation only, like app/admin_kit.py: the numbers come from app/db/.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.admin_kit import Column, Delta, Sparkline, admin_query, delta, format_number, sparkline
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
