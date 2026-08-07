"""Exporting chapters to a downloadable file (single chapter for now, see PR 8)."""

import os
import tempfile

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


def _require_known_format(fmt: str) -> None:
    if fmt not in available_export_formats():
        raise HTTPException(status_code=400, detail="Неизвестный формат экспорта")
