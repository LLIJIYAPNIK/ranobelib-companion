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

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

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
