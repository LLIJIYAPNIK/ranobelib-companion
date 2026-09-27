"""app/email.py's no-SMTP fallback (PR 225, body logging from PR 246)."""

import logging
from collections.abc import Iterator

import pytest

from app.config import get_settings
from app.email import send_email


@pytest.fixture(autouse=True)
def _no_smtp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("EMAIL_HOST", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_without_smtp_outside_production_the_body_is_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    with caplog.at_level(logging.WARNING, logger="app.email"):
        await send_email("alice@example.com", "Subject", "code 123456")

    assert "code 123456" in caplog.text


async def test_without_smtp_in_production_the_body_is_never_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")

    with caplog.at_level(logging.WARNING, logger="app.email"):
        await send_email("alice@example.com", "Subject", "code 123456")

    assert "Subject" in caplog.text
    assert "123456" not in caplog.text
