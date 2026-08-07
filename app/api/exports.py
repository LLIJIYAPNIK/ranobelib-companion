"""Exporting to a downloadable file - single/selected chapters, or whole volumes."""

import os
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.services.client import open_client
from app.services.exports import require_known_format, temp_export_path

router = APIRouter(prefix="/titles/{slug_url}")


@router.get("/chapters/{volume}/{number}/export")
async def export_chapter(
    slug_url: str,
    volume: int,
    number: str,
    fmt: str,
    branch_id: int | None = Query(default=None),
) -> FileResponse:
    require_known_format(fmt)
    with temp_export_path(fmt) as path:
        async with open_client(slug_url) as lib:
            chapter = await lib.get_chapter(volume, number, branch_id=branch_id)
            await lib.export([chapter], fmt=fmt, path=path)
    return FileResponse(
        path,
        filename=f"{slug_url}--{volume}-{number}.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


@router.get("/export")
async def export_chapters(
    slug_url: str,
    fmt: str,
    chapters: Annotated[list[str], Query()],
) -> FileResponse:
    require_known_format(fmt)
    parsed = [_parse_chapter_key(key) for key in chapters]
    with temp_export_path(fmt) as path:
        async with open_client(slug_url) as lib:
            fetched = await lib.get_chapters(parsed)
            await lib.export(fetched, fmt=fmt, path=path)
    return FileResponse(
        path,
        filename=f"{slug_url}--{len(fetched)}-chapters.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


@router.get("/volumes/{volume}/export")
async def export_volume(slug_url: str, volume: int, fmt: str) -> FileResponse:
    require_known_format(fmt)
    with temp_export_path(fmt) as path:
        async with open_client(slug_url) as lib:
            fetched = await lib.get_volume(volume)
            await lib.export(fetched.chapters, fmt=fmt, path=path)
    return FileResponse(
        path,
        filename=f"{slug_url}--volume-{volume}.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


@router.get("/volumes/export")
async def export_volumes(
    slug_url: str,
    fmt: str,
    volumes: Annotated[list[int], Query()],
) -> FileResponse:
    require_known_format(fmt)
    with temp_export_path(fmt) as path:
        async with open_client(slug_url) as lib:
            fetched = await lib.get_volumes(volumes)
            chapters = [chapter for vol in fetched for chapter in vol.chapters]
            await lib.export(chapters, fmt=fmt, path=path)
    return FileResponse(
        path,
        filename=f"{slug_url}--{len(fetched)}-volumes.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


def _parse_chapter_key(key: str) -> tuple[int, str]:
    """Parse a `"{volume}--{number}"` checkbox value (see title.html) into the
    `(volume, number)` pair `RanobeLib.get_chapters()` expects."""
    volume, separator, number = key.partition("--")
    if not separator:
        raise HTTPException(status_code=400, detail="Некорректный список глав")
    try:
        return int(volume), number
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный список глав") from exc
