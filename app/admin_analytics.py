"""«Аналитика» (PR 346): the titles list and a title's page, shaped for
admin/analytics.html and admin/analytics_title.html from app/db/admin_analytics.py and
the SDK data app/services/ resolves. Presentation only, like app/admin_overview.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ranobelib.models import Volume

from app.admin_kit import Column, Delta, delta, format_number
from app.admin_overview import format_duration
from app.db.admin_analytics import TitleRow
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
