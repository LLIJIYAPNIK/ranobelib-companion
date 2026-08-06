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


def read_recent(request: Request) -> list[dict[str, str]]:
    """Recently opened titles from the cookie, most recent first. Never raises on bad
    cookie content - a malformed/tampered cookie is just treated as an empty history."""
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
        item
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("slug_url"), str)
        and isinstance(item.get("name"), str)
    ]


def remember(response: Response, request: Request, *, slug_url: str, name: str) -> None:
    """Move `slug_url` to the front of the recent-titles cookie on `response`."""
    recent = [item for item in read_recent(request) if item["slug_url"] != slug_url]
    recent.insert(0, {"slug_url": slug_url, "name": name})
    payload = json.dumps(recent[:_MAX_ENTRIES], ensure_ascii=False)
    response.set_cookie(
        _COOKIE_NAME,
        quote(payload),
        max_age=_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
