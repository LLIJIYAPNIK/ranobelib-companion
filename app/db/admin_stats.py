"""The admin panel's metrics layer (PR 344): periods, series and the previous-period
comparison that "Обзор" (PR 345) and the analytics screens (PR 346-347) are built on.

Periods are ``24h``/``7d``/``30d``/``90d``, always in ``ADMIN_TIMEZONE`` (the server's
clock, not any user's - see app/config.py):

- ``24h`` - 24 one-hour buckets ending at the next hour boundary on the local clock, so
  the current, still running hour is the last bucket. Each bucket is exactly one hour of
  real time, so a DST switch doesn't make one longer or shorter (its label just repeats
  or skips a local hour).
- ``7d``/``30d``/``90d`` - local calendar days, today (so far) the last one. A day is
  local midnight to local midnight, 23 or 25 hours long on a DST switch.

The previous period is the same number of buckets right before the current one.

Every boundary is computed here, in Python, as an aware UTC datetime. Postgres only puts
a row into a bucket - ``width_bucket(ts, thresholds)`` over the list of bucket starts -
so zones with half-hour offsets or DST need nothing on the SQL side. ``created_at``
columns stay UTC ISO-8601 TEXT; range filters compare them as strings (string order is
time order for these, see app/timezones.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any, Literal
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from app.db.admin_data import _read_only

PERIODS = ("24h", "7d", "30d", "90d")
DEFAULT_PERIOD = "7d"
_DAYS = {"7d": 7, "30d": 30, "90d": 90}

Granularity = Literal["hour", "day"]


@dataclass(frozen=True)
class Period:
    """One run of consecutive buckets. ``starts`` are the buckets' UTC starts in order;
    bucket i lasts until ``starts[i + 1]`` (the last one until ``end``)."""

    key: str
    granularity: Granularity
    tz: str
    starts: tuple[datetime, ...]
    end: datetime

    @property
    def start(self) -> datetime:
        return self.starts[0]

    @property
    def labels(self) -> list[datetime]:
        """Each bucket's start on the local clock - what the chart's axis shows."""
        zone = ZoneInfo(self.tz)
        return [start.astimezone(zone) for start in self.starts]

    def previous(self) -> Period:
        """The same number of buckets right before this period."""
        if self.granularity == "hour":
            shift = timedelta(hours=len(self.starts))
            return Period(
                key=self.key,
                granularity="hour",
                tz=self.tz,
                starts=tuple(start - shift for start in self.starts),
                end=self.start,
            )
        zone = ZoneInfo(self.tz)
        first_day = self.start.astimezone(zone).date()
        days = len(self.starts)
        return Period(
            key=self.key,
            granularity="day",
            tz=self.tz,
            starts=tuple(
                _local_midnight(first_day - timedelta(days=days - i), zone) for i in range(days)
            ),
            end=self.start,
        )


def parse_period(raw: str | None) -> str:
    """A period key from a query string - anything unknown is the default, not an error."""
    return raw if raw in PERIODS else DEFAULT_PERIOD


def build_period(key: str, tz: str, now: datetime | None = None) -> Period:
    """The period `key` ending now (the current hour or day included) in zone `tz`."""
    if key not in PERIODS:
        raise ValueError(f"unknown period {key!r}")
    zone = ZoneInfo(tz)
    local_now = (now or datetime.now(UTC)).astimezone(zone)
    if key == "24h":
        hour_start = local_now.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
        end = hour_start + timedelta(hours=1)
        starts = tuple(end - timedelta(hours=24 - i) for i in range(24))
        return Period(key=key, granularity="hour", tz=tz, starts=starts, end=end)
    days = _DAYS[key]
    today = local_now.date()
    starts = tuple(_local_midnight(today - timedelta(days=days - 1 - i), zone) for i in range(days))
    end = _local_midnight(today + timedelta(days=1), zone)
    return Period(key=key, granularity="day", tz=tz, starts=starts, end=end)


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


# --- metric series -------------------------------------------------------------------------

METRICS = (
    "active_readers",
    "chapters_read",
    "reading_seconds",
    "registrations",
    "downloads",
    "comments",
)

# How long cached_period_stats() reuses a result. Application data (not the SDK's cache):
# the overview may lag this far behind, which is fine for a dashboard, and a reload storm
# or several open tabs don't redo the 90-day aggregates every time.
CACHE_SECONDS = 60


@dataclass(frozen=True)
class _MetricQuery:
    table: str
    column: str  # the UTC ISO-8601 TEXT timestamp a row is counted at
    where: str
    value: str


# Constants only - nothing from a request ever reaches these strings.
_QUERIES = {
    # Anyone with any activity event: a chapter opened or a reading heartbeat. The kinds
    # are spelled out so the (kind, created_at) index (migration 0028) is usable.
    "active_readers": _MetricQuery(
        "activity_events",
        "created_at",
        "kind IN ('chapter_read', 'heartbeat')",
        "COUNT(DISTINCT user_id)",
    ),
    "chapters_read": _MetricQuery(
        "activity_events", "created_at", "kind = 'chapter_read'", "COUNT(*)"
    ),
    # Seconds of active reading - the heartbeat ticks summed, as on the user's own
    # activity page (app/db/activity.py).
    "reading_seconds": _MetricQuery(
        "activity_events", "created_at", "kind = 'heartbeat'", "COALESCE(SUM(seconds), 0)"
    ),
    "registrations": _MetricQuery("users", "created_at", "TRUE", "COUNT(*)"),
    # Every finished download, successful or not.
    "downloads": _MetricQuery("download_history", "finished_at", "TRUE", "COUNT(*)"),
    # Every comment written, including ones deleted since.
    "comments": _MetricQuery("comments", "created_at", "TRUE", "COUNT(*)"),
}


@dataclass(frozen=True)
class MetricSeries:
    """One metric over a period and the period before it. ``current``/``previous`` have
    one value per bucket. The totals are for the whole period - for active readers that's
    distinct readers over the period, not the sum of the buckets (one reader active on
    three days is one reader for the week)."""

    key: str
    current: list[int]
    previous: list[int]
    current_total: int
    previous_total: int

    @property
    def comparison(self) -> Comparison:
        return compare(self.current_total, self.previous_total)


@dataclass(frozen=True)
class Comparison:
    """A total against the previous period's. ``percent`` is None when the previous
    period had nothing - growth from zero has no percentage, and the card shows that
    rather than "+∞%" or a made-up 100%."""

    current: int
    previous: int
    delta: int
    percent: float | None


def compare(current: int, previous: int) -> Comparison:
    percent = (current - previous) / previous * 100 if previous else None
    return Comparison(current=current, previous=previous, delta=current - previous, percent=percent)


@dataclass(frozen=True)
class PeriodStats:
    period: Period
    previous_period: Period
    metrics: dict[str, MetricSeries]
    at: datetime  # when it was computed (UTC) - the overview's «данные на 14:32»


async def period_stats(
    conn: AsyncConnection, key: str, tz: str, now: datetime | None = None
) -> PeriodStats:
    """Every metric for period `key` in zone `tz`, with the previous period's alongside.
    Runs read-only with the admin query timeout (PR 326): a too slow aggregate is
    cancelled with QueryCanceled rather than holding a pool connection."""
    now = now or datetime.now(UTC)
    period = build_period(key, tz, now)
    previous = period.previous()
    async with _read_only(conn):
        metrics = {name: await _series(conn, name, previous, period) for name in METRICS}
        readers = await _distinct_readers(conn, previous, period)
    metrics["active_readers"] = replace(
        metrics["active_readers"],
        previous_total=readers["previous"],
        current_total=readers["current"],
    )
    return PeriodStats(period=period, previous_period=previous, metrics=metrics, at=now)


_cache: dict[tuple[str, ...], tuple[float, Any]] = {}


async def cached[T](key: tuple[str, ...], compute: Callable[[], Awaitable[T]]) -> T:
    """`compute()`'s result, reused for CACHE_SECONDS per `key` (what was computed and
    for which period/zone)."""
    hit = _cache.get(key)
    if hit is not None and monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = await compute()
    _cache[key] = (monotonic(), value)
    return value


async def cached_period_stats(conn: AsyncConnection, key: str, tz: str) -> PeriodStats:
    """period_stats() for now, reused for CACHE_SECONDS per (period, zone)."""
    return await cached(("stats", key, tz), lambda: period_stats(conn, key, tz))


def clear_cache() -> None:
    _cache.clear()


async def _series(
    conn: AsyncConnection, name: str, previous: Period, period: Period
) -> MetricSeries:
    """One query over both periods: width_bucket() numbers the previous period's buckets
    1..n and the current one's n+1..2n; a bucket with no rows is simply absent."""
    query = _QUERIES[name]
    thresholds = list(previous.starts + period.starts)
    cursor = await conn.execute(
        f"SELECT width_bucket({query.column}::timestamptz, %s::timestamptz[]) AS bucket, "
        f"{query.value} AS value FROM {query.table} "
        f"WHERE {query.where} AND {query.column} >= %s AND {query.column} < %s "
        "GROUP BY bucket",
        (thresholds, previous.start.isoformat(), period.end.isoformat()),
    )
    values = [0] * len(thresholds)
    for row in await cursor.fetchall():
        values[row["bucket"] - 1] = int(row["value"])
    n = len(previous.starts)
    return MetricSeries(
        key=name,
        current=values[n:],
        previous=values[:n],
        current_total=sum(values[n:]),
        previous_total=sum(values[:n]),
    )


async def _distinct_readers(
    conn: AsyncConnection, previous: Period, period: Period
) -> dict[str, int]:
    """Distinct active readers over each whole period (see MetricSeries)."""
    cursor = await conn.execute(
        "SELECT COUNT(DISTINCT user_id) FILTER (WHERE created_at < %(split)s) AS previous, "
        "COUNT(DISTINCT user_id) FILTER (WHERE created_at >= %(split)s) AS current "
        f"FROM activity_events WHERE {_QUERIES['active_readers'].where} "
        "AND created_at >= %(start)s AND created_at < %(end)s",
        {
            "split": period.start.isoformat(),
            "start": previous.start.isoformat(),
            "end": period.end.isoformat(),
        },
    )
    row = await cursor.fetchone()
    return {"previous": row["previous"], "current": row["current"]}
