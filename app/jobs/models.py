"""In-memory state for a background whole-title download job."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ranobelib.exceptions import AmbiguousChapter

JobStatus = Literal["queued", "running", "exporting", "done", "error", "needs_translation"]


@dataclass
class DownloadJob:
    """Tracks one `download_title()` + `export()` run, polled by the status endpoint."""

    id: str
    slug_url: str
    fmt: str
    user_id: int | None = None
    status: JobStatus = "queued"
    completed: int = 0
    total: int = 0
    started_at: float | None = None
    """`time.monotonic()` timestamp set when `status` becomes "running" - the fallback
    basis for `app/jobs/eta.py`'s remaining-time estimate until enough of `recent_ticks`
    has built up."""
    recent_ticks: list[tuple[float, int]] = field(default_factory=list)
    """A short rolling window of (time.monotonic(), completed) samples from on_chapter()
    ticks - `app/jobs/eta.py` extrapolates from *this* recent pace rather than the
    average since `started_at`, so the estimate actually falls as progress speeds back up
    after a slowdown (e.g. ranobelib.me rate-limiting) instead of only ever climbing."""
    error: str | None = None
    ambiguous_chapters: list[AmbiguousChapter] = field(default_factory=list)
    result_path: Path | None = None
