import asyncio

from app.jobs.store import create_job, get_job, track_task


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
