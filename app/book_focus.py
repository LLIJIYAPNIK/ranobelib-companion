"""A book in the reader's library, in depth, from the SDK: its description and status, where
the reader is in it (the current chapter's place among all of them) and the chapters from
there on. Shared by the home page's «Витрина» (app/api/home.py) and the library's shelves
(app/api/library.py), so both read a title the same way.

The current chapter is found by its volume and number exactly as the SDK gives them, in
the SDK's own order - no chapter-number parsing or sorting here (CLAUDE.md's Golden Rule).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.titles import title_details

# The chapters listed from the reader's own on.
NEXT_CHAPTERS = 4


@dataclass(frozen=True)
class BookChapter:
    volume: str
    number: str
    name: str | None
    is_current: bool


@dataclass(frozen=True)
class BookFocus:
    """A book on the shelf, from the SDK: its description, where the reader is in it, and
    the chapters from the current one on, in the SDK's order."""

    summary: str | None
    status: str | None
    position: int | None  # 1-based place of the current chapter, None if not found
    total: int
    chapters: list[BookChapter]
    # The title's first chapter in the SDK's order - where «Начать читать» and
    # «Перечитать» lead; None for a title with no chapters.
    first: BookChapter | None = None

    @property
    def left(self) -> int | None:
        """Chapters after the reader's own - None when their place isn't known."""
        return self.total - self.position if self.position is not None else None

    @property
    def finished(self) -> bool:
        """The reader is on the last chapter there is. An ongoing title stops being
        finished by itself once a new chapter comes out."""
        return self.position is not None and self.position == self.total


async def book_focus(item: dict[str, object], limit: int = NEXT_CHAPTERS) -> BookFocus | None:
    """A library item's book in depth; None when ranobelib.me can't be reached (the page
    then does without)."""
    entry = item["entry"]
    details = await title_details(entry.slug_url)  # type: ignore[attr-defined]
    if details is None:
        return None
    chapters = [chapter for volume in details.volumes for chapter in volume.chapters]
    current = next(
        (
            index
            for index, chapter in enumerate(chapters)
            if chapter.volume == entry.last_read_volume  # type: ignore[attr-defined]
            and chapter.number == entry.last_read_number  # type: ignore[attr-defined]
        ),
        None,
    )
    start = current or 0
    return BookFocus(
        summary=details.summary,
        status=details.status,
        position=current + 1 if current is not None else None,
        total=len(chapters),
        chapters=[
            BookChapter(
                volume=chapter.volume,
                number=chapter.number,
                name=chapter.name,
                is_current=index == current,
            )
            for index, chapter in enumerate(chapters[start : start + limit], start=start)
        ],
        first=BookChapter(
            volume=chapters[0].volume,
            number=chapters[0].number,
            name=chapters[0].name,
            is_current=current == 0,
        )
        if chapters
        else None,
    )
