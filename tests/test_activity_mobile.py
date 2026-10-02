"""PR 283 (Webnovells Mobile, wave 34): the activity screen on phones."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, the <= 767px block."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_metric_notes_and_event_lines_wrap_instead_of_ellipsizing() -> None:
    for selector in (
        ".wn-activity-metric__note",
        ".wn-activity-event__title",
        ".wn-activity-event__sub",
    ):
        rule = _rule(selector, indent="  ")
        assert "white-space: normal;" in rule, selector
        assert "overflow-wrap: break-word;" in rule, selector


def test_period_switch_and_continue_are_44px_targets() -> None:
    assert "min-height: 44px;" in _rule(".wn-segmented__item", indent="  ")
    assert "min-height: 44px;" in _rule(".wn-activity-today .wn-activity-link", indent="  ")
