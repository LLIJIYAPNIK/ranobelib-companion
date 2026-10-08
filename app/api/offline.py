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

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ranobelib import RanobeLibError
from ranobelib.models import Volume

from app.services.client import open_client

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
