"""PR 326: a whole admin session never writes the admin password or a secret column's
value to the logs (every logger, at DEBUG)."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.test_admin_auth import PASSWORD, Clock, _client, _login
from tests.test_admin_data import SECRET_HASH, _seed


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as test_client:
        yield test_client
    get_settings.cache_clear()


async def test_an_admin_session_logs_no_password_and_no_secret_values(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed(client)
    caplog.set_level(logging.DEBUG)

    _login(client, "a-wrong-guess-77")
    _login(client)
    for path in (
        "/admin",
        "/admin/tables",
        "/admin/tables/users",
        "/admin/tables/password_reset_tokens",
        "/admin/tables/users?q=alice",
        "/admin/tables/users?sort=password_hash",
    ):
        assert client.get(path).status_code == 200, path

    logged = caplog.text
    assert "/admin/tables/users" in logged  # the capture really saw the session
    assert PASSWORD not in logged
    assert "a-wrong-guess-77" not in logged
    assert SECRET_HASH not in logged  # users.password_hash and the reset token_hash
