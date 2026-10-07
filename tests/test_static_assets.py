"""PR 317: content-hashed static URLs (static_url()) and their cache headers."""

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.static_assets as static_assets
from app.main import app
from app.static_assets import IMMUTABLE, NO_CACHE, asset_hash

client = TestClient(app)
TEMPLATES = Path(__file__).parents[1] / "app/templates"


@pytest.fixture
def static_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(static_assets, "STATIC_DIR", tmp_path)
    monkeypatch.setattr(static_assets, "_hashes", {})
    (tmp_path / "js").mkdir()
    return tmp_path


def _write(file: Path, content: str, mtime: int) -> None:
    file.write_text(content, encoding="utf-8")
    os.utime(file, ns=(mtime, mtime))


def test_hash_is_stable_while_the_file_does_not_change(static_dir: Path) -> None:
    _write(static_dir / "js/a.js", "one", 1_000_000_000)

    first = asset_hash("js/a.js")

    assert first is not None and re.fullmatch(r"[0-9a-f]{10}", first)
    assert asset_hash("js/a.js") == first


def test_hash_changes_when_the_file_does(static_dir: Path) -> None:
    _write(static_dir / "js/a.js", "one", 1_000_000_000)
    before = asset_hash("js/a.js")

    _write(static_dir / "js/a.js", "two", 2_000_000_000)

    assert asset_hash("js/a.js") != before


def test_same_content_gives_the_same_hash_even_after_a_touch(static_dir: Path) -> None:
    _write(static_dir / "js/a.js", "one", 1_000_000_000)
    before = asset_hash("js/a.js")

    _write(static_dir / "js/a.js", "one", 2_000_000_000)

    assert asset_hash("js/a.js") == before


def test_production_hashes_each_file_once(
    static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(static_assets, "get_settings", lambda: SimpleNamespace(is_production=True))
    _write(static_dir / "js/a.js", "one", 1_000_000_000)
    before = asset_hash("js/a.js")

    _write(static_dir / "js/a.js", "two", 2_000_000_000)

    assert asset_hash("js/a.js") == before


def test_missing_file_has_no_hash(static_dir: Path) -> None:
    assert asset_hash("js/missing.js") is None


def test_pages_link_css_and_js_with_the_current_hash() -> None:
    html = client.get("/login").text

    css = asset_hash("css/app.css")
    assert f"/static/css/app.css?v={css}" in html
    for path in re.findall(r"/static/((?:css|js)/[^\"'?]+)", html):
        assert f"/static/{path}?v={asset_hash(path)}" in html, path


def test_no_template_links_css_or_js_without_a_version() -> None:
    for template in TEMPLATES.rglob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert not re.search(r"url_for\('static', path='(css|js)/", source), template.name
        assert not re.search(r"[\"']/static/(css|js)/", source), template.name


def test_matching_version_is_cached_as_immutable() -> None:
    response = client.get(f"/static/css/app.css?v={asset_hash('css/app.css')}")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == IMMUTABLE


@pytest.mark.parametrize(
    "url",
    [
        "/static/css/app.css",
        "/static/css/app.css?v=0123456789",
        "/static/favicon/favicon-16x16.png",
    ],
)
def test_unversioned_or_stale_static_is_revalidated(url: str) -> None:
    response = client.get(url)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == NO_CACHE


def test_not_modified_keeps_no_cache() -> None:
    etag = client.get("/static/css/app.css").headers["ETag"]

    response = client.get("/static/css/app.css", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["Cache-Control"] == NO_CACHE


def test_html_is_no_cache() -> None:
    response = client.get("/login")

    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["Cache-Control"] == NO_CACHE


def test_json_responses_are_left_alone() -> None:
    assert "Cache-Control" not in client.get("/health").headers
