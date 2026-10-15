"""A chapter's paragraphs as plain text, for the admin panel's comment drawer (PR 350).

TODO(https://github.com/LLIJIYAPNIK/ranobelib-python-sdk/issues/77): temporary - this is
content parsing, which belongs in the SDK. SDK 0.12.0 has no public way to split
``Chapter.content`` into paragraphs; replace ``top_level_texts()`` with the SDK's
``chapter_paragraphs()`` once that issue ships.

The index has to match the reader's: paragraph-menu.js numbers a paragraph by its
position among ``.reader-content``'s children, i.e. the top-level elements of
``Chapter.content`` (footnotes are a separate field and come after them). So this counts
top-level elements only - stray top-level text isn't a child element and isn't counted.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser

import httpx
from ranobelib import RanobeLibError

from app.services.client import open_client

logger = logging.getLogger(__name__)

# Elements with no end tag - at the top level each one is a child of its own.
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
# Inside a block these break the text - «a</p><p>b» reads «a b», not «ab».
_BREAKS = frozenset(
    {"blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "td", "tr"}
)


class _TopLevel(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[list[str]] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth == 0:
            self.blocks.append([])
        elif tag in _BREAKS:
            self.blocks[-1].append(" ")
        if tag not in _VOID:
            self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth == 0:
            self.blocks.append([])
        elif tag in _BREAKS:
            self.blocks[-1].append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag not in _VOID and self.depth > 0:
            self.depth -= 1
            if self.depth > 0 and tag in _BREAKS:
                self.blocks[-1].append(" ")

    def handle_data(self, data: str) -> None:
        if self.depth > 0 and self.blocks:
            self.blocks[-1].append(data)


def top_level_texts(content: str) -> list[str]:
    """One entry per top-level element of ``content``, in order: its text with whitespace
    collapsed ("" for an image-only block)."""
    parser = _TopLevel()
    parser.feed(content)
    parser.close()
    return [" ".join("".join(block).split()) for block in parser.blocks]


async def paragraph_text(
    slug_url: str, volume: str, number: str, branch_id: str, index: int
) -> str | None:
    """The text of paragraph ``index`` (0-based, as comments store it), or None when it
    can't be had - the chapter is gone or ambiguous, ranobelib.me is unreachable, or the
    chapter now has fewer paragraphs. It's optional context in the admin drawer, so any
    of these is logged and the drawer just goes without the quote."""
    try:
        async with open_client(slug_url) as lib:
            chapter = await lib.get_chapter(
                int(volume), number, branch_id=int(branch_id) if branch_id else None
            )
    except (RanobeLibError, httpx.HTTPError, ValueError):
        logger.warning(
            "Could not load %s %s/%s for the admin comment drawer", slug_url, volume, number
        )
        return None
    texts = top_level_texts(chapter.content or "")
    return texts[index] if 0 <= index < len(texts) else None
