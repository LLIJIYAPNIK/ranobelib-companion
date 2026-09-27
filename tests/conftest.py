"""Shared, autouse test fixtures.

app.auth.rate_limit._attempts (PR 188) is a process-wide dict, same as
app.jobs.store._jobs - every test module in this suite shares one pytest process, and
several of them log in/register the same "alice@example.com" from the TestClient's fixed
"testclient" host. Without a reset between tests, attempts recorded by one test's
POST /login or /register would count against the next test's, eventually tripping the
rate limit in a completely unrelated test.
"""

import asyncio
import sys
from collections.abc import Iterator

import pytest

from app.auth.rate_limit import _attempts
from tests.auth_helpers import record_email, sent_emails

if sys.platform == "win32":
    # Same requirement as app/main.py's own WindowsSelectorEventLoopPolicy (psycopg's
    # async mode refuses to run on Windows' default ProactorEventLoop) - set here too,
    # for pytest-asyncio's own event loop, rather than relying on every test module
    # happening to import app.main first (some deliberately don't - see
    # tests/test_production_startup.py's own module docstring). conftest.py is always
    # collected before any test module, so this reliably runs first regardless.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    _attempts.clear()
    yield
    _attempts.clear()


@pytest.fixture(autouse=True)
def _capture_emails(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """PR 246: records outgoing email instead of sending it, so tests can confirm a new
    account with the emailed code (see tests/auth_helpers.py). Patched by string, so
    app.api.auth is only imported when a test runs, not while conftest.py loads."""
    sent_emails.clear()
    monkeypatch.setattr("app.api.auth.send_email", record_email)
    yield
    sent_emails.clear()
