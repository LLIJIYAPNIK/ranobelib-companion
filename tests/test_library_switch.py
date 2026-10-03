"""PR 294: the «Библиотека / Каталог» switch (_library_switch.html, LibraryTabs.dc.html)."""

import re
from pathlib import Path

from app.templating import templates

ROOT = Path(__file__).resolve().parents[1]


def _render(**context: object) -> str:
    return templates.env.get_template("_library_switch.html").render(**context)


def _tab(html: str, href: str) -> str:
    match = re.search(rf'<a\s[^>]*href="{re.escape(href)}"[^>]*>.*?</a>', html, re.S)
    assert match is not None, href
    return match.group(0)


def test_switch_is_a_tablist_of_two_route_links() -> None:
    html = _render(active_tab="library", library_count=3)

    assert 'role="tablist" aria-label="Разделы"' in html
    assert html.count('role="tab"') == 2
    assert ">Библиотека</span>" in _tab(html, "/library")
    assert ">Каталог</span>" in _tab(html, "/catalog")


def test_active_item_is_selected_and_the_only_tab_stop() -> None:
    html = _render(active_tab="catalog", library_count=3)

    catalog, library = _tab(html, "/catalog"), _tab(html, "/library")
    assert 'aria-selected="true"' in catalog
    assert 'tabindex="0"' in catalog
    assert 'aria-current="page"' in catalog
    assert "library-tabs__link--active" in catalog
    assert 'aria-selected="false"' in library
    assert 'tabindex="-1"' in library
    assert "aria-current" not in library


def test_count_only_on_the_library_item() -> None:
    html = _render(active_tab="catalog", library_count=12)

    assert 'data-role="library-switch-count">12</span>' in _tab(html, "/library")
    assert "library-switch-count" not in _tab(html, "/catalog")


def test_no_count_for_a_guest() -> None:
    assert "library-switch-count" not in _render(active_tab="library", library_count=None)
    assert "library-switch-count" not in _render(active_tab="library")


def test_styles_follow_the_design() -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    track = css.split(".wn-lib-switch {", 1)[1].split("}", 1)[0]
    assert "border-radius: 15px;" in track
    assert "background: rgb(8 8 12 / 55%);" in track
    tab = css.split(".wn-lib-switch__tab {", 1)[1].split("}", 1)[0]
    assert "height: 40px;" in tab
    assert "padding: 0 18px;" in tab
    assert "border-radius: 11px;" in tab
