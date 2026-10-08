"""GET /manifest.webmanifest (PR 327) - the Web App Manifest that makes the site
installable.

Served by a route rather than from /static: it needs the right media type
(``application/manifest+json``, which Python's mimetypes doesn't know everywhere), and its
icon URLs carry the same content hashes as every other static asset (PR 317), so an
updated icon reaches already-installed copies. Colors are the Webnovells canvas token
(--wn-canvas), so the splash screen and the browser bars match the site's own background.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response

from app.static_assets import asset_hash

router = APIRouter()

THEME_COLOR = "#0c0c12"  # --wn-canvas in app.css


def _icon(path: str) -> str:
    version = asset_hash(path)
    return f"/static/{path}?v={version}" if version else f"/static/{path}"


def manifest() -> dict[str, object]:
    icons = [
        {"src": _icon(f"icons/icon-{size}.png"), "sizes": f"{size}x{size}", "type": "image/png"}
        for size in (192, 512)
    ] + [
        {
            "src": _icon(f"icons/icon-maskable-{size}.png"),
            "sizes": f"{size}x{size}",
            "type": "image/png",
            "purpose": "maskable",
        }
        for size in (192, 512)
    ]
    shortcut_icon = [{"src": _icon("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"}]
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
