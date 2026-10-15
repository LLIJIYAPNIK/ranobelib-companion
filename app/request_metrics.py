"""Request latency and 5xx share for the admin panel's «Система» (PR 351).

An in-process ring buffer of the last MAX_SAMPLES requests (when, how long, which
status), filled by a middleware. It's a sliding window since the process started -
empty after every deploy or restart, and per process - which the page says. No external
monitoring, no stored series.

Static files and uploaded media aren't counted: they're served without touching the app's
own code, and thousands of 304s would drown out the pages and API calls this is about.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response

MAX_SAMPLES = 10_000
SKIPPED_PREFIXES = ("/static/", "/avatars/", "/comment-attachments/")


@dataclass(frozen=True)
class Sample:
    at: float  # time.time()
    ms: float
    status: int


_samples: deque[Sample] = deque(maxlen=MAX_SAMPLES)


def record(ms: float, status: int, at: float | None = None) -> None:
    _samples.append(Sample(time.time() if at is None else at, ms, status))


def samples() -> list[Sample]:
    return list(_samples)


def clear() -> None:
    _samples.clear()


def install_request_metrics(app: FastAPI) -> None:
    """Registered last in app.main, so it's the outermost of the app's middlewares and
    times the whole request. An exception escaping the app counts as a 500 - that's what
    the client gets from Starlette's error handler."""

    @app.middleware("http")
    async def _request_metrics(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith(SKIPPED_PREFIXES):
            return await call_next(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            record((time.perf_counter() - started) * 1000, 500)
            raise
        record((time.perf_counter() - started) * 1000, response.status_code)
        return response


def percentile(sorted_ms: list[float], share: float) -> float | None:
    """Nearest-rank percentile of already sorted values; None for none."""
    if not sorted_ms:
        return None
    rank = max(1, math.ceil(share * len(sorted_ms)))
    return sorted_ms[rank - 1]


@dataclass(frozen=True)
class RequestStats:
    count: int
    p50: float | None
    p95: float | None
    p99: float | None
    errors: int  # 5xx
    since: float | None  # the oldest sample still in the window

    @property
    def error_share(self) -> float | None:
        return self.errors / self.count if self.count else None


def summarize(window: list[Sample]) -> RequestStats:
    durations = sorted(sample.ms for sample in window)
    return RequestStats(
        count=len(window),
        p50=percentile(durations, 0.50),
        p95=percentile(durations, 0.95),
        p99=percentile(durations, 0.99),
        errors=sum(1 for sample in window if sample.status >= 500),
        since=min((sample.at for sample in window), default=None),
    )


@dataclass(frozen=True)
class MinuteBucket:
    start: float  # the minute's first second, time.time()
    count: int
    p95: float | None


def per_minute(window: list[Sample], now: float, minutes: int = 60) -> list[MinuteBucket]:
    """p95 per minute for the last `minutes` minutes (the current one included), oldest
    first; a minute without requests has p95 None."""
    current = math.floor(now / 60) * 60
    first = current - (minutes - 1) * 60
    buckets: dict[float, list[float]] = {first + i * 60: [] for i in range(minutes)}
    for sample in window:
        start = math.floor(sample.at / 60) * 60
        if start in buckets:
            buckets[start].append(sample.ms)
    return [
        MinuteBucket(start, len(values), percentile(sorted(values), 0.95))
        for start, values in buckets.items()
    ]
