"""PR 309: titles enter the app through catalog search, never pasted URLs."""

from pathlib import Path

from app.api import library, titles

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app/templates"


def _source(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _has_route(path: str, method: str) -> bool:
    return any(
        getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
        for route in [*library.router.routes, *titles.router.routes]
    )


def test_home_and_library_use_native_get_catalog_search() -> None:
    # The search form lives in the home's shared macros (home/_macros.html); the library
    # («Полки») renders the same one.
    source = _source("home/_macros.html")
    assert 'method="get" action="/catalog" role="search"' in source
    assert 'type="search"' in source
    assert 'name="query"' in source
    assert 'enterkeyhint="search"' in source
    assert 'type="submit"' in source
    library = _source("library.html")
    assert 'from "home/_macros.html" import search_form' in library
    assert "{{ search_form() }}" in library


def test_legacy_url_entry_routes_are_not_registered() -> None:
    assert not _has_route("/titles/open", "GET")
    assert not _has_route("/library/add", "POST")
    assert _has_route("/library/{slug_url}/add", "POST")


def test_empty_states_and_downloads_open_the_catalog() -> None:
    assert _source("index.html").count('href="/catalog"') >= 2
    assert 'href="/catalog">Открыть каталог</a>' in _source("library.html")
    assert _source("downloads.html").count('href="/catalog"') >= 2


def test_user_facing_copy_does_not_name_the_upstream_domain() -> None:
    public_sources = [*TEMPLATES.rglob("*.html"), ROOT / "app/exceptions.py"]
    for source in public_sources:
        assert "ranobelib.me" not in source.read_text(encoding="utf-8").lower(), source
