"""Comment body rendering (PR 148) - a deliberately small subset of Markdown (bold,
italic, strikethrough, links, lists), not full CommonMark, matching the roadmap's "минимум
достаточный для комментариев" scope. `comments.body` stays stored as raw Markdown
(migrations/0011_comments.sql is untouched); this module is only ever called at output
time, turning that stored text into HTML for the client to insert.

Two independent layers, neither trusted alone:
- `_MD` is built on markdown-it-py's "zero" preset with every rule disabled by default,
  then only the handful this feature needs re-enabled - raw HTML (`html_inline`/
  `html_block`) is never among them, so `<script>`/`onerror=` etc. in a comment come out
  as escaped text, never live markup, before sanitization even runs.
- `nh3.clean()` (Rust/ammonia bindings) still runs on the result regardless, allow-listing
  exactly the tags/attributes this feature renders and restricting link schemes to
  http/https/mailto. A Markdown renderer's own escaping is an implementation detail, not a
  security contract - sanitizing the HTML output is what actually makes untrusted
  `comment.body` safe to insert into the DOM.
"""

from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

_MD = MarkdownIt("zero", {"breaks": True}).enable(
    ["emphasis", "strikethrough", "link", "list", "newline", "blockquote"]
)

# PR 156: quoting - a paragraph-menu.js "Цитировать" trigger (on a chapter paragraph or an
# existing comment) inserts `> `-prefixed lines into the composer; nothing renders that
# without both the markdown-it rule above and this tag in the sanitizer's allow-list below.
_ALLOWED_TAGS = {"p", "strong", "em", "s", "a", "br", "ul", "ol", "li", "blockquote"}
_ALLOWED_ATTRIBUTES = {"a": {"href"}}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def render_comment_body(body: str) -> str:
    """Raw Markdown (as typed into the composer, PR 133) -> sanitized HTML safe to set as
    `innerHTML` on the client."""
    html = _MD.render(body)
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="nofollow noopener noreferrer ugc",
    )
