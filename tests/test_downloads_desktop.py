"""PR 314 (Webnovells): the «Загрузки» desktop composition - hero, queue beside the
«Сводка»/«Как скачать» column, one full-width history table - and what keeps working
on top of it: filtering, deleting, «Скачать снова» and the ready file."""

import re
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.downloads_section import _summarize_history
from app.auth.dependencies import get_current_user
from app.db.downloads import DownloadHistoryEntry
from app.db.users import User
from app.main import app
from app.services.titles import TitleSummary

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
TOOLS = (ROOT / "app/static/js/downloads-history-tools.js").read_text(encoding="utf-8")
DELETE = (ROOT / "app/static/js/download-history-delete.js").read_text(encoding="utf-8")


def _rule(selector: str, *, indent: str = "") -> str:
    matches = re.findall(rf"\n{indent}{re.escape(selector)} \{{([^}}]*)\}}", CSS)
    assert matches, selector
    return matches[-1]


def _entry(
    entry_id: int,
    status: str = "done",
    *,
    slug_url: str = "6712--test-novel",
    finished_at: str = "2026-10-07T06:56:00+00:00",
    error: str | None = None,
) -> DownloadHistoryEntry:
    chapters = None if status == "error" else 12
    return DownloadHistoryEntry(
        entry_id, 1, slug_url, "epub", status, chapters, error, finished_at, None
    )


def _render(
    monkeypatch: pytest.MonkeyPatch,
    history: list[DownloadHistoryEntry],
    *,
    ready: dict[int, str] | None = None,
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
        return {"6712--test-novel": TitleSummary(name="Тестовая новелла", cover_url=None)}

    entry_by_job = {entry.job_id: entry.id for entry in history}
    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(downloads_section, "connection", _ConnectionContext)
    monkeypatch.setattr(downloads_section, "list_download_history", fake_history)
    monkeypatch.setattr(downloads_section, "list_active_jobs_for_user", lambda user_id: [])
    monkeypatch.setattr(
        downloads_section,
        "ready_file_url",
        lambda job_id, user_id: (ready or {}).get(entry_by_job.get(job_id)),
    )
    monkeypatch.setattr(downloads_section, "title_summaries", fake_titles)
    try:
        response = TestClient(app).get("/downloads")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    return response.text


def test_summary_counts_only_the_loaded_history() -> None:
    history = [_entry(4), _entry(3, "error"), _entry(2, "cancelled"), _entry(1)]

    summary = _summarize_history(history)

    assert (summary.total, summary.done, summary.errors, summary.cancelled) == (4, 2, 1, 1)
    assert summary.last is history[0]
    assert _summarize_history([]).last is None


def test_page_leads_with_one_primary_cta_then_queue_beside_the_side_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _render(monkeypatch, [_entry(2), _entry(1, "error", error="Глава не найдена")])

    assert html.count("wn-btn--primary") == 1
    hero = html.index("wn-downloads__hero")
    queue = html.index('data-role="active-downloads"')
    summary = html.index('data-role="downloads-summary"')
    history = html.index('data-role="downloads-history-section"')
    assert hero < queue < summary < history
    assert html.index('class="wn-downloads__overview"') < queue
    assert "Как скачать" in html


def test_summary_card_shows_counts_and_the_last_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _render(
        monkeypatch,
        [_entry(3), _entry(2, "error", error="Глава не найдена"), _entry(1, "cancelled")],
    )

    assert 'data-role="summary-done">1<' in html
    assert 'data-role="summary-error">1<' in html
    assert 'data-role="summary-cancelled">1<' in html
    assert 'data-role="summary-scope">3 последние загрузки<' in html
    assert 'data-role="summary-last-name" href="/titles/6712--test-novel">Тестовая новелла<' in (
        html
    )
    assert "EPUB · 07.10 · 06:56 · Готово" in html


def test_no_history_means_no_summary_and_an_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _render(monkeypatch, [])

    assert 'data-role="downloads-summary"' not in html
    assert 'data-role="download-history-empty"' in html
    assert "Пока ничего не скачивали" in html
    # The steps still show - they're how the history gets its first entry.
    assert "Как скачать" in html


def test_history_is_one_table_with_days_inside(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _render(
        monkeypatch,
        [_entry(2), _entry(1, finished_at="2026-10-06T23:56:00+00:00")],
    )

    assert html.count('class="wn-downloads-table__header"') == 1
    assert html.count('class="wn-downloads-history__group"') == 2
    assert html.index('data-role="download-history-table"') < html.index("07.10.2026")


def test_error_status_reads_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    error = "ranobelib сейчас ограничивает запросы, попробуйте позже"
    html = _render(monkeypatch, [_entry(1, "error", error=error)])

    assert f'title="{error}">{error}</span>' in html
    status = _rule(".wn-downloads .downloads-history__status")
    assert "-webkit-line-clamp: 2;" in status
    assert "white-space: nowrap;" not in status


def test_rows_offer_retry_delete_and_the_ready_file(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _entry(1)
    entry = DownloadHistoryEntry(**{**entry.__dict__, "job_id": "job-1"})
    file_url = "/titles/6712--test-novel/download/job-1/file"

    html = _render(monkeypatch, [entry], ready={1: file_url})

    assert f'<a class="downloads-history__download" href="{file_url}"' in html
    assert '<a href="/titles/6712--test-novel" aria-label="Скачать тайтл снова">' in html
    assert 'data-role="delete-history-entry" data-entry-id="1"' in html
    assert 'data-history-outcome="done"' in html


def test_filtered_out_rows_are_really_hidden() -> None:
    # The row's display: grid used to override [hidden], so search/filter hid nothing.
    assert "display: none;" in _rule(".wn-downloads-row[hidden]")
    assert "display: none;" in _rule(".wn-downloads-history__group[hidden]")
    assert "row.hidden = !(matchesQuery && matchesStatus);" in TOOLS


def test_summary_follows_deletes_and_clear() -> None:
    assert 'new CustomEvent("downloads:historychange")' in DELETE
    assert 'document.addEventListener("downloads:historychange", refreshSummary);' in TOOLS
    assert "summary.hidden = true;" in TOOLS
    assert '"download-history-table"' in TOOLS


def test_layout_breakpoints() -> None:
    assert "minmax(300px, 360px)" in _rule(".wn-downloads__overview")
    # Below 1100px the side column goes under the queue.
    assert "grid-template-columns: minmax(0, 1fr);" in _rule(".wn-downloads__overview", indent="  ")
    # Tablets drop Формат/Время rather than scroll the actions off screen.
    assert "min-width: 880px;" not in CSS
    tablet = re.search(
        r"@media \(min-width: 768px\) and \(max-width: 960px\) \{(.*?)\n\}", CSS, re.S
    )
    assert tablet
    hidden = ".wn-downloads .downloads-history__fmt,\n  .wn-downloads .downloads-history__date {"
    assert hidden + "\n    display: none;" in tablet.group(1)
