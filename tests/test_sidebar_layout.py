"""PR 310 desktop rail geometry and visual-state contracts."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
BASE = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")


def _rule(selector: str, *, last: bool = True) -> str:
    matches = re.findall(rf"\n(?:  )?{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1] if last else matches[0]


def test_toggle_keeps_one_fixed_coordinate_in_both_desktop_states() -> None:
    compact = _rule(".sidebar__toggle", last=False)
    expanded = _rule(".sidebar--expanded .sidebar__toggle")

    for declaration in ("left: 20px;", "bottom: 14px;", "height: 40px;"):
        assert declaration in compact
        assert declaration in expanded
    assert 'grid-template-areas: "brand" "sep" "nav" "." "account";' in CSS
    assert "brand toggle" not in CSS


def test_expanded_toggle_adds_a_label_without_moving_its_icon_column() -> None:
    expanded = _rule(".sidebar--expanded .sidebar__toggle")

    assert "grid-template-columns: 44px minmax(0, 1fr);" in expanded
    assert '<span class="sidebar__toggle-label">Свернуть меню</span>' in BASE
    assert "display: block;" in _rule(".sidebar--expanded .sidebar__toggle-label")


def _desktop_rule(selector: str) -> str:
    """The rule inside the PR 320 desktop rail block (min-width: 768px)."""
    desktop = CSS[CSS.index("/* Desktop rail (PR 320)") :]
    desktop = desktop[: desktop.index("@media (min-width: 768px) and (hover: hover)")]
    matches = re.findall(rf"\n  {re.escape(selector)} \{{([^}}]*)\}}", desktop)
    assert len(matches) == 1, selector
    return matches[0]


def test_navigation_and_account_rows_share_one_icon_grid_in_both_states() -> None:
    # PR 320: the grid is not scoped to .sidebar--expanded - collapsed and expanded rows
    # have the same 44px icon column and height, so expanding moves no control.
    rows = _desktop_rule(".sidebar__link,\n  .sidebar__bell,\n  .sidebar__guest")
    account = _desktop_rule(".sidebar__account-trigger")

    assert "grid-template-columns: 44px minmax(0, 1fr);" in rows
    assert "justify-items: start;" in rows
    assert "height: 46px;" in rows
    assert "grid-template-columns: 44px minmax(0, 1fr) 20px;" in account
    assert "height: 56px;" in account
    for selector in (
        ".sidebar",
        ".sidebar__brand",
        ".sidebar__sep",
        ".sidebar__nav",
        ".sidebar__account",
    ):
        assert ".sidebar--expanded" not in selector
        _desktop_rule(selector)


def test_expanded_state_only_widens_the_rail_and_reveals_labels() -> None:
    assert _desktop_rule(".sidebar--expanded").strip() == "width: 248px;"
    for selector in (
        ".sidebar--expanded .sidebar__link,\n  .sidebar--expanded .sidebar__bell,\n"
        "  .sidebar--expanded .sidebar__guest",
        ".sidebar--expanded .sidebar__account-trigger",
    ):
        body = _desktop_rule(selector)
        assert "height" not in body and "grid-template" not in body, selector


def test_collapsed_labels_are_visually_hidden_but_stay_accessible_names() -> None:
    hidden = _desktop_rule(
        ".sidebar:not(.sidebar--expanded) .sidebar__label,\n"
        "  .sidebar:not(.sidebar--expanded) .sidebar__guest-label"
    )
    assert "clip-path: inset(50%);" in hidden
    assert "position: absolute;" in hidden
    assert "display: none" not in hidden  # would drop them from the accessibility tree
    for label in ("Главная", "Библиотека", "Загрузки"):
        assert f'<span class="sidebar__label">{label}</span>' in BASE


def test_saved_state_suppresses_first_paint_transitions() -> None:
    assert ".sidebar--initializing,\n.sidebar--initializing + .main" in CSS
    assert "transition: none !important;" in CSS


def test_mobile_navigation_contract_is_still_isolated_below_767px() -> None:
    mobile = CSS.split("@media (max-width: 767px)", 1)[1]

    assert ".sidebar__toggle" in mobile
    assert "display: none;" in mobile
    assert "env(safe-area-inset-bottom" in mobile
    assert "html.keyboard-open .sidebar__nav" in CSS
