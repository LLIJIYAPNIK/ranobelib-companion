"""Remaining-download-time estimate - web-layer logic over the SDK's own on_chapter()
progress ticks (see CLAUDE.md, "Оставшееся время загрузки"), not anything from the SDK.
"""

from __future__ import annotations

import time

from app.jobs.models import DownloadJob


def estimate_remaining_seconds(job: DownloadJob, *, now: float | None = None) -> float | None:
    """Extrapolates from the average pace since the job started running. `None` until
    there's enough signal to say anything (no `started_at` yet, or no progress yet)."""
    if job.started_at is None or job.completed <= 0 or job.total <= 0:
        return None
    elapsed = (now if now is not None else time.monotonic()) - job.started_at
    if elapsed <= 0:
        return None
    rate = job.completed / elapsed
    remaining_chapters = job.total - job.completed
    return remaining_chapters / rate
