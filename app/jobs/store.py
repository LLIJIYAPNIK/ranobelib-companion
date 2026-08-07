"""In-memory job store.

A single process-wide dict - the MVP choice CLAUDE.md's roadmap calls for (single-process
deployment; Redis/RQ/Celery only if horizontal scaling actually becomes necessary).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.jobs.models import DownloadJob

_jobs: dict[str, DownloadJob] = {}
_tasks: dict[str, asyncio.Task[None]] = {}


def create_job(slug_url: str, fmt: str) -> DownloadJob:
    """Register a new job in the "queued" state and return it."""
    job = DownloadJob(id=str(uuid4()), slug_url=slug_url, fmt=fmt)
    _jobs[job.id] = job
    return job


def get_job(job_id: str) -> DownloadJob | None:
    return _jobs.get(job_id)


def track_task(job_id: str, task: asyncio.Task[None]) -> None:
    """Keep a strong reference to `task` so it isn't garbage-collected mid-run - asyncio
    only holds a weak reference internally (see `asyncio.create_task()`'s own docs).
    Dropped once the task finishes; the job's own state (in `_jobs`) is what the status
    endpoint reads, so nothing needs the task itself past that point.
    """
    _tasks[job_id] = task
    task.add_done_callback(lambda _: _tasks.pop(job_id, None))
