"""PR 279 (Webnovells Mobile, wave 34): the shared mobile shell - header, bottom
navigation and the one bottom sheet every screen reuses."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

client = TestClient(app)


@pytest.fixture
def logged_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    with TestClient(app) as test_client:
        register(test_client, "alice.wong@example.com")
        yield test_client

    get_settings.cache_clear()


def _header_title(html: str) -> str:
    match = re.search(r'<span class="sidebar__strip-title">(.*?)</span>', html, re.S)
    assert match, "the mobile header has no title slot"
    return match.group(1).strip()


def test_mobile_header_title_is_empty_on_home() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert _header_title(response.text) == ""


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("/library", "Библиотека"),
        ("/downloads", "Загрузки"),
        ("/activity", "Активность"),
        ("/friends", "Друзья"),
        ("/settings/reading", "Настройки"),
        ("/profile", "Профиль"),
    ],
)
def test_mobile_header_names_the_current_section(
    logged_in_client: TestClient, path: str, title: str
) -> None:
    response = logged_in_client.get(path)

    assert response.status_code == 200
    assert _header_title(response.text) == title


def test_mobile_header_marks_the_avatar_on_the_users_own_profile_only(
    logged_in_client: TestClient,
) -> None:
    own = logged_in_client.get("/profile")
    home = logged_in_client.get("/")

    assert "sidebar__account-trigger--current" in own.text
    assert "sidebar__account-trigger--current" not in home.text
