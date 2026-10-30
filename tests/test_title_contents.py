"""«Лампа»: the title page's contents (app/title_contents.py) - each volume's reading
state, the reading line with its folds, «новая», who translated how many chapters."""

from datetime import UTC, date, datetime, timedelta

from ranobelib.models import Chapter, ChapterBranch, ChapterUser, Team, Volume

from app.title_contents import Fold, translators, volume_views, when

TODAY = date(2026, 10, 30)


def _branch(team: str, day: date = date(2020, 1, 1)) -> ChapterBranch:
    return ChapterBranch(
        id=1,
        created_at=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        teams=[Team(id=1, slug=team.lower(), slug_url=f"1--{team.lower()}", name=team)],
        user=ChapterUser(id=1, username="uploader"),
    )


def _volume(number: str, count: int, start: int = 1, team: str = "RKT TEAM") -> Volume:
    return Volume(
        number=number,
        chapters=[
            Chapter(
                id=start + i,
                volume=number,
                number=str(start + i),
                name=f"Глава {start + i}",
                branches=[_branch(team)],
            )
            for i in range(count)
        ],
    )


def _numbers(item: object) -> list[str]:
    if isinstance(item, Fold):
        return [row.chapter.number for row in item.rows]
    return [item.chapter.number]  # type: ignore[attr-defined]


def test_the_reader_volume_folds_the_read_run_and_keeps_two_above_the_place() -> None:
    volumes = [_volume("1", 5), _volume("2", 30, start=6)]
    views = volume_views(volumes, current_index=5 + 20, today=TODAY)

    first, second = views
    assert first.done and first.read_count == 5
    assert len(first.items) == 1 and isinstance(first.items[0], Fold) and first.items[0].read

    assert second.is_current and second.read_count == 20
    fold, *rest = second.items
    assert isinstance(fold, Fold) and fold.read and fold.count == 18
    shown = [item for item in rest if not isinstance(item, Fold)]
    assert [row.state for row in shown[:3]] == ["read", "read", "here"]
    # Nine after the place, and the tail of one is shown rather than folded.
    assert shown[-1].chapter.number == str(6 + 29)


def test_a_long_tail_folds_and_a_short_one_does_not() -> None:
    long_tail = volume_views([_volume("1", 40)], current_index=None, today=TODAY)[0]
    assert [isinstance(i, Fold) for i in long_tail.items].count(True) == 1
    assert long_tail.items[-1].count == 28  # type: ignore[union-attr]

    short = volume_views([_volume("1", 14)], current_index=None, today=TODAY)[0]
    assert not any(isinstance(i, Fold) for i in short.items)
    assert sum(len(_numbers(i)) for i in short.items) == 14


def test_without_a_place_nothing_is_read() -> None:
    views = volume_views(
        [_volume("1", 3), _volume("2", 3, start=4)], current_index=None, today=TODAY
    )
    assert all(v.read_count == 0 and not v.is_current for v in views)
    assert all(row.state == "" for v in views for row in v.items)  # type: ignore[union-attr]


def test_new_chapters_say_when_they_came_out() -> None:
    chapter = Chapter(
        id=1, volume="1", number="1", branches=[_branch("A", TODAY - timedelta(days=1))]
    )
    old = Chapter(id=2, volume="1", number="2", branches=[_branch("A", TODAY - timedelta(days=40))])
    rows = volume_views([Volume(number="1", chapters=[chapter, old])], None, TODAY)[0].items
    assert [row.fresh for row in rows] == ["новая · вчера", None]  # type: ignore[union-attr]


def test_when_words() -> None:
    assert when(TODAY, TODAY) == "сегодня"
    assert when(TODAY - timedelta(days=3), TODAY) == "3 дня назад"
    assert when(TODAY - timedelta(days=6), TODAY) == "6 дней назад"
    assert when(date(2026, 9, 22), TODAY) == "22 сен"
    assert when(date(2018, 4, 13), TODAY) == "13 апр 2018"


def test_translators_count_chapters_most_first() -> None:
    volumes = [_volume("1", 3, team="DUB1401"), _volume("2", 5, start=4, team="RKT TEAM")]
    assert translators(volumes) == [("RKT TEAM", 5), ("DUB1401", 3)]
