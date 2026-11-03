"""PR 285 (Webnovells Mobile, wave 34), redrawn with «Друзья» in the home page's frame:
the friends screen on tablets and phones."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
PAGE = (ROOT / "app/templates/friends.html").read_text(encoding="utf-8")


def _media(query: str) -> str:
    """Every block of this media query that holds a «Друзья» (.fr-*) rule, joined."""
    blocks = []
    for match in re.finditer(rf"@media \({re.escape(query)}\) \{{\n(.*?)\n\}}\n", CSS, re.S):
        if ".fr-" in match.group(1):
            blocks.append(match.group(1))
    assert blocks, query
    return "\n".join(blocks)


def _rule(selector: str, css: str = CSS, *, indent: str = "") -> str:
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", "\n" + css)
    assert matches, selector
    return matches[-1]


PHONE = _media("max-width: 767px")
TABLET = _media("max-width: 1100px")


def test_search_is_the_glass_field_with_a_search_key() -> None:
    assert '<form class="hm-search fr-search" method="get" action="/friends" role="search">' in PAGE
    assert 'enterkeyhint="search"' in PAGE
    assert 'name="query"' in PAGE
    assert "width: 100%;" in _rule(".fr-search", TABLET, indent="  ")


def test_requests_come_before_the_list_unless_there_are_none() -> None:
    assert "flex-direction: column;" in _rule(".fr-layout", TABLET, indent="  ")
    order = {
        name: int(re.search(r"order: (\d+);", _rule(name, TABLET, indent="  ")).group(1))
        for name in (".fr-results", ".fr-queue", ".fr-main", ".fr-queue--empty", ".fr-side")
    }
    assert order[".fr-results"] < order[".fr-queue"] < order[".fr-main"]
    assert order[".fr-main"] < order[".fr-queue--empty"] < order[".fr-side"]
    assert "fr-queue--empty{% endif %}" in PAGE


def test_friends_tabs_are_a_ribbon() -> None:
    assert '<nav class="fr-tabs" aria-label="Друзья и заявки" data-ribbon>' in PAGE
    assert "js/ribbon.js" in PAGE
    # offsetLeft of the active tab is measured against it (ribbon.js).
    assert "position: relative;" in _rule(".fr-tabs")
    assert "overflow-x: auto;" in _rule(".fr-tabs")
    assert "align-self: stretch;" in _rule(".fr-tabs", PHONE, indent="  ")


def test_a_friend_is_two_lines_with_wrapping_names() -> None:
    row = _rule(".fr-row,\n  .fr-row--friend", PHONE, indent="  ")
    assert "grid-template-columns: minmax(0, 1fr) 44px;" in row
    assert "grid-column: 1 / -1;" in _rule(".fr-row--friend .fr-book", PHONE, indent="  ")
    names = _rule(".fr-person__name,\n  .fr-book__name", PHONE, indent="  ")
    assert "white-space: normal;" in names
    assert "overflow-wrap: anywhere;" in names


def test_request_and_result_actions_are_wrapping_44px_pills() -> None:
    actions = _rule(
        ".fr-row--request .fr-row__actions,\n  .fr-row--result .fr-row__actions,\n"
        "  .fr-list--compact .fr-row__actions",
        PHONE,
        indent="  ",
    )
    assert "flex-wrap: wrap;" in actions
    assert "flex: 1 1 120px;" in _rule(".fr-row__actions form", PHONE, indent="  ")
    assert "height: 44px;" in _rule(".fr-btn,\n  .fr-result__actions .ui-btn", PHONE, indent="  ")
    assert "flex-wrap: wrap;" in _rule(".fr-invite__nick", PHONE, indent="  ")
    # The 26px switch keeps a 44px hit area.
    assert "inset: -9px 0;" in _rule(".fr .wn-switch::before", PHONE, indent="  ")


def test_friend_actions_open_in_the_shared_sheet_on_mobile() -> None:
    assert 'data-friend-sheet="friend-actions-{{ friend.user.id }}"' in PAGE
    assert 'aria-haspopup="dialog"' in PAGE
    assert 'id="friend-actions-{{ friend.user.id }}" data-role="friend-actions-sheet"' in PAGE
    assert 'data-bottom-sheet-title="{{ friend.user.display_name }}" hidden' in PAGE
    assert ">Открыть профиль</a>" in PAGE
    assert "display: none;" in _rule(".fr-row .fr-row__remove", PHONE, indent="  ")
    assert "display: grid;" in _rule(".fr-row__more", PHONE, indent="  ")
    assert "display: none;" in _rule(".fr-row__more")


def test_remove_shows_with_its_row_on_a_pointer() -> None:
    hover = re.search(
        r"@media \(hover: hover\) \{\n  \.fr-row__remove \{([^}]*)\}\n\n"
        r"  \.fr-row:hover \.fr-row__remove,\n  \.fr-row:focus-within \.fr-row__remove \{([^}]*)\}",
        CSS,
    )
    assert hover
    assert "opacity: 0;" in hover.group(1)
    assert "opacity: 1;" in hover.group(2)
    assert 'aria-label="Удалить {{ friend.user.display_name }} из друзей"' in PAGE


def test_no_violet_primary_on_the_page() -> None:
    assert "wn-btn--primary" not in PAGE
    assert "ui-btn--primary" not in PAGE
    assert "background: #eeecf4;" in _rule(
        ".fr-btn--primary,\n.fr-result__actions .ui-btn:not(.ui-btn--quiet)"
    )
