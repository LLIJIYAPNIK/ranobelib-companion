"""Sanitizes chapter HTML before rendering it in the browser.

TODO(ranobelib-python-sdk#31): Chapter.content is only guaranteed sanitized on the
prosemirror-JSON API response path (see ranobelib/models.py, _normalize_content /
_prosemirror_to_html) - when the API instead returns content as a raw HTML string, the
SDK passes it through untouched. Sanitize again here as a stopgap until that's closed
upstream; drop this module once Chapter.content's safety is guaranteed for both response
shapes.
"""

import nh3

_ALLOWED_TAGS = {"p", "strong", "em", "br", "hr", "img"}
_ALLOWED_ATTRIBUTES = {"img": {"src", "alt", "loading"}}


def sanitize_chapter_html(raw: str) -> str:
    """Restrict `raw` to the tag/attribute set ranobelib.me chapter content ever uses."""
    return nh3.clean(raw, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES)
