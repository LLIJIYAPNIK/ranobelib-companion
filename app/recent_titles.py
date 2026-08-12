"""Cookie-backed list of recently opened titles.

No server-side storage and no accounts (see CLAUDE.md, "Авторизация" — not implemented,
not planned), so "history" is just a small JSON array in a cookie on the visitor's own
browser, most-recent first.
"""

from __future__ import annotations

import json
from urllib.parse import quote, unquote

from starlette.requests import Request
from starlette.responses import Response

_COOKIE_NAME = "recent_titles"
_MAX_ENTRIES = 8
_MAX_AGE_SECONDS = 180 * 24 * 60 * 60


def read_recent(request: Request) -> list[dict[str, str | None]]:
    """Recently opened titles from the cookie, most recent first. Never raises on bad
    cookie content - a malformed/tampered cookie is just treated as an empty history.

    `cover_url` is optional in the stored shape - cookies written before it existed just
    don't have it, and that's fine, not a reason to drop the whole entry (see PR 16).
    """
    raw = request.cookies.get(_COOKIE_NAME)
    if not raw:
        return []
    try:
        data = json.loads(unquote(raw))
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [
        {
            "slug_url": item["slug_url"],
            "name": item["name"],
            "cover_url": item.get("cover_url") if isinstance(item.get("cover_url"), str) else None,
        }
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("slug_url"), str)
        and isinstance(item.get("name"), str)
    ]


def remember(
    response: Response, request: Request, *, slug_url: str, name: str, cover_url: str | None = None
) -> None:
    """Move `slug_url` to the front of the recent-titles cookie on `response`."""
    recent = [item for item in read_recent(request) if item["slug_url"] != slug_url]
    recent.insert(0, {"slug_url": slug_url, "name": name, "cover_url": cover_url})
    _write_cookie(response, recent)


def forget(response: Response, request: Request, *, slug_url: str) -> None:
    """Remove `slug_url` from the recent-titles cookie on `response` - symmetric to
    `remember()` (PR 69's "×" on a "Недавние" card). Not an error if it wasn't there to
    begin with, e.g. a stale double-click."""
    recent = [item for item in read_recent(request) if item["slug_url"] != slug_url]
    _write_cookie(response, recent)


def _write_cookie(response: Response, entries: list[dict[str, str | None]]) -> None:
    payload = json.dumps(entries[:_MAX_ENTRIES], ensure_ascii=False)
    response.set_cookie(
        _COOKIE_NAME,
        # safe="" - a raw "/" (e.g. from a cover_url) is outside the charset Python's
        # http.cookies accepts unquoted, which otherwise makes it wrap the whole value in
        # a literal quoted-string that some cookie-jar implementations don't strip back
        # off on read.
        quote(payload, safe=""),
        max_age=_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
