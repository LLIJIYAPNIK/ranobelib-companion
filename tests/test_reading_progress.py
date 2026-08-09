from ranobelib.models import Chapter, Volume

from app.reading_progress import reading_progress_percent


def _volumes() -> list[Volume]:
    return [
        Volume(
            number="1",
            chapters=[
                Chapter(id=1, volume="1", number="1"),
                Chapter(id=2, volume="1", number="2"),
                Chapter(id=3, volume="1", number="3"),
            ],
        ),
        Volume(
            number="2",
            chapters=[
                Chapter(id=4, volume="2", number="1"),
            ],
        ),
    ]


def test_reading_progress_percent_none_without_recorded_progress() -> None:
    assert reading_progress_percent(_volumes(), None, None) is None


def test_reading_progress_percent_none_with_only_half_recorded() -> None:
    # Shouldn't happen given how record_progress() writes both fields together, but the
    # helper shouldn't guess a percentage from a half-known position either.
    assert reading_progress_percent(_volumes(), "1", None) is None
    assert reading_progress_percent(_volumes(), None, "1") is None


def test_reading_progress_percent_none_without_any_chapters() -> None:
    assert reading_progress_percent([], "1", "1") is None


def test_reading_progress_percent_none_when_chapter_not_found() -> None:
    # e.g. the last-read chapter was removed/renumbered upstream since it was recorded.
    assert reading_progress_percent(_volumes(), "9", "9") is None


def test_reading_progress_percent_first_chapter() -> None:
    assert reading_progress_percent(_volumes(), "1", "1") == 25  # 1 of 4


def test_reading_progress_percent_mid_title() -> None:
    assert reading_progress_percent(_volumes(), "1", "3") == 75  # 3 of 4


def test_reading_progress_percent_last_chapter_is_100() -> None:
    assert reading_progress_percent(_volumes(), "2", "1") == 100  # 4 of 4


def test_reading_progress_percent_rounds_to_nearest_percent() -> None:
    volumes = [
        Volume(
            number="1",
            chapters=[Chapter(id=i, volume="1", number=str(i)) for i in range(1, 4)],
        )
    ]
    assert reading_progress_percent(volumes, "1", "1") == 33  # 1 of 3, rounds down
    assert reading_progress_percent(volumes, "1", "2") == 67  # 2 of 3, rounds up
