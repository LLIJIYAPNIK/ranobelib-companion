"""Finding and rewriting the images in a chapter's HTML, for offline copies (PR 329).

TODO(https://github.com/LLIJIYAPNIK/ranobelib-python-sdk/issues/76): temporary - this is
content parsing, which belongs in the SDK. SDK 0.12.0 only has it privately
(``ranobelib.exporters._illustrations.extract_image_urls``) and nothing rewrites a
``src``; replace both functions with the SDK's public helpers once that issue ships.

Kept deliberately small. The SDK already sanitizes ``Chapter.content``/footnotes and
writes every image as one ``<img loading="lazy" src="..." />`` tag, so this only finds
those tags and swaps each one, verbatim as the SDK wrote it, for a new tag with the
rewritten ``src``.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from html.parser import HTMLParser


class _ImageTags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, str]] = []  # (the tag as written, its decoded src)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            src = dict(attrs).get("src")
            raw = self.get_starttag_text()
            if src and raw:
                self.tags.append((raw, src))

    handle_startendtag = handle_starttag


def _image_tags(fragment: str) -> list[tuple[str, str]]:
    parser = _ImageTags()
    parser.feed(fragment)
    parser.close()
    return parser.tags


def image_urls(*fragments: str) -> list[str]:
    """Every ``<img src>`` in the fragments, in document order, without repeats."""
    return list(dict.fromkeys(src for fragment in fragments for _, src in _image_tags(fragment)))


def rewrite_images(fragment: str, rewrite: Callable[[str], str]) -> str:
    """``fragment`` with each ``<img>`` pointing at ``rewrite(src)`` instead."""
    for raw, src in dict(_image_tags(fragment)).items():
        new_src = html.escape(rewrite(src), quote=True)
        fragment = fragment.replace(raw, f'<img loading="lazy" src="{new_src}" />')
    return fragment
