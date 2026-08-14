"""GET/POST /settings/account (PR 90) - editing the account's nickname/email/bio."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
from app.config import get_settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    get_settings.cache_clear()
    db_connection._connection = None

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def _register(client: TestClient, email: str, password: str = "hunter2pass") -> None:
    client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password},
    )


def test_anonymous_visitor_sees_a_locked_screen_instead_of_the_form(
    client: TestClient,
) -> None:
    response = client.get("/settings/account")

    assert response.status_code == 200
    assert 'class="locked-feature"' in response.text
    assert 'name="email"' not in response.text


def test_anonymous_post_is_redirected_to_login(client: TestClient) -> None:
    response = client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "", "bio": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logged_in_visitor_sees_the_form_prefilled_with_their_email(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")

    response = client.get("/settings/account")

    assert response.status_code == 200
    assert 'name="email"' in response.text
    assert 'value="alice@example.com"' in response.text


def test_update_account_saves_nickname_and_bio(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "Alice Wong", "bio": "Hi there"},
    )

    assert response.status_code == 200
    assert 'value="Alice Wong"' in response.text
    assert ">Hi there</textarea>" in response.text
    assert "Изменения сохранены" in response.text


def test_update_account_can_change_email(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account",
        data={"email": "alice2@example.com", "nickname": "", "bio": ""},
    )

    assert response.status_code == 200
    assert 'value="alice2@example.com"' in response.text


def test_update_account_rejects_an_email_already_used_by_another_account(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    client.post("/logout")
    _register(client, "bob@example.com")

    response = client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "", "bio": ""},
    )

    assert response.status_code == 400
    assert "уже используется другим аккаунтом" in response.text
    assert "Изменения сохранены" not in response.text
    # Bob's own account is untouched.
    reloaded = client.get("/settings/account")
    assert 'value="bob@example.com"' in reloaded.text


def test_update_account_allows_resubmitting_the_same_email_unchanged(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account",
        data={"email": "alice@example.com", "nickname": "Alice", "bio": ""},
    )

    assert response.status_code == 200
    assert 'value="Alice"' in response.text


def test_saved_nickname_switches_the_sidebar_avatar_initials(client: TestClient) -> None:
    _register(client, "alice.wong@example.com")
    client.post(
        "/settings/account",
        data={"email": "alice.wong@example.com", "nickname": "Bob Carter", "bio": ""},
    )

    home = client.get("/")

    assert ">BC</button>" in home.text
    assert ">AW</button>" not in home.text


def test_avatar_upload_is_a_clickable_preview_that_auto_submits(client: TestClient) -> None:
    # PR 109: no separate file field/"Загрузить" button - the round avatar preview is
    # itself the <label> wrapping the (visually hidden) file input, and avatar-upload.js
    # submits the form as soon as a file is chosen.
    _register(client, "alice@example.com")

    response = client.get("/settings/account")

    assert response.status_code == 200
    assert 'data-role="avatar-upload-form"' in response.text
    assert 'class="avatar-upload"' in response.text
    assert 'data-role="avatar-upload-preview"' in response.text
    assert 'class="avatar-upload__initials"' in response.text
    assert 'data-role="avatar-upload-input"' in response.text
    assert "static/js/avatar-upload.js" in response.text
    assert ">Загрузить<" not in response.text


def test_anonymous_avatar_upload_is_redirected_to_login(client: TestClient) -> None:
    response = client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", b"fake-png-bytes", "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_upload_avatar_saves_the_file_and_confirms(client: TestClient, tmp_path: Path) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", _PNG_BYTES, "image/png")},
    )

    assert response.status_code == 200
    assert "Аватар обновлён" in response.text
    saved_files = list((tmp_path / "avatars").iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].name.endswith(".png")
    assert saved_files[0].read_bytes() == _PNG_BYTES


def test_upload_avatar_rejects_a_disallowed_content_type(
    client: TestClient, tmp_path: Path
) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.gif", b"fake-gif-bytes", "image/gif")},
    )

    assert response.status_code == 400
    assert "Поддерживаются только изображения" in response.text
    assert not (tmp_path / "avatars").exists()


def test_upload_avatar_rejects_a_file_too_large(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.auth.avatar._MAX_AVATAR_SIZE_BYTES", 1024)
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", _PNG_BYTES + b"\x00" * 2000, "image/png")},
    )

    assert response.status_code == 400
    assert "слишком большой" in response.text
    assert not (tmp_path / "avatars").exists()


def test_upload_avatar_rejects_content_that_doesnt_match_its_claimed_type(
    client: TestClient, tmp_path: Path
) -> None:
    _register(client, "alice@example.com")

    response = client.post(
        "/settings/account/avatar",
        files={"avatar": ("me.png", b"this is not actually a png", "image/png")},
    )

    assert response.status_code == 400
    assert "не является изображением" in response.text
    assert not (tmp_path / "avatars").exists()


_JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32


def test_reuploading_with_a_different_extension_removes_the_old_file(
    client: TestClient, tmp_path: Path
) -> None:
    _register(client, "alice@example.com")
    client.post(
        "/settings/account/avatar", files={"avatar": ("me.png", _PNG_BYTES, "image/png")}
    )

    client.post(
        "/settings/account/avatar", files={"avatar": ("me.jpg", _JPEG_BYTES, "image/jpeg")}
    )

    saved_files = list((tmp_path / "avatars").iterdir())
    assert [f.name for f in saved_files] == ["1.jpg"]
