"""«Аналитика» (PR 346): the titles list and a title's page, shaped for
admin/analytics.html and admin/analytics_title.html from app/db/admin_analytics.py and
the SDK data app/services/ resolves. Presentation only, like app/admin_overview.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ranobelib.models import Chapter, Volume

from app.admin_kit import BarChart, BarSeries, Column, Delta, bar_chart, delta, format_number
from app.admin_overview import format_duration
from app.db.admin_analytics import TitleRow, TitleTotals
from app.reading_progress import reading_progress_percent
from app.services.titles import TitleSummary

# «Дочитали» needs each title's table of contents from the SDK - one request per title -
# so it's computed for the first rows of the list only, and the column says so.
FINISHED_TOP = 10

TITLE_COLUMNS = (
    Column("title", "Тайтл", sortable=False, locked=True),
    Column("readers", "Читатели", "num", locked=True),
    Column("trend", "К прошлому периоду", "num", sortable=False),
    Column("chapters", "Прочитано глав", "num"),
    Column("library", "В библиотеку", "num"),
    Column("time", "Время чтения", "num"),
    Column("finished", f"Дочитали (топ-{FINISHED_TOP})", "num", sortable=False),
)


@dataclass(frozen=True)
class Finished:
    """Library entries of a title whose reading position is its last chapter
    (reading_progress_percent() = 100), out of those with any position."""

    done: int
    total: int

    @property
    def text(self) -> str:
        share = round(self.done / self.total * 100) if self.total else 0
        return f"{share}% ({self.done} из {self.total})"


def finished(volumes: list[Volume], positions: list[tuple[str, str]]) -> Finished | None:
    """None when no library entry has a position the SDK's chapter list knows - there's
    nothing to divide by, so no share is made up."""
    percents = [reading_progress_percent(volumes, v, n) for v, n in positions]
    known = [p for p in percents if p is not None]
    if not known:
        return None
    return Finished(done=sum(1 for p in known if p == 100), total=len(known))


@dataclass(frozen=True)
class TitleListRow:
    slug_url: str
    name: str
    cover_url: str | None
    href: str
    readers: str
    trend: Delta | None
    chapters: str
    library: str
    time: str
    finished: str | None  # None outside the top - the cell says why


# --- a title's page -----------------------------------------------------------------------

# A drop is only named when this many readers reached the chapter before it - one reader
# out of two leaving is 50%, and means nothing.
DROP_MIN_READERS = 3
TOP_DROPS = 5


@dataclass(frozen=True)
class RetentionPoint:
    """One chapter of the SDK's table of contents with how many of the first chapter's
    readers reached it. ``drop`` is the share of the previous chapter's readers (of the
    cohort) who didn't open this one - None for the first chapter or after a chapter
    nobody reached."""

    index: int
    volume: str
    number: str
    label: str
    name: str | None
    readers: int
    reach: float  # percent of the cohort
    drop: float | None
    previous_readers: int

    @property
    def drop_text(self) -> str:
        return format_percent(self.drop) if self.drop is not None else "—"


def chapter_label(volume: str, number: str, several_volumes: bool) -> str:
    return f"Т. {volume}, гл. {number}" if several_volumes else f"Гл. {number}"


def retention(
    volumes: list[Volume], cohort: int, reached: Mapping[tuple[str, str], int]
) -> list[RetentionPoint]:
    """The chapters in the SDK's own order (never re-sorted here) with the cohort's reach
    and the drop from the chapter before. Gaps in numbering, extra chapters, several
    volumes - all simply follow the table of contents."""
    several = len({volume.number for volume in volumes}) > 1
    points: list[RetentionPoint] = []
    previous = None
    for volume in volumes:
        for chapter in volume.chapters:
            readers = reached.get((chapter.volume, chapter.number), 0)
            drop = None
            if previous is not None and previous > 0:
                drop = max(0.0, (previous - readers) / previous * 100)
            points.append(
                RetentionPoint(
                    index=len(points),
                    volume=chapter.volume,
                    number=chapter.number,
                    label=chapter_label(chapter.volume, chapter.number, several),
                    name=chapter.name,
                    readers=readers,
                    reach=readers / cohort * 100 if cohort else 0.0,
                    drop=drop,
                    previous_readers=previous or 0,
                )
            )
            previous = readers
    return points


def biggest_drops(points: list[RetentionPoint]) -> list[RetentionPoint]:
    """The chapters that lost the largest share of the previous chapter's readers, among
    those with enough readers before them to mean something."""
    candidates = [
        point for point in points if point.drop and point.previous_readers >= DROP_MIN_READERS
    ]
    return sorted(candidates, key=lambda point: (-point.drop, point.index))[:TOP_DROPS]


# --- «Что стоит проверить» ----------------------------------------------------------------
# Only what follows from the numbers and the SDK's dates - where readers stopped and where
# releases paused. No guessed reasons: the admin opens the chapter and decides.

SHARP_DROP = 30.0  # percent of the previous chapter's readers
PAUSE_DAYS = 30
HINTS_LIMIT = 6


@dataclass(frozen=True)
class Hint:
    kind: str  # "drop" or "pause"
    title: str
    text: str


def chapter_released(chapter: Chapter) -> datetime | None:
    """When a chapter first appeared on ranobelib.me: its earliest translation's date
    from the SDK. None when the table of contents has no translations for it."""
    dates = [branch.created_at for branch in chapter.branches]
    return min(dates) if dates else None


def hints(volumes: list[Volume], points: list[RetentionPoint]) -> list[Hint]:
    """Sharp drops (at least SHARP_DROP% of the previous chapter's readers, with enough
    of them) and release pauses of PAUSE_DAYS or more, in chapter order."""
    by_index = {point.index: point for point in points}
    chapters = [chapter for volume in volumes for chapter in volume.chapters]
    found: list[tuple[int, Hint]] = []
    for index, chapter in enumerate(chapters):
        point = by_index.get(index)
        if (
            point is not None
            and point.drop is not None
            and point.drop >= SHARP_DROP
            and point.previous_readers >= DROP_MIN_READERS
        ):
            found.append(
                (
                    index,
                    Hint(
                        "drop",
                        f"{point.label}: резкий обрыв",
                        f"Из {point.previous_readers} читателей предыдущей главы эту открыли "
                        f"{point.readers} (−{point.drop_text}). Стоит открыть главу и "
                        "проверить, всё ли с ней в порядке.",
                    ),
                )
            )
        if index == 0:
            continue
        released, before = chapter_released(chapter), chapter_released(chapters[index - 1])
        if released is None or before is None:
            continue
        days = (released - before).days
        if days >= PAUSE_DAYS:
            label = point.label if point else chapter_label(chapter.volume, chapter.number, False)
            text = f"Глава вышла через {days} дн. после предыдущей."
            if point is not None and point.previous_readers:
                text += (
                    f" Из {point.previous_readers} читателей предыдущей главы её открыли "
                    f"{point.readers}."
                )
            found.append((index, Hint("pause", f"{label}: пауза в выпуске", text)))
    return [hint for _, hint in sorted(found, key=lambda item: item[0])][:HINTS_LIMIT]


def format_percent(value: float) -> str:
    return f"{format_number(round(value, 1))}%"


def retention_chart(points: list[RetentionPoint]) -> BarChart:
    """«Удержание по главам»: a column per chapter, 0-100% of the first chapter's
    readers; the hover adds the readers and the drop from the chapter before. Every
    column's hover has the same rows, so the screen-reader table lines up."""
    return bar_chart(
        [BarSeries("Дошли", [point.reach for point in points])],
        [point.label for point in points],
        name="Удержание по главам",
        fmt=format_percent,
        height=220,
        y_max=100,
        details=[
            (
                ("Читателей", format_number(point.readers)),
                ("Отток", point.drop_text),
            )
            for point in points
        ],
    )


@dataclass(frozen=True)
class TitleKpi:
    label: str
    value: str
    change: Delta | None
    previous: str


def title_kpis(totals: TitleTotals) -> list[TitleKpi]:
    cards = (
        ("Читатели", totals.readers, format_number),
        ("Прочитано глав", totals.chapters, format_number),
        ("Время чтения", totals.seconds, format_duration),
        ("Добавили в библиотеку", totals.library_adds, format_number),
    )
    return [
        TitleKpi(
            label=label,
            value=fmt(comparison.current),
            change=delta(comparison.current, comparison.previous),
            previous=f"было {fmt(comparison.previous)}",
        )
        for label, comparison, fmt in cards
    ]


def title_list_rows(
    rows: list[TitleRow],
    summaries: Mapping[str, TitleSummary],
    contents: Mapping[str, list[Volume]],
    positions: Mapping[str, list[tuple[str, str]]],
) -> list[TitleListRow]:
    items = []
    for index, row in enumerate(rows):
        summary = summaries.get(row.slug_url)
        share: str | None = None
        if index < FINISHED_TOP:
            volumes = contents.get(row.slug_url)
            result = finished(volumes, positions.get(row.slug_url, [])) if volumes else None
            share = result.text if result else "нет данных"
        comparison = row.comparison
        items.append(
            TitleListRow(
                slug_url=row.slug_url,
                name=summary.name if summary else row.slug_url,
                cover_url=summary.cover_url if summary else None,
                href=f"/admin/analytics/{row.slug_url}",
                readers=format_number(row.readers),
                trend=delta(comparison.current, comparison.previous),
                chapters=format_number(row.chapters),
                library=format_number(row.library_adds),
                time=format_duration(row.seconds),
                finished=share,
            )
        )
    return items
