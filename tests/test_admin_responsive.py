"""PR 354: the admin panel on a tablet and a phone - touch targets of 44px for every kit and
shell control, the table's «Сортировка» bar where the header row is hidden (and that row
really hidden, so its links don't take focus unseen), wide tables reachable by keyboard,
and no admin animation without its prefers-reduced-motion override."""

import re
from pathlib import Path

from app.admin_kit import Column
from tests.test_admin_kit import _render

CSS = Path(__file__).resolve().parents[1].joinpath("app/static/css/app.css").read_text("utf-8")
KIT_JS = (
    Path(__file__).resolve().parents[1].joinpath("app/static/js/admin-kit.js").read_text("utf-8")
)
TOUCH = "@media (max-width: 1024px), (pointer: coarse) {"


def _block(start: str) -> str:
    """A media block from its opening line to its closing brace at column 0."""
    begin = CSS.index(start)
    return CSS[begin : CSS.index("\n}\n", begin)]


def _rules(block: str) -> dict[str, str]:
    """selector -> declarations, for a block's rules (a selector list counts for each)."""
    found: dict[str, str] = {}
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    for selectors, body in re.findall(r"\n  ([^{}@]+?)\s*\{([^{}]*)\}", block):
        for selector in selectors.split(","):
            found[selector.strip()] = found.get(selector.strip(), "") + body
    return found


# --- touch targets ------------------------------------------------------------------------


def test_every_kit_and_shell_control_is_44px_by_finger() -> None:
    rules = _rules(_block(TOUCH))

    full = (
        ".wn-admin-btn",
        ".wn-admin-input",
        ".wn-admin-select",
        ".wn-admin-search__input",
        ".wn-admin-search__toggle",
        ".wn-admin-top__menu",
        ".wn-admin-top__site",
        ".wn-admin-side__logout-btn",
        ".wn-admin-toast .wn-admin-iconbtn",
        ".wn-admin-pager__page",
        ".wn-admin-pager__step",
    )
    for selector in full:
        assert "height: 44px" in rules[selector], selector
        assert "min-width: 44px" in rules[selector], selector
    tall = (
        ".wn-admin-side__item",
        ".wn-admin-seg__item",
        ".wn-admin-toggle",
        ".wn-admin-chip",
        ".wn-admin-filters__reset",
        ".wn-admin-colmenu__item",
        ".wn-admin-table__sort",
        ".wn-admin-btn--link",
        ".wn-admin-search__option",
        ".wn-admin-table__td .wn-admin-cm__link",
        ".wn-admin-actions__title .wn-admin-cm__link",
    )
    for selector in tall:
        assert "min-height: 44px" in rules[selector], selector


def test_the_touch_sizes_come_last_so_they_win() -> None:
    # Every admin rule they override (heights of 32-38px) is defined earlier in the file.
    touch = CSS.index(TOUCH)
    for rule in (".wn-admin-btn {", ".wn-admin-select {", ".wn-admin-pager__page,"):
        assert CSS.index("\n" + rule) < touch, rule


# --- the table on a phone -----------------------------------------------------------------

COLUMNS = [
    Column("name", "Имя", locked=True),
    Column("id", "ID", "mono"),
    Column("status", "Статус", sortable=False),
    Column("actions", "Действия", "actions", sortable=False),
]
ROWS = [{"name": "aster", "id": 7, "status": "ok", "actions": ""}] * 2


def _table(rows: list[dict], **kwargs: object) -> str:
    return _render(
        '{{ kit.table("t", columns, rows, sort=sort, descending=descending, args=args,'
        ' caption="Пользователи") }}',
        columns=COLUMNS,
        rows=rows,
        sort=kwargs.get("sort"),
        descending=kwargs.get("descending", True),
        args=kwargs.get("args", {}),
    )


def test_the_sort_bar_has_the_header_links() -> None:
    html = _table(ROWS, sort="id", descending=True, args={"q": "a", "page": "3"})

    bar = re.search(r'<nav class="wn-admin-table__sortbar"[^>]*>(.*?)</nav>', html, re.S)
    assert 'aria-label="Сортировка: Пользователи"' in html
    links = re.findall(
        r'<a class="wn-admin-table__sortbar-item" href="([^"]+)"([^>]*)>', bar.group(1)
    )
    # Sortable columns only, the search kept, the page dropped; the active one flips.
    assert links == [
        ("?q=a&amp;sort=name&amp;order=desc", ""),
        ("?q=a&amp;sort=id&amp;order=asc", ' aria-current="true"'),
    ]
    assert "по убыванию" in bar.group(1)
    # The same links as the header row's, in the same order.
    header = re.findall(r'class="wn-admin-table__sort" href="([^"]+)"', html)
    assert header == [href for href, _ in links]


def test_no_sort_bar_with_nothing_to_sort() -> None:
    assert "wn-admin-table__sortbar" not in _table(ROWS[:1])
    assert "wn-admin-table__sortbar" not in _table([])


def test_on_a_phone_the_header_row_is_gone_and_the_sort_bar_shows() -> None:
    phone = _rules(_block("@media (max-width: 767px) {\n  /* Hidden for real"))

    assert "display: none" in phone[".wn-admin-table__table thead"]
    assert "display: flex" in phone[".wn-admin-table__sortbar"]
    assert "min-height: 44px" in phone[".wn-admin-table__sortbar-item"]
    # Nowhere is the header row just clipped any more (its links would keep focus).
    assert not re.search(r"thead \{\s*position: absolute;", CSS)
    # Off a phone the bar isn't there at all.
    assert re.search(r"\n\.wn-admin-table__sortbar \{\s*display: none;", CSS)


def test_a_wide_table_is_reachable_by_keyboard() -> None:
    html = _table(ROWS)

    assert 'data-kit-table="t" data-kit-label="Пользователи"' in html
    # Made a focusable, named region by the kit's script only while it scrolls sideways.
    assert "wrap.scrollWidth > wrap.clientWidth" in KIT_JS
    assert 'wrap.setAttribute("role", "region")' in KIT_JS
    assert 'wrap.removeAttribute("tabindex")' in KIT_JS
    assert ".wn-admin-table[tabindex]:focus-visible" in CSS


# --- motion -------------------------------------------------------------------------------


def test_every_admin_animation_has_a_reduced_motion_override() -> None:
    admin = CSS[CSS.index("Admin panel (PR 324)") : CSS.index("Offline page (PR 328)")]
    reduced = "".join(
        _block_at(admin, match.start())
        for match in re.finditer(r"@media \(prefers-reduced-motion: reduce\)", admin)
    )
    animated = set()
    for selector, body in re.findall(r"\n([.\[][^{}\n@]+?)\s*\{([^{}]*)\}", admin):
        if re.search(r"\b(animation|transition):\s*(?!none)", body):
            animated.update(part.strip() for part in selector.split(","))
    # A transition that only runs under no-preference needs no override.
    no_pref = "".join(
        _block_at(admin, match.start())
        for match in re.finditer(r"@media \(prefers-reduced-motion: no-preference\)", admin)
    )
    for selector in animated:
        assert selector in reduced or selector in no_pref, selector


def _block_at(text: str, start: int) -> str:
    return text[start : text.index("\n}\n", start)]
