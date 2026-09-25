import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from app.jobs.store import (
    cancel_job,
    create_job,
    delete_result_file,
    get_job,
    ready_file_url,
    sweep_expired_result_files,
    track_task,
)


def test_create_job_starts_queued() -> None:
    job = create_job("6712--test-novel", "epub")

    assert job.slug_url == "6712--test-novel"
    assert job.fmt == "epub"
    assert job.status == "queued"
    assert job.completed == 0
    assert job.total == 0
    assert job.error is None
    assert job.ambiguous_chapters == []
    assert job.result_path is None


def test_create_job_ids_are_unique() -> None:
    first = create_job("6712--test-novel", "epub")
    second = create_job("6712--test-novel", "epub")

    assert first.id != second.id


def test_get_job_returns_the_created_job() -> None:
    job = create_job("6712--test-novel", "txt")

    assert get_job(job.id) is job


def test_get_job_missing_returns_none() -> None:
    assert get_job("does-not-exist") is None


def _job_with_result_file(*, finished_seconds_ago: float) -> tuple:
    job = create_job("6712--test-novel", "epub")
    job.status = "done"
    fd, path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    job.result_path = Path(path)
    job.finished_at = time.monotonic() - finished_seconds_ago
    return job, path


def test_delete_result_file_removes_file_and_clears_path() -> None:
    job, path = _job_with_result_file(finished_seconds_ago=0)

    delete_result_file(job)

    assert not os.path.exists(path)
    assert job.result_path is None


def test_delete_result_file_is_a_no_op_when_already_delivered() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "done"
    job.result_path = None

    delete_result_file(job)  # must not raise

    assert job.result_path is None


def test_sweep_expired_result_files_removes_files_past_ttl() -> None:
    old_job, old_path = _job_with_result_file(finished_seconds_ago=120)
    fresh_job, fresh_path = _job_with_result_file(finished_seconds_ago=1)

    sweep_expired_result_files(ttl_seconds=60)

    assert not os.path.exists(old_path)
    assert old_job.result_path is None
    assert os.path.exists(fresh_path)
    assert fresh_job.result_path == Path(fresh_path)

    os.remove(fresh_path)


def test_sweep_expired_result_files_ignores_still_running_jobs() -> None:
    job = create_job("6712--test-novel", "epub")
    job.status = "running"

    sweep_expired_result_files(ttl_seconds=0)  # must not raise on a job with no result yet


def test_ready_file_url_returns_none_for_missing_job_id() -> None:
    assert ready_file_url(None, user_id=1) is None


def test_ready_file_url_returns_none_for_unknown_job() -> None:
    assert ready_file_url("does-not-exist", user_id=1) is None


def test_ready_file_url_returns_none_for_another_users_job() -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "done"
    job.result_path = Path("/tmp/whatever.epub")

    assert ready_file_url(job.id, user_id=2) is None


def test_ready_file_url_returns_none_while_still_running() -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "running"

    assert ready_file_url(job.id, user_id=1) is None


def test_ready_file_url_returns_none_once_delivered() -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "done"
    job.result_path = None

    assert ready_file_url(job.id, user_id=1) is None


def test_ready_file_url_returns_the_file_url_when_still_ready() -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "done"
    job.result_path = Path("/tmp/whatever.epub")

    assert ready_file_url(job.id, user_id=1) == f"/titles/6712--test-novel/download/{job.id}/file"


# --- PR 226: cancel_job() ---------------------------------------------------------------


def test_cancel_job_unknown_job_returns_false() -> None:
    assert cancel_job("does-not-exist", user_id=1) is False


def test_cancel_job_with_no_tracked_task_returns_false() -> None:
    # e.g. a job that already finished - track_task()'s own done-callback already dropped
    # it from _tasks by the time anything would try to cancel it.
    job = create_job("6712--test-novel", "epub", user_id=1)
    job.status = "done"

    assert cancel_job(job.id, user_id=1) is False


async def test_cancel_job_owned_by_another_user_returns_false_and_does_not_cancel() -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    finish = asyncio.Event()
    task = asyncio.create_task(finish.wait())
    track_task(job.id, task)

    assert cancel_job(job.id, user_id=2) is False
    assert not task.cancelled()

    finish.set()
    await task


async def test_cancel_job_cancels_the_tracked_task() -> None:
    job = create_job("6712--test-novel", "epub", user_id=1)
    finish = asyncio.Event()
    task = asyncio.create_task(finish.wait())
    track_task(job.id, task)

    assert cancel_job(job.id, user_id=1) is True

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_cancel_job_anonymous_job_is_cancellable_by_anyone() -> None:
    # No user_id (an anonymous download) - same asymmetric ownership rule as
    # app/api/downloads.py's _get_job_or_404(): only a job actually tied to an account is
    # scoped to its owner.
    job = create_job("6712--test-novel", "epub")  # user_id=None
    finish = asyncio.Event()
    task = asyncio.create_task(finish.wait())
    track_task(job.id, task)

    assert cancel_job(job.id, user_id=999) is True

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_track_task_survives_until_it_completes() -> None:
    job = create_job("6712--test-novel", "epub")
    finish = asyncio.Event()

    async def _work() -> None:
        await finish.wait()

    task = asyncio.create_task(_work())
    track_task(job.id, task)

    # No local reference to `task` is kept beyond this point - if track_task() didn't
    # hold one, the task could be garbage-collected before it runs to completion.
    del task
    await asyncio.sleep(0)

    finish.set()
    await asyncio.sleep(0)
