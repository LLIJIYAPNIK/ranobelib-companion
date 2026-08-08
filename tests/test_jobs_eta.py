import pytest

from app.jobs.eta import estimate_remaining_seconds
from app.jobs.models import DownloadJob


def _job(**kwargs: object) -> DownloadJob:
    defaults: dict[str, object] = {"id": "1", "slug_url": "6712--test-novel", "fmt": "epub"}
    defaults.update(kwargs)
    return DownloadJob(**defaults)


def test_returns_none_before_started_at_is_set() -> None:
    job = _job(completed=5, total=10)

    assert estimate_remaining_seconds(job, now=100.0) is None


def test_returns_none_with_no_progress_yet() -> None:
    job = _job(started_at=90.0, completed=0, total=10)

    assert estimate_remaining_seconds(job, now=100.0) is None


def test_returns_none_without_a_known_total() -> None:
    job = _job(started_at=90.0, completed=5, total=0)

    assert estimate_remaining_seconds(job, now=100.0) is None


def test_extrapolates_from_average_pace() -> None:
    # 5 chapters in 10s -> 0.5 chapters/s -> 5 chapters remaining -> 10s left
    job = _job(started_at=90.0, completed=5, total=10)

    assert estimate_remaining_seconds(job, now=100.0) == pytest.approx(10.0)


def test_returns_zero_when_job_is_effectively_done() -> None:
    job = _job(started_at=90.0, completed=10, total=10)

    assert estimate_remaining_seconds(job, now=100.0) == pytest.approx(0.0)


def test_returns_none_for_nonpositive_elapsed_time() -> None:
    # started_at in the future (clock skew/race) - no meaningful rate to extrapolate from.
    job = _job(started_at=100.0, completed=5, total=10)

    assert estimate_remaining_seconds(job, now=100.0) is None
