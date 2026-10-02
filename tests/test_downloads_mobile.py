"""PR 281 (Webnovells Mobile, wave 34): the downloads screen on phones."""

import re
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.db.downloads import DownloadHistoryEntry
from app.db.users import User
from app.main import app
from app.services.titles import TitleSummary
from app.templating import plural

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


def _render_history(
    monkeypatch: pytest.MonkeyPatch,
    history: list[DownloadHistoryEntry],
    titles: dict[str, TitleSummary],
) -> str:
    from app.api import downloads_section

    user = User(1, "reader@example.com", "hash", "2026-10-01T08:00:00+00:00")

    class _ConnectionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def override_current_user(request: Request) -> User:
        request.state.current_user = user
        return user

    async def fake_history(conn: object, user_id: int) -> list[DownloadHistoryEntry]:
        return history

    async def fake_titles(slugs: set[str]) -> dict[str, TitleSummary]:
        assert slugs == {entry.slug_url for entry in history}
        return titles

    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(downloads_section, "connection", _ConnectionContext)
    monkeypatch.setattr(downloads_section, "list_download_history", fake_history)
    monkeypatch.setattr(downloads_section, "list_active_jobs_for_user", lambda user_id: [])
    monkeypatch.setattr(downloads_section, "ready_file_url", lambda job_id, user_id: None)
    monkeypatch.setattr(downloads_section, "title_summaries", fake_titles)
    try:
        response = TestClient(app).get("/downloads")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    return response.text


def _entry(entry_id: int, slug_url: str, chapters: int | None = 226) -> DownloadHistoryEntry:
    return DownloadHistoryEntry(
        entry_id, 1, slug_url, "epub", "done", chapters, None, "2026-10-01T19:17:00+00:00", None
    )


def test_history_card_leads_with_the_title_and_shows_the_file_name_under_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = "237596--became-a-medieval-fantasy-wizard"
    html = _render_history(
        monkeypatch,
        [_entry(1, slug)],
        {slug: TitleSummary("Я стал магом в средневековом фэнтези", "https://cover/1.jpg")},
    )

    assert re.search(
        r'class="downloads-history__link"[^>]*>Я стал магом в средневековом фэнтези</a>', html
    )
    assert f'<span class="wn-downloads-row__file">{slug}.epub</span>' in html
    assert '<img src="https://cover/1.jpg" alt="" loading="lazy">' in html
    # Search matches the name and the file name alike.
    assert f'data-history-title="я стал магом в средневековом фэнтези {slug}.epub"' in html


def test_history_card_without_a_title_leads_with_the_file_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = "272794--juin-gong-deurui-dongsim-sogeuro"
    html = _render_history(monkeypatch, [_entry(1, slug, chapters=None)], {})

    assert '<span class="wn-downloads-row__names wn-downloads-row__names--file">' in html
    assert re.search(rf'class="downloads-history__link"[^>]*>{re.escape(slug)}\.epub</a>', html)
    assert 'class="wn-downloads-row__file"' not in html
    assert '<span class="wn-downloads-row__cover" aria-hidden="true">EPUB</span>' in html
    assert "wn-downloads-row__chapters--none" in html


def test_history_card_counts_chapters_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _render_history(
        monkeypatch,
        [_entry(1, "1--a", chapters=226), _entry(2, "2--b", chapters=53), _entry(3, "3--c", 1)],
        {},
    )

    assert '<span class="wn-downloads-row__unit"> глав</span>' in html
    assert '<span class="wn-downloads-row__unit"> главы</span>' in html
    assert '<span class="wn-downloads-row__unit"> глава</span>' in html
    assert 'aria-label="53 главы"' in html


@pytest.mark.parametrize(
    ("n", "form"), [(1, "глава"), (2, "главы"), (5, "глав"), (11, "глав"), (21, "глава")]
)
def test_plural(n: int, form: str) -> None:
    assert plural(n, "глава", "главы", "глав") == form


def test_history_card_layout_on_mobile() -> None:
    assert "display: none;" in _rule(".wn-downloads-table__header", indent="  ")
    row = _rule(".wn-downloads-row", indent="  ")
    assert "flex-wrap: wrap;" in row
    assert "min-width: 0;" in row
    delete = _rule(".wn-downloads-row__actions .downloads-history__delete", indent="  ")
    assert "position: absolute;" in delete
    assert "width: 44px;" in delete
    assert "height: 44px;" in delete
    assert "overflow-wrap: anywhere;" in _rule(".wn-downloads-row__file", indent="  ")


def test_history_delete_is_undoable_instead_of_confirmed() -> None:
    # Webnovells Mobile -> Загрузки: the row goes at once with «Вернуть» in a toast; the
    # DELETE waits for the toast, and leaving the page still sends it.
    script = (ROOT / "app/static/js/download-history-delete.js").read_text(encoding="utf-8")

    assert "confirm(" not in script
    assert "UNDO_MS = 5000" in script
    assert 'label: "Вернуть"' in script
    assert "setTimeout(commit, UNDO_MS)" in script
    assert '"pagehide"' in script
    assert "keepalive" in script
    assert "fetch(`/downloads/history/${p.entryId}`" in script


def test_clear_history_confirms_in_the_shared_sheet_on_mobile() -> None:
    assert 'id="clear-history-confirm" data-bottom-sheet-title="Очистить историю?" hidden' in (
        TEMPLATE
    )
    assert "data-bottom-sheet-close data-autofocus>Отмена</button>" in TEMPLATE
    assert 'data-role="clear-download-history-confirm">Очистить</button>' in TEMPLATE
    script = (ROOT / "app/static/js/downloads-history-tools.js").read_text(encoding="utf-8")
    assert "window.bottomSheet.open(" in script
    # Desktop keeps the native confirmation.
    assert 'window.confirm("Очистить всю историю загрузок?")' in script


def test_history_heading_row_wraps_its_clear_button() -> None:
    # Found by the five-width check: at 320px «Очистить историю» ran the row 25px off
    # the screen.
    assert "flex-wrap: wrap;" in _rule(".wn-downloads__section-head", indent="  ")
