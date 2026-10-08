"""PR 327: the web app manifest, icons, install meta tags and the install card."""

import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).parents[1]
client = TestClient(app)


def _png(path: Path) -> tuple[int, int, int]:
    """(width, height, color type) from a PNG's IHDR - color type 2 is RGB (opaque),
    6 is RGBA."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    width, height = struct.unpack(">II", data[16:24])
    return width, height, data[25]


@pytest.fixture(scope="module")
def manifest() -> dict:
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/manifest+json"
    return response.json()


def test_manifest_identity_and_display(manifest: dict) -> None:
    assert manifest["name"] == "Webnovells"
    assert manifest["short_name"] == "Webnovells"
    assert manifest["lang"] == "ru"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == manifest["background_color"] == "#0c0c12"


def test_manifest_colors_are_the_canvas_token(manifest: dict) -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    assert f"--wn-canvas: {manifest['theme_color']};" in css


def test_icons_exist_at_their_declared_sizes(manifest: dict) -> None:
    purposes = set()
    for icon in manifest["icons"]:
        path = ROOT / "app" / icon["src"].split("?")[0].lstrip("/")
        width, height, color_type = _png(path)
        assert f"{width}x{height}" == icon["sizes"], icon
        assert "?v=" in icon["src"]  # content-hashed (PR 317)
        purpose = icon.get("purpose", "any")
        purposes.add((icon["sizes"], purpose))
        if purpose == "maskable":
            assert color_type == 2, "a maskable icon needs an opaque full-bleed background"
        assert client.get(icon["src"]).status_code == 200
    assert {
        ("192x192", "any"),
        ("512x512", "any"),
        ("192x192", "maskable"),
        ("512x512", "maskable"),
    } <= purposes


def test_apple_touch_icon_is_opaque() -> None:
    # iOS fills transparency with black, which swallowed the logo's dark half.
    width, height, color_type = _png(ROOT / "app/static/favicon/apple-touch-icon.png")
    assert (width, height) == (180, 180)
    assert color_type == 2


def test_shortcuts_point_at_real_pages(manifest: dict) -> None:
    shortcuts = {s["name"]: s["url"] for s in manifest["shortcuts"]}
    assert shortcuts == {"Библиотека": "/library", "Каталог": "/catalog", "Загрузки": "/downloads"}
    for url in shortcuts.values():
        assert client.get(url, follow_redirects=False).status_code != 404, url


def test_every_page_links_the_manifest_and_install_meta() -> None:
    head = client.get("/settings/reading").text.split("</head>")[0]
    for tag in (
        '<link rel="manifest" href="/manifest.webmanifest">',
        '<meta name="theme-color" content="#0c0c12">',
        '<meta name="apple-mobile-web-app-capable" content="yes">',
        '<meta name="apple-mobile-web-app-status-bar-style" content="black">',
        '<meta name="apple-mobile-web-app-title" content="Webnovells">',
        'rel="apple-touch-icon"',
    ):
        assert tag in head, tag


def test_install_is_offered_only_from_settings_and_never_as_a_banner() -> None:
    home = client.get("/").text
    settings = client.get("/settings/reading").text
    script = (ROOT / "app/static/js/pwa-install.js").read_text(encoding="utf-8")

    assert "js/pwa-install.js" in home and "js/pwa-install.js" in settings
    assert 'data-role="pwa-install"' not in home
    assert 'data-role="pwa-install" hidden' in settings
    assert "event.preventDefault()" in script  # holds Chrome's own mini-infobar back
    assert "На экран „Домой“" in script  # the iOS hint


def test_manifest_is_not_in_the_public_api_schema() -> None:
    assert "/manifest.webmanifest" not in client.get("/openapi.json").json()["paths"]
