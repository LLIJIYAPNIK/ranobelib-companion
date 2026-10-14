"""How this process's recent SDK calls ended (PR 345) - what the admin overview's
«Требует внимания» reads to spot ranobelib.me throttling, blocking or not answering.

Every SDK client this app builds goes through ``watch()``: ``open_client()``
(app/services/client.py) and ``get_catalog()`` (app/services/catalog.py). It records one
outcome per client session, in memory only:

- ``rate_limited`` - RateLimitError, ranobelib.me asked us to slow down;
- ``blocked`` - AccessBlockedError, its DDoS protection turned the request away;
- ``unreachable`` - a network failure (httpx.TransportError, which the SDK doesn't wrap);
- ``ok`` - it finished, or failed with an answer from ranobelib.me (a title not found, a
  translation to choose) - the site was there and replied.

Errors that don't come from the SDK (a route's own HTTPException inside the block) record
nothing. The log holds the last MAX_OUTCOMES and starts empty with each process, so the
counts mean "since this process started, at most this many back" - the overview says so.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from ranobelib import AccessBlockedError, RanobeLibError, RateLimitError

OK = "ok"
RATE_LIMITED = "rate_limited"
BLOCKED = "blocked"
UNREACHABLE = "unreachable"

MAX_OUTCOMES = 5000

_outcomes: deque[tuple[datetime, str]] = deque(maxlen=MAX_OUTCOMES)


def _now() -> datetime:
    return datetime.now(UTC)


def outcome_of(exc: BaseException) -> str | None:
    if isinstance(exc, RateLimitError):
        return RATE_LIMITED
    if isinstance(exc, AccessBlockedError):
        return BLOCKED
    if isinstance(exc, RanobeLibError):
        return OK
    if isinstance(exc, httpx.TransportError):
        return UNREACHABLE
    return None


def record(outcome: str) -> None:
    _outcomes.append((_now(), outcome))


@asynccontextmanager
async def watch() -> AsyncIterator[None]:
    """Records how the SDK work inside the block ended; exceptions pass through."""
    try:
        yield
    except Exception as exc:
        outcome = outcome_of(exc)
        if outcome is not None:
            record(outcome)
        raise
    record(OK)


def counts_since(since: datetime) -> Counter[str]:
    return Counter(outcome for at, outcome in _outcomes if at >= since)


def clear() -> None:
    _outcomes.clear()
