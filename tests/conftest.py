"""Shared, autouse test fixtures.

app.auth.rate_limit._attempts (PR 188) is a process-wide dict, same as
app.jobs.store._jobs - every test module in this suite shares one pytest process, and
several of them log in/register the same "alice@example.com" from the TestClient's fixed
"testclient" host. Without a reset between tests, attempts recorded by one test's
POST /login or /register would count against the next test's, eventually tripping the
rate limit in a completely unrelated test.
"""

from collections.abc import Iterator

import pytest

from app.auth.rate_limit import _attempts


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    _attempts.clear()
    yield
    _attempts.clear()
