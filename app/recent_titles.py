"""Cookie-backed list of recently opened titles.

No server-side storage and no accounts (see CLAUDE.md, "Авторизация" — not implemented,
not planned), so "history" is just a small JSON array in a cookie on the visitor's own
browser, most-recent first.

The payload is base64-encoded, not percent-encoded (see issue #205): `urllib.parse.quote`
with `safe=""` escapes every UTF-8 byte of a Cyrillic title as `%XX` - three ASCII bytes
per encoded byte, i.e. six characters per Cyrillic letter. Eight full-length titles (plus
their cover URLs) pushed the resulting `Set-Cookie` header past nginx's default single
response-header buffer (~4-8k), which nginx surfaces as "upstream sent too big header" in
its error log and a 502 to the visitor. `_MAX_NAME_LENGTH` caps the other side of the same
problem - a single title long enough on its own to blow the budget - while base64 (~4/3
bytes per input byte, regardless of script) replaces the 3x-6x percent-encoding blowup.
"""

from __future__ import annotations

import base64
import json

from starlette.requests import Request
from starlette.responses import Response

_COOKIE_NAME = "recent_titles"
_MAX_ENTRIES = 8
_MAX_NAME_LENGTH = 100
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
        # base64.urlsafe_b64decode raises binascii.Error (a ValueError subclass) on bad
        # padding/charset; raw.encode("ascii") raises UnicodeEncodeError (also a
        # ValueError subclass) on a tampered cookie containing non-ASCII bytes.
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
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
    if len(name) > _MAX_NAME_LENGTH:
        name = name[: _MAX_NAME_LENGTH - 1].rstrip() + "…"
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
    payload = json.dumps(
        entries[:_MAX_ENTRIES], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    response.set_cookie(
        _COOKIE_NAME,
        base64.urlsafe_b64encode(payload).decode("ascii"),
        max_age=_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
