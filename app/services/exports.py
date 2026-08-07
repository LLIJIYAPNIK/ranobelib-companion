"""Which export formats are on offer, and the shared temp-file lifecycle for exporting."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from ranobelib.exporters import EXPORTERS


def available_export_formats() -> list[str]:
    """Formats this deployment can export to, e.g. `["epub", "fb2", "txt"]`.

    Sourced from `EXPORTERS` rather than a hardcoded list, so a format that isn't
    registered (e.g. "pdf" when WeasyPrint isn't installed) simply doesn't appear.
    """
    return sorted(EXPORTERS)


def require_known_format(fmt: str) -> None:
    """Reject a format that isn't registered, before it ever reaches the SDK.

    RanobeLib.export() raises a plain ValueError for this (not a RanobeLibError), which
    the central exception handler wouldn't catch - routes validate up front instead.
    """
    if fmt not in available_export_formats():
        raise HTTPException(status_code=400, detail="Неизвестный формат экспорта")


@contextmanager
def temp_export_path(fmt: str) -> Iterator[str]:
    """A temp file path to export to, removed if the block raises before finishing.

    On the happy path, the file is left behind for the caller to hand to a FileResponse
    (which deletes it itself, via a background task, once the response has been sent) or
    to a DownloadJob (which deletes it once the result has been downloaded) - this only
    cleans up the case export() fails partway through and nothing else gets to do that.
    """
    fd, path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    try:
        yield path
    except BaseException:
        os.remove(path)
        raise
