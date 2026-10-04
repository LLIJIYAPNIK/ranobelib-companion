"""PR 294: the «Библиотека / Каталог» switch (_library_switch.html, LibraryTabs.dc.html)."""

import re
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("count", [1, 4, 99, 999])
@pytest.mark.parametrize("active_tab", ["library", "catalog"])
def test_count_only_on_the_library_item(count: int, active_tab: str) -> None:
    html = _render(active_tab=active_tab, library_count=count)

    assert f'data-role="library-switch-count">{count}</span>' in _tab(html, "/library")
    assert "library-switch-count" not in _tab(html, "/catalog")


def test_no_count_for_a_guest() -> None:
    assert "library-switch-count" not in _render(active_tab="library", library_count=None)
    assert "library-switch-count" not in _render(active_tab="library")


def _rule(css: str, selector: str, *, last: bool = False) -> str:
    matches = re.findall(rf"\n(?:  )?{re.escape(selector)} \{{([^}}]*)\}}", css)
    assert matches, selector
    return matches[-1] if last else matches[0]


def test_desktop_geometry_is_compact_but_never_squeezes_content() -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    track = _rule(css, ".wn-lib-switch")
    assert "flex: 0 0 auto;" in track
    assert "width: max-content;" in track
    assert "max-width: 100%;" in track
    assert "border-radius: 15px;" in track
    assert "background: rgb(8 8 12 / 55%);" in track
    tab = _rule(css, ".wn-lib-switch__tab")
    assert "flex: 0 0 auto;" in tab
    assert "min-width: 142px;" in tab
    assert "min-height: 44px;" in tab
    assert "height: 44px;" in tab
    assert "padding: 0 18px;" in tab
    assert "border-radius: 11px;" in tab


def test_labels_and_counts_do_not_shrink_or_wrap_at_large_text_sizes() -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    label = _rule(css, ".wn-lib-switch__tab > span:not(.wn-lib-switch__count)")
    count = _rule(css, ".wn-lib-switch__count")

    assert "flex: none;" in label
    assert "white-space: nowrap;" in label
    assert "flex: none;" in count
    assert "min-width: 22px;" in count


@pytest.mark.parametrize("viewport_width", [320, 375])
def test_phone_geometry_splits_the_available_width(viewport_width: int) -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    track = _rule(css, ".wn-lib-switch", last=True)
    tab = _rule(css, ".wn-lib-switch__tab", last=True)
    icon = _rule(css, ".wn-lib-switch__tab svg", last=True)

    assert viewport_width <= 767
    assert "display: grid;" in track
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in track
    assert "width: 100%;" in track
    assert "width: 100%;" in tab
    assert "min-width: 0;" in tab
    assert "padding: 0 6px;" in tab
    assert "font-size: 14px;" in tab
    assert "display: none;" in icon
    mobile_count = _rule(css, ".wn-lib-switch__count", last=True)
    assert "padding-inline: 5px;" in mobile_count


def test_hover_focus_and_active_states_do_not_change_geometry() -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")

    assert '.wn-lib-switch__tab:not([aria-selected="true"]):hover' in css
    assert ".wn-lib-switch__tab:focus-visible" in css
    assert '.wn-lib-switch__tab:not([aria-selected="true"]):active' in css
    assert '.wn-lib-switch__tab[aria-selected="true"]:active' in css
    for selector in (
        '.wn-lib-switch__tab:not([aria-selected="true"]):hover',
        ".wn-lib-switch__tab:focus-visible",
        '.wn-lib-switch__tab:not([aria-selected="true"]):active',
        '.wn-lib-switch__tab[aria-selected="true"]:active',
    ):
        rule = _rule(css, selector)
        assert "width:" not in rule
        assert "padding:" not in rule


def test_library_and_catalog_templates_use_the_same_switch_partial() -> None:
    for name in ("library.html", "catalog.html"):
        source = (ROOT / "app/templates" / name).read_text(encoding="utf-8")
        assert '{% include "_library_switch.html" %}' in source
