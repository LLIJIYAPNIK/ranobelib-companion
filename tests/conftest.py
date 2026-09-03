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
