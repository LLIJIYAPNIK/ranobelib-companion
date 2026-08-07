"""Exporting chapters to a downloadable file - a single chapter or several selected
ones combined into one file."""

import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.services.client import get_client
from app.services.exports import available_export_formats

router = APIRouter(prefix="/titles/{slug_url}")


@router.get("/chapters/{volume}/{number}/export")
async def export_chapter(
    slug_url: str,
    volume: int,
    number: str,
    fmt: str,
    branch_id: int | None = Query(default=None),
) -> FileResponse:
    _require_known_format(fmt)
    fd, path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    async with get_client(slug_url) as lib:
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
    _require_known_format(fmt)
    parsed = [_parse_chapter_key(key) for key in chapters]
    fd, path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    async with get_client(slug_url) as lib:
        fetched = await lib.get_chapters(parsed)
        await lib.export(fetched, fmt=fmt, path=path)
    return FileResponse(
        path,
        filename=f"{slug_url}--{len(fetched)}-chapters.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


def _require_known_format(fmt: str) -> None:
    if fmt not in available_export_formats():
        raise HTTPException(status_code=400, detail="Неизвестный формат экспорта")


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
