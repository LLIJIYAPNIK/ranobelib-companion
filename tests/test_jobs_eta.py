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


def test_prefers_the_recent_window_over_the_since_start_average() -> None:
    # Since start: 10 chapters in 100s -> 0.1 chapters/s -> would say 900s left.
    # Recent window: 1 chapter in the last 2s -> 0.5 chapters/s, 90 remaining -> 180s left.
    job = _job(
        started_at=0.0,
        completed=10,
        total=100,
        recent_ticks=[(98.0, 9), (100.0, 10)],
    )

    assert estimate_remaining_seconds(job, now=100.0) == pytest.approx(180.0)


def test_eta_falls_as_pace_recovers_after_a_slowdown() -> None:
    # A stall (e.g. ranobelib.me rate-limiting) shouldn't make the estimate climb
    # forever - once the recent window is past the stall, it should reflect the faster
    # pace immediately rather than staying dragged down by the whole run's average.
    slow_job = _job(
        started_at=0.0, completed=10, total=20, recent_ticks=[(0.0, 5), (100.0, 10)]
    )
    slow_eta = estimate_remaining_seconds(slow_job, now=100.0)

    recovered_job = _job(
        started_at=0.0,
        completed=15,
        total=20,
        recent_ticks=[(100.0, 10), (101.0, 11), (102.0, 12), (103.0, 13), (104.0, 15)],
    )
    recovered_eta = estimate_remaining_seconds(recovered_job, now=104.0)

    assert recovered_eta < slow_eta


def test_ignores_a_window_with_only_one_sample() -> None:
    # Falls back to the since-start average rather than dividing by a zero-length window.
    job = _job(started_at=90.0, completed=5, total=10, recent_ticks=[(100.0, 5)])

    assert estimate_remaining_seconds(job, now=100.0) == pytest.approx(10.0)


def test_ignores_a_stalled_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # No progress within the recent window (e.g. stuck retrying a rate-limited request) -
    # falls back to the since-start average instead of a division by zero/negative rate.
    job = _job(
        started_at=90.0, completed=5, total=10, recent_ticks=[(95.0, 5), (100.0, 5)]
    )

    assert estimate_remaining_seconds(job, now=100.0) == pytest.approx(10.0)
