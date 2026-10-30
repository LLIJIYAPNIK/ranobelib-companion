"""The title page's contents as the «Лампа» redesign shows them: each volume of
`get_table_of_contents()` with its reading state, and its chapters as a reading line where
the run already read before the reader's place and a long tail after it fold away.

Pure web-layer arrangement of what the SDK already returned, in the SDK's own order: no
chapter numbering, sorting or parsing here (CLAUDE.md's Golden Rule). The reader's place is
an index into that order, as app/api/titles.py's title_data() computes it for
reading_progress_percent().
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from ranobelib.models import Chapter, Volume

# A chapter whose newest translation came out this many days ago or less is «новая».
FRESH_DAYS = 14
# The reader's chapter keeps this many read chapters above it before the rest fold.
READ_KEPT = 2
# How many chapters a volume shows after the reader's place, or from its start, before
# its tail folds - and a tail this short or shorter is just shown.
SHOWN_AHEAD = 9
SHOWN_FROM_START = 12
TAIL_MIN = 4

_MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


@dataclass(frozen=True)
class ChapterRow:
    index: int
    chapter: Chapter
    state: str  # "read", "here" or ""
    fresh: str | None  # «новая · вчера», only for a chapter out in the last FRESH_DAYS days


@dataclass(frozen=True)
class Fold:
    """Chapters [start, end) of the flat order, folded under one head."""

    start: int
    end: int
    read: bool
    rows: list[ChapterRow]

    @property
    def count(self) -> int:
        return self.end - self.start

    @property
    def first_number(self) -> str:
        return self.rows[0].chapter.number

    @property
    def last_number(self) -> str:
        return self.rows[-1].chapter.number


@dataclass(frozen=True)
class VolumeView:
    number: str
    start: int
    chapter_count: int
    read_count: int
    is_current: bool
    items: list[ChapterRow | Fold] = field(default_factory=list)

    @property
    def read_percent(self) -> int:
        return round(self.read_count / self.chapter_count * 100) if self.chapter_count else 0

    @property
    def done(self) -> bool:
        return self.chapter_count > 0 and self.read_count >= self.chapter_count


def when(day: date, today: date) -> str:
    """«сегодня», «вчера», «3 дня назад», then «22 сен» this year and «13 апр 2018» before."""
    days = (today - day).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    if days < 7:
        word = "день" if days == 1 else "дня" if days < 5 else "дней"
        return f"{days} {word} назад"
    label = f"{day.day} {_MONTHS[day.month - 1]}"
    return label if day.year == today.year else f"{label} {day.year}"


def _fresh(chapter: Chapter, today: date) -> str | None:
    dates = [b.created_at.date() for b in chapter.branches if b.created_at is not None]
    if not dates:
        return None
    newest = max(dates)
    return f"новая · {when(newest, today)}" if (today - newest).days <= FRESH_DAYS else None


def volume_views(volumes: list[Volume], current_index: int | None, today: date) -> list[VolumeView]:
    """Every volume with its chapters arranged for the reading line.

    In the reader's volume the read run before their chapter folds (READ_KEPT stay in
    view); a volume read through folds whole; a long tail after what is shown folds too.
    Without a reading place nothing is read and every volume opens from its start.
    """
    at = -1 if current_index is None else current_index
    views: list[VolumeView] = []
    start = 0
    for volume in volumes:
        count = len(volume.chapters)
        end = start + count

        def row(i: int, _start: int = start, _volume: Volume = volume) -> ChapterRow:
            chapter = _volume.chapters[i - _start]
            state = "read" if i < at else "here" if i == at else ""
            return ChapterRow(index=i, chapter=chapter, state=state, fresh=_fresh(chapter, today))

        def fold(a: int, b: int, read: bool) -> Fold:
            return Fold(start=a, end=b, read=read, rows=[row(i) for i in range(a, b)])

        read_count = max(0, min(count, at - start))
        items: list[ChapterRow | Fold] = []
        shown_from = start
        if count and read_count >= count:
            items.append(fold(start, end, read=True))
            shown_from = end
        elif start < at < end and at - start > READ_KEPT + 1:
            items.append(fold(start, at - READ_KEPT, read=True))
            shown_from = at - READ_KEPT
        if shown_from < end:
            ahead = at + SHOWN_AHEAD if start <= at < end else shown_from + SHOWN_FROM_START
            until = min(end, ahead)
            if end - until < TAIL_MIN:
                until = end
            items.extend(row(i) for i in range(shown_from, until))
            if until < end:
                items.append(fold(until, end, read=False))

        views.append(
            VolumeView(
                number=volume.number,
                start=start,
                chapter_count=count,
                read_count=read_count,
                is_current=start <= at < end,
                items=items,
            )
        )
        start = end
    return views


def translators(volumes: list[Volume]) -> list[tuple[str, int]]:
    """Who translated how many chapters, most first - for a title whose every chapter has
    one translation, where there is no default to choose but the reader still sees the
    teams change from chapter to chapter."""
    counts: Counter[str] = Counter()
    for volume in volumes:
        for chapter in volume.chapters:
            for branch in chapter.branches:
                names = ", ".join(team.name for team in branch.teams)
                counts[names or (branch.user.username if branch.user else "")] += 1
    counts.pop("", None)
    return counts.most_common()
