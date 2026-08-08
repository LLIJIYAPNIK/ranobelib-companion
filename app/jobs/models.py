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
    """`time.monotonic()` timestamp set when `status` becomes "running" - the basis for
    `app/jobs/eta.py`'s remaining-time estimate."""
    error: str | None = None
    ambiguous_chapters: list[AmbiguousChapter] = field(default_factory=list)
    result_path: Path | None = None
