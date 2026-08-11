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


def create_job(slug_url: str, fmt: str, user_id: int | None = None) -> DownloadJob:
    """Register a new job in the "queued" state and return it. `user_id` is None for an
    anonymous visitor - downloading has never required an account, it just means this job
    won't show up in anyone's "Загрузки" list (see app/api/downloads_section.py)."""
    job = DownloadJob(id=str(uuid4()), slug_url=slug_url, fmt=fmt, user_id=user_id)
    _jobs[job.id] = job
    return job


def get_job(job_id: str) -> DownloadJob | None:
    return _jobs.get(job_id)


def list_active_jobs_for_user(user_id: int) -> list[DownloadJob]:
    """The "Текущие" section of "Загрузки" (app/api/downloads_section.py) - jobs that
    haven't reached a terminal state yet. Finished ones live in `download_history`
    instead (see app/db/downloads.py); this isn't the place to also show those.
    """
    return [
        job
        for job in _jobs.values()
        if job.user_id == user_id and job.status not in ("done", "error")
    ]


def list_ready_jobs_for_user(user_id: int) -> list[DownloadJob]:
    """Finished jobs whose exported file is still on disk, waiting to be picked up - the
    global "file ready" poller (app/static/js/download-ready.js) reads this from any page,
    not just the job's own status page (PR 50). A job falls out of this list once its file
    is delivered or swept by TTL (see delete_result_file() below), not when `status`
    becomes "done" - that's immediate and would make every finished job "ready" forever.
    """
    return [
        job
        for job in _jobs.values()
        if job.user_id == user_id and job.status == "done" and job.result_path is not None
    ]


def track_task(job_id: str, task: asyncio.Task[None]) -> None:
    """Keep a strong reference to `task` so it isn't garbage-collected mid-run - asyncio
    only holds a weak reference internally (see `asyncio.create_task()`'s own docs).
    Dropped once the task finishes; the job's own state (in `_jobs`) is what the status
    endpoint reads, so nothing needs the task itself past that point.
    """
    _tasks[job_id] = task
    task.add_done_callback(lambda _: _tasks.pop(job_id, None))
