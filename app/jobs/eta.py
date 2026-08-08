"""Remaining-download-time estimate - web-layer logic over the SDK's own on_chapter()
progress ticks (see CLAUDE.md, "Оставшееся время загрузки"), not anything from the SDK.
"""

from __future__ import annotations

import time

from app.jobs.models import DownloadJob


def estimate_remaining_seconds(job: DownloadJob, *, now: float | None = None) -> float | None:
    """Extrapolates from the *recent* pace (job.recent_ticks - a short window of on_chapter()
    ticks, see app/jobs/download.py), not the average since the job started: a slowdown
    (e.g. ranobelib.me rate-limiting) should make the estimate rise and then fall back as
    the pace recovers, not climb forever just because the historical average keeps
    dragging behind. Falls back to the since-start average only until enough recent ticks
    have built up. `None` until there's enough signal to say anything.
    """
    if job.completed <= 0 or job.total <= 0:
        return None

    rate = _recent_rate(job)
    if rate is None:
        rate = _average_rate_since_start(job, now)
    if rate is None or rate <= 0:
        return None

    remaining_chapters = job.total - job.completed
    return remaining_chapters / rate


def _recent_rate(job: DownloadJob) -> float | None:
    if len(job.recent_ticks) < 2:
        return None
    oldest_time, oldest_completed = job.recent_ticks[0]
    newest_time, newest_completed = job.recent_ticks[-1]
    elapsed = newest_time - oldest_time
    completed_in_window = newest_completed - oldest_completed
    if elapsed <= 0 or completed_in_window <= 0:
        return None
    return completed_in_window / elapsed


def _average_rate_since_start(job: DownloadJob, now: float | None) -> float | None:
    if job.started_at is None:
        return None
    elapsed = (now if now is not None else time.monotonic()) - job.started_at
    if elapsed <= 0:
        return None
    return job.completed / elapsed
