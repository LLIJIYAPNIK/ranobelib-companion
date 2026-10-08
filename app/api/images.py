"""Proxying image downloads from ranobelib.me/cdnlibs.org (PR 143).

.image-lightbox__download (app/static/js/image-lightbox.js) used to be a plain
`<a download href="...">` pointing straight at the image's original URL - but browsers
only honor the `download` attribute for same-origin (or CORS-permitting) targets, and
these images are hotlinked straight from ranobelib.me/its cdnlibs.org CDN, not proxied or
re-uploaded by this app (the SDK/API is read-only, see CLAUDE.md "Что явно не делать").
Cross-origin, `download` is silently ignored and the browser just navigates to the image
instead of saving it. Routing the link through this same-origin endpoint instead - which
fetches the bytes itself and re-serves them with `Content-Disposition: attachment` - makes
`download` work the same way for every browser regardless of the CDN's own CORS headers,
which are outside this app's control and could change.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

router = APIRouter()

# The two domains chapter.content/cover images (PR 6/16) are actually hosted on - chapter
# images are hotlinked straight to ranobelib.me itself (e.g. ranobelib.me/uploads/ranobe/
# ...), covers to a cdnlibs.org subdomain (e.g. cover.cdnlibs.org) - both are the site's
# own official domains (see SITE_BASE_URL/API_BASE_URL in the SDK's models.py/client.py),
# never anything this app doesn't already trust elsewhere. Not just "ranobelib.me" alone,
# or every cover download would 400.
_ALLOWED_HOSTS = ("ranobelib.me", "cdnlibs.org")

# cover.cdnlibs.org 403s a plain, header-less request - it's checking Referer as
# anti-hotlink protection (verified directly against the live CDN), same as most image
# CDNs do. ranobelib.me's own upload host doesn't seem to require it, but sending it
# unconditionally for every allowed host is simpler than branching on which one this
# particular URL happens to be, and a same-site Referer is exactly what a real visitor's
# browser would have sent anyway if the app didn't proxy this at all.
_REFERER = "https://ranobelib.me/"


@router.get("/images/download")
async def download_image(url: Annotated[str, Query()]) -> Response:
    """Fetches `url` itself and re-serves it as a same-origin attachment - not an open
    proxy: `_is_allowed_image_url` rejects anything outside the site's own domains, so
    this can't be used to fetch/relay arbitrary third-party URLs through this server."""
    response = await _fetch(url, failure="Не удалось скачать изображение")
    filename = urlsplit(url).path.rsplit("/", 1)[-1] or "image"
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Raster formats only: an SVG served from our own origin is a document that can carry
# script, and nothing in chapter content needs one.
_VIEWABLE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"})
_VIEW_CACHE_CONTROL = "public, max-age=604800"


@router.get("/images/view")
async def view_image(url: Annotated[str, Query()]) -> Response:
    """PR 329: the same proxy, but inline - what a chapter saved for offline reading
    points its <img> at (app/api/offline.py). A hotlinked cross-origin image can only be
    stored opaque (unreadable, and padded against the storage quota); a same-origin copy
    is an ordinary response the browser can keep beside the chapter's text. Same host
    allowlist as /images/download; only a raster image type is passed through."""
    response = await _fetch(url, failure="Не удалось загрузить изображение")
    media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in _VIEWABLE_TYPES:
        raise HTTPException(status_code=502, detail="Не удалось загрузить изображение")
    return Response(
        content=response.content,
        media_type=media_type,
        headers={"Cache-Control": _VIEW_CACHE_CONTROL},
    )


async def _fetch(url: str, *, failure: str) -> httpx.Response:
    if not _is_allowed_image_url(url):
        raise HTTPException(status_code=400, detail="Недопустимый адрес изображения")

    async with httpx.AsyncClient() as client:
        try:
            # No follow_redirects: a redirect could point anywhere, and re-validating
            # every hop is more complexity than this proxy needs for what's ultimately a
            # fixed, small set of known-good source URLs already embedded in our own
            # server-rendered pages.
            response = await client.get(url, timeout=15, headers={"Referer": _REFERER})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=failure) from exc
    return response


def _is_allowed_image_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in _ALLOWED_HOSTS)
