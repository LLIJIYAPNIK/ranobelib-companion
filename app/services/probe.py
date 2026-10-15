"""Is ranobelib.me answering right now? (PR 351, the admin panel's «Система».)

One small real request through the SDK - the first catalog page with ``refresh=True``, so
the SDK's disk cache can't answer for the site - timed, and its outcome kept for
PROBE_TTL_SECONDS: opening the page again doesn't hit ranobelib.me again, and a lock
keeps two admin tabs from probing at once. The SDK paces and rate-limits it like any
other call (it goes through get_catalog(), so sdk_outcomes counts it too).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from ranobelib import AccessBlockedError, RanobeLibError, RateLimitError
from ranobelib.catalog import MIN_PER_PAGE

from app.services.catalog import get_catalog

logger = logging.getLogger(__name__)

PROBE_TTL_SECONDS = 60.0

OK = "ok"  # answered
RATE_LIMITED = "rate_limited"
BLOCKED = "blocked"  # DDoS-Guard turned the request away
UNREACHABLE = "unreachable"  # no answer at all (network, timeout)
ERROR = "error"  # answered, but with an error the SDK couldn't make sense of


@dataclass(frozen=True)
class ProbeResult:
    status: str
    ms: float | None  # None when there was no answer
    checked_at: datetime


_last: tuple[float, ProbeResult] | None = None
_lock = asyncio.Lock()


async def _probe() -> ProbeResult:
    started = time.perf_counter()
    status = OK
    try:
        async with get_catalog() as catalog:
            await catalog.list_titles(per_page=MIN_PER_PAGE, refresh=True)
    except RateLimitError:
        status = RATE_LIMITED
    except AccessBlockedError:
        status = BLOCKED
    except RanobeLibError:
        logger.warning("ranobelib.me answered the probe with an error", exc_info=True)
        status = ERROR
    except httpx.HTTPError:
        logger.warning("ranobelib.me didn't answer the probe", exc_info=True)
        status = UNREACHABLE
    ms = None if status == UNREACHABLE else (time.perf_counter() - started) * 1000
    return ProbeResult(status, ms, datetime.now(UTC))


async def probe_ranobelib() -> ProbeResult:
    global _last
    async with _lock:
        if _last is not None and time.monotonic() - _last[0] < PROBE_TTL_SECONDS:
            return _last[1]
        result = await _probe()
        _last = (time.monotonic(), result)
        return result


def forget() -> None:
    """Drops the kept result - for tests."""
    global _last
    _last = None
