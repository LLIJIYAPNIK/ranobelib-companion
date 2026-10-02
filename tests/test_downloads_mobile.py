"""PR 281 (Webnovells Mobile, wave 34): the downloads screen on phones."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "app/templates/downloads.html").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    """The selector's last rule at this indent - for an indented one, the <= 767px block."""
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def test_steps_carry_a_mobile_only_hint_each() -> None:
    for label, hint in (
        ("Ссылка", "вставьте адрес тайтла"),
        ("Главы", "выберите тома или диапазон"),
        ("EPUB", "файл появится в истории ниже"),
    ):
        assert f'{label}<small class="wn-downloads-steps__hint"> — {hint}</small>' in TEMPLATE
    assert "display: none;" in _rule(".wn-downloads-steps__hint")
    assert "display: inline;" in _rule(".wn-downloads-steps__hint", indent="  ")


def test_steps_are_a_vertical_list_on_mobile() -> None:
    assert "flex-direction: column;" in _rule(".wn-downloads-steps", indent="  ")
    assert "display: none;" in _rule(".wn-downloads-steps li:nth-child(even)", indent="  ")
    assert "flex: none;" in _rule(".wn-downloads-steps b", indent="  ")
    # The decorative connectors between steps aren't announced as empty list items.
    assert TEMPLATE.count('<li aria-hidden="true"><i></i></li>') == 2
