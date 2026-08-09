"""Reading-progress percentage for a title in the personal library (PR 14/27) - purely
web-layer arithmetic over the SDK's own chapter ordering (`get_table_of_contents()`), not
a reimplementation of chapter numbering/sorting itself (see CLAUDE.md's Golden Rule).
Same chapter-flattening app/api/chapters.py's `_adjacent_chapter_urls()` uses for
previous/next chapter links.
"""

from __future__ import annotations

from ranobelib.models import Volume


def reading_progress_percent(
    volumes: list[Volume], last_read_volume: str | None, last_read_number: str | None
) -> int | None:
    """Percentage of `volumes`' chapters read so far, based on where
    `(last_read_volume, last_read_number)` falls in the SDK's own chapter order.

    `None` when there's no recorded progress, no chapters to measure it against, or the
    last-read chapter isn't in the list anymore (e.g. removed upstream).
    """
    if last_read_volume is None or last_read_number is None:
        return None
    flat = [(volume.number, chapter.number) for volume in volumes for chapter in volume.chapters]
    if not flat:
        return None
    try:
        index = flat.index((last_read_volume, last_read_number))
    except ValueError:
        return None
    return round((index + 1) / len(flat) * 100)
