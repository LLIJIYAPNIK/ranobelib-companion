"""GET /manifest.webmanifest (PR 327) - the Web App Manifest that makes the site
installable - and the service worker with its offline page (PR 328).

Served by a route rather than from /static: it needs the right media type
(``application/manifest+json``, which Python's mimetypes doesn't know everywhere), and its
icon URLs carry the same content hashes as every other static asset (PR 317), so an
updated icon reaches already-installed copies. Colors are the Webnovells canvas token
(--wn-canvas), so the splash screen and the browser bars match the site's own background.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.static_assets import NO_CACHE, STATIC_DIR, asset_hash
from app.templating import templates

router = APIRouter()

THEME_COLOR = "#0c0c12"  # --wn-canvas in app.css

SERVICE_WORKER_SOURCE = Path(__file__).parents[1] / "pwa" / "service-worker.js"
OFFLINE_URL = "/offline"
OFFLINE_TEMPLATE = Path(__file__).parents[1] / "templates" / "offline.html"


def _static(path: str) -> str:
    version = asset_hash(path)
    return f"/static/{path}?v={version}" if version else f"/static/{path}"


def manifest() -> dict[str, object]:
    icons = [
        {"src": _static(f"icons/icon-{size}.png"), "sizes": f"{size}x{size}", "type": "image/png"}
        for size in (192, 512)
    ] + [
        {
            "src": _static(f"icons/icon-maskable-{size}.png"),
            "sizes": f"{size}x{size}",
            "type": "image/png",
            "purpose": "maskable",
        }
        for size in (192, 512)
    ]
    shortcut_icon = [
        {"src": _static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"}
    ]
    return {
        "id": "/",
        "name": "Webnovells",
        "short_name": "Webnovells",
        "description": "Чтение и скачивание ранобэ",
        "lang": "ru",
        "dir": "ltr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": THEME_COLOR,
        "background_color": THEME_COLOR,
        "icons": icons,
        "shortcuts": [
            {"name": "Библиотека", "url": "/library", "icons": shortcut_icon},
            {"name": "Каталог", "url": "/catalog", "icons": shortcut_icon},
            {"name": "Загрузки", "url": "/downloads", "icons": shortcut_icon},
        ],
    }


@router.get("/manifest.webmanifest", include_in_schema=False)
async def web_app_manifest() -> Response:
    return Response(
        json.dumps(manifest(), ensure_ascii=False),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


def service_worker_config() -> dict[str, object]:
    """What the route prepends to app/pwa/service-worker.js as ``self.SW_CONFIG``. The
    version hashes everything the worker's behaviour depends on, its own source included,
    so a change to any of it is a new worker the browser installs."""
    precache = precache_urls()
    digest = hashlib.sha256(SERVICE_WORKER_SOURCE.read_bytes())
    digest.update(OFFLINE_TEMPLATE.read_bytes())
    digest.update("\n".join(precache).encode())
    return {
        "version": digest.hexdigest()[:10],
        "precache": precache,
        "offline": OFFLINE_URL,
        # PR 332: importScripts()'d by the worker - a ?v= URL, so a changed queue is a
        # changed precache list and so a new worker version.
        "syncQueue": _static("js/sync-queue.js"),
    }


def precache_urls() -> list[str]:
    """The offline page, and every stylesheet and script under app/static at the same
    ``?v=`` URL static_url() gives the pages (PR 317) - so the precached copy is exactly
    what a page asks for, and a changed file is a new URL (and a new worker version)."""
    paths = sorted(
        file.relative_to(STATIC_DIR).as_posix()
        for pattern in ("css/*.css", "js/*.js")
        for file in STATIC_DIR.glob(pattern)
    )
    return [OFFLINE_URL, *(_static(path) for path in paths)]


@router.get("/service-worker.js", include_in_schema=False)
async def service_worker() -> Response:
    """From the site root rather than /static, so its scope can be "/" (a worker only
    controls pages under its own path). ``no-cache``: the browser checks it for updates
    on every navigation anyway, but an HTTP-cached copy must never delay a new version."""
    config = json.dumps(service_worker_config(), ensure_ascii=False)
    body = f"self.SW_CONFIG = {config};\n" + SERVICE_WORKER_SOURCE.read_text(encoding="utf-8")
    return Response(body, media_type="text/javascript", headers={"Cache-Control": NO_CACHE})


@router.get(OFFLINE_URL, include_in_schema=False, response_class=HTMLResponse)
async def offline_page(request: Request) -> HTMLResponse:
    """«Нет соединения» - precached by the service worker, which serves it for a page
    that couldn't load. The same for everyone (no user data), so caching it is safe."""
    return templates.TemplateResponse(request, "offline.html")
