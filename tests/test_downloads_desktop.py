"""«Загрузки» in the home page's frame: the title row with the summary, «Скачать тайтл»
(search, the library's latest reads, the format, «Скачать»), «Сейчас» with the title's
name and a translation choice in place, and the history by the user's own days in a
column with its own scroll - plus what keeps working on top of it: filtering, deleting,
«Скачать снова» and the ready file."""

import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.downloads_section import _group_history, _summarize_history, _translation_variants
from app.auth.dependencies import get_current_user
from app.db.downloads import DownloadHistoryEntry
from app.db.users import User
from app.jobs.models import DownloadJob
from app.main import app
from app.services.titles import TitleSummary
from app.timezones import local_today

ROOT = Path(__file__).parents[1]
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "app/templates/downloads.html").read_text(encoding="utf-8")
TOOLS = (ROOT / "app/static/js/downloads-history-tools.js").read_text(encoding="utf-8")
DELETE = (ROOT / "app/static/js/download-history-delete.js").read_text(encoding="utf-8")
START = (ROOT / "app/static/js/downloads-start.js").read_text(encoding="utf-8")
STATUS = (ROOT / "app/static/js/downloads-status.js").read_text(encoding="utf-8")


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
    jobs: list[DownloadJob] | None = None,
    library: list[str] | None = None,
    formats: list[str] | None = None,
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

    async def fake_library(conn: object, user_id: int) -> list[SimpleNamespace]:
        return [SimpleNamespace(slug_url=slug) for slug in library or []]

    async def fake_titles(slugs: set[str]) -> dict[str, TitleSummary]:
        known = {"6712--test-novel": TitleSummary(name="Тестовая новелла", cover_url=None)}
        for slug in library or []:
            known[slug] = TitleSummary(name=f"Тайтл {slug}", cover_url=f"https://cover/{slug}.jpg")
        return {slug: title for slug, title in known.items() if slug in slugs}

    entry_by_job = {entry.job_id: entry.id for entry in history}
    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(downloads_section, "connection", _ConnectionContext)
    monkeypatch.setattr(downloads_section, "list_download_history", fake_history)
    monkeypatch.setattr(downloads_section, "list_entries", fake_library)
    monkeypatch.setattr(downloads_section, "list_active_jobs_for_user", lambda user_id: jobs or [])
    monkeypatch.setattr(
        downloads_section,
        "ready_file_url",
        lambda job_id, user_id: (ready or {}).get(entry_by_job.get(job_id)),
    )
    monkeypatch.setattr(downloads_section, "title_summaries", fake_titles)
    if formats is not None:
        monkeypatch.setattr(downloads_section, "available_export_formats", lambda: formats)
    try:
        response = TestClient(app).get("/downloads")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    return response.text


# --- the summary --------------------------------------------------------------------


def test_summary_counts_the_loaded_history_and_its_chapters() -> None:
    history = [_entry(4), _entry(3, "error"), _entry(2, "cancelled"), _entry(1)]

    summary = _summarize_history(history)

    assert (summary.total, summary.done, summary.errors, summary.cancelled) == (4, 2, 1, 1)
    # Chapters only of the files that came out.
    assert summary.chapters == 24
    assert summary.last is history[0]
    assert _summarize_history([]).last is None


def test_title_row_says_what_came_out(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _render(monkeypatch, [_entry(2), _entry(1)])

    assert '<h1 class="hm-title downloads-page__title">Загрузки</h1>' in html
    assert 'data-role="summary-done">2</b> файла готовы' in html
    assert "24</b> главы" in html
    # No errors - no «0 с ошибкой».
    assert re.search(r'data-role="summary-error-stat" hidden', html)


def test_no_history_means_no_summary_and_an_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _render(monkeypatch, [])

    assert 'data-role="downloads-summary"' not in html
    assert 'data-role="download-history-empty"' in html
    assert "Пока ничего не скачивали" in html


# --- the layout ---------------------------------------------------------------------


def test_the_page_reads_title_start_now_then_the_history(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _render(monkeypatch, [_entry(1)])

    order = [
        html.index("downloads-page__title"),
        html.index('data-role="download-start"'),
        html.index('data-role="active-downloads"'),
        html.index('data-role="downloads-history-section"'),
    ]
    assert order == sorted(order)
    # The one light button is «Скачать» in the start form.
    assert html.count('class="dl-go"') == 1


def test_layout_two_columns_then_one() -> None:
    assert "grid-template-columns: minmax(0, 1fr) minmax(360px, 440px);" in _rule(".hm.dl")
    history = _rule(".dl-history")
    assert "position: sticky;" in history
    assert "max-height: calc(100dvh - 48px);" in history
    assert "overflow-y: auto;" in _rule(".dl-history__scroll")
    # Below 1100px one column, and the history scrolls with the page.
    narrow = CSS[CSS.index("@media (max-width: 1099px) {\n  .hm.dl {") :]
    narrow = narrow[: narrow.index("\n}\n")]
    assert "grid-template-columns: minmax(0, 1fr);" in narrow
    assert "position: static;" in narrow


# --- «Скачать тайтл» -----------------------------------------------------------------


def test_the_library_offers_its_latest_reads_as_picks(monkeypatch: pytest.MonkeyPatch) -> None:
    library = [f"{n}--title" for n in range(1, 9)]
    html = _render(monkeypatch, [], library=library)

    assert html.count('data-role="download-pick" data-slug="') == 6
    assert (
        'data-slug="1--title" data-name="Тайтл 1--title" data-cover="https://cover/1--title.jpg"'
        in html
    )
    assert 'data-slug="7--title"' not in html
    # Without JS a pick is the title's own page.
    assert 'href="/titles/1--title" data-role="download-pick"' in html


def test_the_formats_are_the_servers_own(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _render(monkeypatch, [], formats=["epub", "fb2"])

    assert re.findall(r'name="fmt" value="(\w+)"', html) == ["epub", "fb2"]
    assert 'value="epub" checked' in html


def test_start_script_searches_the_catalog_and_posts_the_download() -> None:
    assert "/catalog/suggest?query=" in START
    assert "form.action = `/titles/${encodeURIComponent(slug)}/download`;" in START
    assert 'fetch(form.action, { method: "POST", body: new FormData(form) })' in START
    assert "location.reload();" in START
    # Without JS the field is the catalog's own search.
    assert '<form class="dl-search" action="/catalog" method="get" role="search"' in TEMPLATE
    assert 'role="combobox" aria-expanded="false" aria-controls="dl-suggest"' in TEMPLATE


# --- «Сейчас» --------------------------------------------------------------------------


def _job(status: str = "running", **kwargs: object) -> DownloadJob:
    job = DownloadJob("job-1", "6712--test-novel", "epub", user_id=1)
    job.status = status
    for key, value in kwargs.items():
        setattr(job, key, value)
    return job


def test_a_running_job_shows_the_title_and_its_chapters(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _render(monkeypatch, [], jobs=[_job(completed=51, total=836)])

    assert 'data-job-id="job-1" data-status="running"' in html
    assert ">Тестовая новелла</a>" in html
    assert 'data-role="bar-pct">6%' in html
    assert "Скачивание · 51 из 836 глав" in html


def _chapter(*teams: str) -> SimpleNamespace:
    return SimpleNamespace(
        branches=[SimpleNamespace(teams=[SimpleNamespace(name=t)], user=None) for t in teams]
    )


def test_translation_variants_name_the_team_behind_each_number() -> None:
    job = _job(
        "needs_translation",
        ambiguous_chapters=[
            _chapter("Jaomix", "OneSecond"),
            _chapter("Jaomix", "OneSecond"),
            _chapter("OneSecond", "Jaomix", "Третьи"),
        ],
    )

    variants = _translation_variants(job)

    assert [(v.index, v.who, v.chapters) for v in variants] == [
        (0, "Jaomix", 3),
        (1, "OneSecond", 3),
        (2, "Третьи", 1),
    ]


def test_a_job_waiting_for_a_translation_is_answered_in_its_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job("needs_translation", ambiguous_chapters=[_chapter("Jaomix", "OneSecond")])

    html = _render(monkeypatch, [], jobs=[job])

    assert 'action="/titles/6712--test-novel/download/job-1/retry"' in html
    assert 'name="translation_index" value="0" checked' in html
    assert "<b>Вариант 2</b> — OneSecond" in html
    assert "У 1 главы несколько переводов" in html
    assert "display: none;" in _rule('.dl-job[data-status="needs_translation"] .dl-job__pct')
    # A job that comes to need one while the page is open brings the form in.
    assert 'job.status === "needs_translation"' in STATUS
    assert "window.location.reload();" in STATUS


# --- the history -----------------------------------------------------------------------


def test_days_are_the_users_own_and_named() -> None:
    today = local_today("Europe/Moscow")
    yesterday = today - timedelta(days=1)
    groups = _group_history(
        [
            _entry(3, finished_at=f"{today.isoformat()}T09:00:00+00:00"),
            # 22:30 UTC the day before is already today in Moscow.
            _entry(2, finished_at=f"{yesterday.isoformat()}T22:30:00+00:00"),
            _entry(1, finished_at="2025-03-08T10:00:00+00:00"),
        ],
        "Europe/Moscow",
    )

    assert [group.label for group in groups] == ["Сегодня", "8 марта 2025"]
    assert [[entry.id for entry in group.entries] for group in groups] == [[3, 2], [1]]


def test_error_status_reads_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    error = "ranobelib сейчас ограничивает запросы, попробуйте позже"
    html = _render(monkeypatch, [_entry(1, "error", error=error)])

    assert f'<span class="dl-row__status" title="{error}">{error}</span>' in html
    assert "dl-row--error" in html


def test_rows_offer_the_ready_file_retry_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    ready_entry = DownloadHistoryEntry(**{**_entry(2).__dict__, "job_id": "job-2"})
    file_url = "/titles/6712--test-novel/download/job-2/file"

    html = _render(monkeypatch, [ready_entry, _entry(1)], ready={2: file_url})

    assert f'<a class="dl-row__file" href="{file_url}"' in html
    # «Скачать снова» chooses the title in the start form, in the same format.
    assert re.search(
        r'href="/titles/6712--test-novel" data-role="download-pick" data-slug="6712--test-novel"'
        r' data-name="Тестовая новелла" data-cover="" data-fmt="epub" aria-label="Скачать снова"',
        html,
    )
    assert 'data-role="delete-history-entry" data-entry-id="1"' in html
    assert 'data-history-outcome="done"' in html


def test_filtered_out_rows_are_really_hidden() -> None:
    # The row's display: grid would override [hidden], so search/filter would hide nothing.
    for selector in (".dl-row[hidden]", ".dl-day[hidden]"):
        assert re.search(rf"{re.escape(selector)},?[^{{]*\{{\s*display: none;", CSS), selector
    assert "row.hidden = !(matchesQuery && matchesStatus);" in TOOLS


def test_history_scripts_read_both_the_new_rows_and_the_old_list() -> None:
    for script in (TOOLS, DELETE):
        assert 'const ROW = ".dl-row, .downloads-history__item";' in script
        assert 'const DAY = ".dl-day";' in script
    assert 'new CustomEvent("downloads:historychange")' in DELETE
    assert 'document.addEventListener("downloads:historychange", refreshSummary);' in TOOLS
    assert '"download-history-table"' in TOOLS
