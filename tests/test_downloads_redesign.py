from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.downloads_section import _group_history
from app.auth.dependencies import get_current_user
from app.db.downloads import DownloadHistoryEntry
from app.db.users import User
from app.main import app

ROOT = Path(__file__).parents[1]


def _entry(entry_id: int, finished_at: str, status: str = "done") -> DownloadHistoryEntry:
    return DownloadHistoryEntry(
        entry_id,
        1,
        f"{entry_id}--test-title",
        "epub",
        status,
        12,
        None,
        finished_at,
        None,
    )


def test_download_history_is_grouped_by_iso_date_in_original_order() -> None:
    groups = _group_history(
        [
            _entry(1, "2026-10-01T18:20:00+00:00"),
            _entry(2, "2026-10-01T12:10:00+00:00"),
            _entry(3, "2026-09-30T22:00:00+00:00", "error"),
        ]
    )

    assert [(group.date, group.label) for group in groups] == [
        ("2026-10-01", "01.10.2026"),
        ("2026-09-30", "30.09.2026"),
    ]
    assert [[entry.id for entry in group.entries] for group in groups] == [[1, 2], [3]]


def test_downloads_template_exposes_search_filter_and_clear_contracts() -> None:
    template = (ROOT / "app" / "templates" / "downloads.html").read_text(encoding="utf-8")

    for role in (
        'data-role="download-history-search"',
        'data-role="download-history-filter"',
        'data-role="clear-download-history"',
        'data-role="download-history-groups"',
        'data-role="downloads-empty-active"',
    ):
        assert role in template

    assert "Ссылка" in template
    assert "Главы" in template
    assert "EPUB" in template


def test_downloads_history_script_uses_existing_delete_endpoint_and_bulk_clear() -> None:
    script = (ROOT / "app" / "static" / "js" / "downloads-history-tools.js").read_text(
        encoding="utf-8"
    )

    assert 'fetch("/downloads/history", { method: "DELETE" })' in script
    assert 'data-role="download-history-search"' in script
    assert 'data-role="download-history-filter"' in script


def test_downloads_redesign_renders_history_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import downloads_section

    user = User(1, "reader@example.com", "hash", "2026-10-01T08:00:00+00:00")
    history = [_entry(1, "2026-10-01T18:20:00+00:00")]

    class _ConnectionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    async def override_current_user(request: Request) -> User:
        request.state.current_user = user
        return user

    async def fake_history(conn: object, user_id: int) -> list[DownloadHistoryEntry]:
        assert user_id == user.id
        return history

    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(downloads_section, "connection", _ConnectionContext)
    monkeypatch.setattr(downloads_section, "list_download_history", fake_history)
    monkeypatch.setattr(downloads_section, "list_active_jobs_for_user", lambda user_id: [])
    monkeypatch.setattr(downloads_section, "ready_file_url", lambda job_id, user_id: None)
    try:
        response = TestClient(app).get("/downloads")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert 'data-role="download-history-search"' in response.text
    assert 'data-history-date="2026-10-01"' in response.text
    assert "12 глав" in response.text
    assert "Сейчас ничего не скачивается" in response.text
