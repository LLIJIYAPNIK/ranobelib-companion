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


def test_navigation_and_account_rows_share_the_expanded_icon_grid() -> None:
    rows = _rule(".sidebar--expanded .sidebar__link,\n  .sidebar--expanded .sidebar__bell")
    account = _rule(".sidebar--expanded .sidebar__account-trigger")

    assert "grid-template-columns: 44px minmax(0, 1fr);" in rows
    assert "grid-template-columns: 44px minmax(0, 1fr) 20px;" in account


def test_saved_state_suppresses_first_paint_transitions() -> None:
    assert ".sidebar--initializing,\n.sidebar--initializing + .main" in CSS
    assert "transition: none !important;" in CSS


def test_mobile_navigation_contract_is_still_isolated_below_767px() -> None:
    mobile = CSS.split("@media (max-width: 767px)", 1)[1]

    assert ".sidebar__toggle" in mobile
    assert "display: none;" in mobile
    assert "env(safe-area-inset-bottom" in mobile
    assert "html.keyboard-open .sidebar__nav" in CSS
