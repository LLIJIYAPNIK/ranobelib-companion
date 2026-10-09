"""Offline reading's server contract (PR 329): what the browser-side download manager
(PR 330) asks for to store a title on the device.

The server stores nothing here: every call goes to the SDK through ``open_client()``
exactly like the online reader - the same SDK disk cache, the same rate limiting, one call
after another, never fanned out. The copy kept on the device lives only in that browser
(Cache Storage/IndexedDB, PR 330) and the client fetches chapters strictly one at a time -
see CLAUDE.md, «Что явно не делать», on why that isn't a server-side chapter cache.

A chapter with several translations is never resolved here: ``get_chapter()`` without a
``branch_id`` raises ``MultipleTranslationsError``, which the central handler
(app/exceptions.py) turns into a 409 listing the branches - the client asks the visitor.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from ranobelib import RanobeLibError, chapter_size
from ranobelib.models import Volume

from app.api.chapters import load_reader_chapter, reader_context
from app.services.client import open_client
from app.services.offline import image_urls, rewrite_images
from app.templating import templates

router = APIRouter(prefix="/offline/titles/{slug_url}")


@router.get("/manifest")
async def offline_manifest(slug_url: str) -> JSONResponse:
    """The table of contents - every chapter with its translations, so the manager can
    show the list and ask about ambiguous ones up front - plus the SDK's sampled size
    estimate for the space check before a download starts."""
    async with open_client(slug_url) as lib:
        volumes = await lib.get_table_of_contents()
        try:
            estimated_bytes: int | None = await lib.estimate_title_size()
        except RanobeLibError:
            # Same as the title page's size label (app/api/titles.py): a sampled chapter
            # failing (rate limit, needs auth, ...) leaves the estimate out, not the list.
            estimated_bytes = None
    chapter_count = sum(len(volume.chapters) for volume in volumes)
    return JSONResponse(
        {
            "slug_url": slug_url,
            "chapter_count": chapter_count,
            "estimated_bytes": estimated_bytes or None,
            "estimated_bytes_per_chapter": (
                estimated_bytes // chapter_count if estimated_bytes and chapter_count else None
            ),
            "volumes": [_volume(slug_url, volume) for volume in volumes],
        },
        headers={"Cache-Control": "no-cache"},
    )


def _volume(slug_url: str, volume: Volume) -> dict[str, Any]:
    return {
        "number": volume.number,
        "chapters": [
            {
                "volume": chapter.volume,
                "number": chapter.number,
                "name": chapter.name,
                "url": f"/offline/titles/{slug_url}/chapters/{chapter.volume}/{chapter.number}",
                "branches": [
                    {
                        "branch_id": branch.branch_id,
                        "teams": [team.name for team in branch.teams],
                        "uploader": branch.user.username,
                    }
                    for branch in chapter.branches
                ],
            }
            for chapter in volume.chapters
        ],
    }


@router.get("/chapters/{volume}/{number}")
async def offline_chapter(
    slug_url: str, volume: int, number: str, branch_id: int | None = Query(default=None)
) -> JSONResponse:
    """One chapter as the device stores it: the same sanitized content and footnotes the
    reader renders, with every image pointed at the same-origin /images/view proxy so it
    can be stored too, and the list of those image URLs to fetch. ``branch_id`` is the
    translation the visitor picked - without one, an ambiguous chapter is a 409."""
    async with open_client(slug_url) as lib:
        chapter = await lib.get_chapter(volume, number, branch_id=branch_id)
    content = chapter.content or ""
    footnotes = [footnote.content for footnote in chapter.footnotes]
    return JSONResponse(
        {
            "slug_url": slug_url,
            "volume": chapter.volume,
            "number": chapter.number,
            "name": chapter.name,
            "branch_id": branch_id,
            "content": rewrite_images(content, _proxied),
            "footnotes": [rewrite_images(footnote, _proxied) for footnote in footnotes],
            "images": [_proxied(url) for url in image_urls(content, *footnotes)],
            "estimated_bytes": chapter_size(chapter) if chapter.content is not None else 0,
        },
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/chapters/{volume}/{number}/page", response_class=HTMLResponse)
async def offline_chapter_page(
    request: Request,
    slug_url: str,
    volume: int,
    number: str,
    branch_id: int | None = Query(default=None),
) -> HTMLResponse:
    """PR 331: the reader page itself, as the device keeps it - the service worker serves
    it for /titles/{slug}/chapters/{volume}/{number} when there's no network. The same
    chapter.html and context as the online reader (HUD, Aa, Tap Focus, footnotes, the
    end card and its neighbours in SDK order), with three differences:

    - nobody's page: rendered as for a guest - no account in the sidebar, no saved
      paragraph - so a stored copy never carries personal data (PR 328/333), and opening
      it records nothing by itself (unlike the reader route, which adds to the library and
      logs the read). Its progress/activity scripts (PR 332) only queue on the device what
      is read in it; the queue goes to the server later, as whoever is signed in then;
    - images on the same-origin proxy, stored beside the page (PR 329/330);
    - no comments, reactions or chapter export - they need the network
      (``offline_copy`` in the template)."""
    async with open_client(slug_url) as lib:
        chapter, volumes, title = await load_reader_chapter(lib, volume, number, branch_id)
    chapter = chapter.model_copy(
        update={
            "content": rewrite_images(chapter.content or "", _proxied),
            "footnotes": [
                footnote.model_copy(update={"content": rewrite_images(footnote.content, _proxied)})
                for footnote in chapter.footnotes
            ],
        }
    )
    request.state.current_user = None
    return templates.TemplateResponse(
        request,
        "chapter.html",
        {
            **reader_context(slug_url, str(volume), number, chapter, volumes, title, branch_id),
            "export_formats": [],
            "saved_paragraph": None,
            "saved_paragraph_total": None,
            "offline_copy": True,
        },
        headers={"Cache-Control": "no-cache"},
    )


def _proxied(url: str) -> str:
    return f"/images/view?url={quote(url, safe='')}"
