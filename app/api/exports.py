"""Exporting to a downloadable file - single/selected chapters, or whole volumes."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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
    with _temp_export_path(fmt) as path:
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
    with _temp_export_path(fmt) as path:
        async with get_client(slug_url) as lib:
            fetched = await lib.get_chapters(parsed)
            await lib.export(fetched, fmt=fmt, path=path)
    return FileResponse(
        path,
        filename=f"{slug_url}--{len(fetched)}-chapters.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


@router.get("/volumes/{volume}/export")
async def export_volume(slug_url: str, volume: int, fmt: str) -> FileResponse:
    _require_known_format(fmt)
    with _temp_export_path(fmt) as path:
        async with get_client(slug_url) as lib:
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
    _require_known_format(fmt)
    with _temp_export_path(fmt) as path:
        async with get_client(slug_url) as lib:
            fetched = await lib.get_volumes(volumes)
            chapters = [chapter for vol in fetched for chapter in vol.chapters]
            await lib.export(chapters, fmt=fmt, path=path)
    return FileResponse(
        path,
        filename=f"{slug_url}--{len(fetched)}-volumes.{fmt}",
        background=BackgroundTask(os.remove, path),
    )


@contextmanager
def _temp_export_path(fmt: str) -> Iterator[str]:
    """A temp file path to export to, removed if the block raises before finishing.

    On the happy path, the file is left behind for the route to hand to FileResponse,
    which deletes it itself (via a background task) once the response has been sent -
    this only cleans up the case get_chapter(s)/export() fails partway through and no
    response ever gets to do that.
    """
    fd, path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    try:
        yield path
    except BaseException:
        os.remove(path)
        raise


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
