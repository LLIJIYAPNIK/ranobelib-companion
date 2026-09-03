"""In-memory rate limiting for POST /login and POST /register (PR 188).

A single process-wide dict, keyed by a caller-chosen string (IP + email, namespaced per
route) - the same MVP treatment CLAUDE.md's roadmap already gives the job store
(app/jobs/store.py): single-process deployment, no Redis/RQ/Celery unless horizontal
scaling actually becomes necessary.
"""

from __future__ import annotations

import time

_WINDOW_SECONDS = 60.0
_MAX_ATTEMPTS = 5

_attempts: dict[str, list[float]] = {}


def is_rate_limited(
    key: str, *, max_attempts: int = _MAX_ATTEMPTS, window_seconds: float = _WINDOW_SECONDS
) -> bool:
    """Records this attempt against `key` and reports whether it exceeds the limit.

    The attempt is recorded even when it's already over the limit, so repeatedly
    retrying past the limit doesn't slide the window back and reset the count - each
    excess attempt just extends how long the key stays limited.
    """
    now = time.monotonic()
    timestamps = _attempts.setdefault(key, [])
    timestamps[:] = [t for t in timestamps if now - t < window_seconds]
    timestamps.append(now)
    return len(timestamps) > max_attempts
