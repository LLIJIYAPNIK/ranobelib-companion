"""Content-hashed URLs for app/static CSS/JS and their cache headers (PR 317).

Neither app.css nor the page scripts used to carry a version in their URL or an explicit
``Cache-Control``, so after a deploy a phone could keep rendering new markup against the
``app.css`` it had cached before - exactly "the styles didn't load". ``static_url()``
(a Jinja global, see app/templating.py) appends ``?v=<short content hash>``; a request
whose ``v`` matches the file's current hash is cached for a year as immutable, everything
else from /static (no ``v``, or a stale one from an old page) is ``no-cache`` - always
revalidated, via the ETag StaticFiles already sends. HTML gets ``no-cache`` too
(``install_html_no_cache``), so a fresh page always points at fresh assets.

Hashes are computed lazily, once per file in production; in dev the cache is keyed on
mtime, so an edited file gets a new URL without a restart.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.config import get_settings

STATIC_DIR = Path(__file__).parent / "static"
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"

_HASH_LENGTH = 10
# path -> (mtime_ns, hash)
_hashes: dict[str, tuple[int, str]] = {}


def asset_hash(path: str) -> str | None:
    """Short content hash of app/static/<path>, or None if there is no such file."""
    cached = _hashes.get(path)
    if cached is not None and get_settings().is_production:
        return cached[1]
    file = STATIC_DIR / path
    try:
        mtime = os.stat(file).st_mtime_ns
    except OSError:
        return None
    if cached is not None and cached[0] == mtime:
        return cached[1]
    digest = hashlib.sha256(file.read_bytes()).hexdigest()[:_HASH_LENGTH]
    _hashes[path] = (mtime, digest)
    return digest


class VersionedStaticFiles(StaticFiles):
    """StaticFiles that sends ``immutable`` only for a ``?v=`` matching the file's hash."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            version = Request(scope).query_params.get("v")
            current = asset_hash(path) if version else None
            response.headers["Cache-Control"] = (
                IMMUTABLE if version and version == current else NO_CACHE
            )
        return response


def install_html_no_cache(app: FastAPI) -> None:
    """``Cache-Control: no-cache`` on every HTML response that didn't set its own."""

    @app.middleware("http")
    async def _html_no_cache(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if (
            response.headers.get("content-type", "").startswith("text/html")
            and "cache-control" not in response.headers
        ):
            response.headers["Cache-Control"] = NO_CACHE
        return response
