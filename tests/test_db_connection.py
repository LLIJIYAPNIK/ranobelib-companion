"""app/db/connection.py - see PR 67 in CLAUDE.md's roadmap: a single process-wide
sqlite3.Connection shared across threads used to crash under concurrent access (the
"Загрузки" page's /downloads/status and /downloads/ready polling, PR 17/50/56, hitting it
from FastAPI's thread pool at the same time another thread was using it directly).

PR 191's async follow-up replaced that per-thread-cached connection with a pooled
``AsyncConnection`` checked out fresh - via ``connection()``/``get_connection()`` - for the
scope of just one request/background task, and always returned to the pool afterward. The
original bug (two threads sharing and corrupting one Connection object) is now
structurally impossible: nothing is ever shared between concurrent callers to begin with.
These tests cover that design instead - concurrent checkouts are independent connections,
and heavy concurrent use of the pool doesn't error or corrupt anything, the async
equivalent of PR 67's own thread-based torture test."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.config import get_settings
from app.db.connection import close_pool, connection, open_pool
from app.db.migrate import run_migrations
from tests.db_reset import areset_app_database


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    await areset_app_database(monkeypatch)
    get_settings.cache_clear()
    # No TestClient/app.main lifespan here to open the pool (see its own docstring on why
    # get_connection()/connection() need it opened first) - open it directly instead.
    await open_pool()
    async with connection() as conn:
        await run_migrations(conn)
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) "
            "VALUES (1, 'alice@example.com', 'hash', 'now')"
        )

    yield

    await close_pool()
    get_settings.cache_clear()


async def test_concurrent_checkouts_are_independent_connections(db: None) -> None:
    async with connection() as first, connection() as second:
        assert first is not second


async def test_pool_survives_heavy_concurrent_use(db: None) -> None:
    """Reproduces the bug report's shape under load, adapted to asyncio tasks instead of
    threads: mostly-reader tasks (standing in for the many concurrent requests
    get_current_user()/get_connection() serve on almost every page, plus
    /downloads/status and /downloads/ready polling, which are themselves reads) alongside
    a couple of writer tasks (an occasional real write - recording a download, adding a
    library entry). Each task checks out its own connection via connection() and never
    shares it, so there's nothing left here to corrupt the way the old shared-Connection
    implementation could - this instead guards against the pool itself erroring or
    deadlocking under a burst of concurrent checkouts."""
    READER_COUNT = 20
    WRITER_COUNT = 2
    ITERATIONS_PER_TASK = 200
    errors: list[Exception] = []

    async def read() -> None:
        try:
            async with connection() as conn:
                for _ in range(ITERATIONS_PER_TASK):
                    cursor = await conn.execute("SELECT * FROM users WHERE id = 1")
                    await cursor.fetchone()
        except Exception as exc:
            errors.append(exc)

    async def write() -> None:
        try:
            async with connection() as conn:
                for _ in range(ITERATIONS_PER_TASK):
                    await conn.execute(
                        "INSERT INTO download_history "
                        "(user_id, slug_url, fmt, status, chapter_count, error, finished_at) "
                        "VALUES (1, 'x', 'epub', 'done', 1, NULL, 'now')"
                    )
        except Exception as exc:
            errors.append(exc)

    tasks = [asyncio.create_task(read()) for _ in range(READER_COUNT)]
    tasks += [asyncio.create_task(write()) for _ in range(WRITER_COUNT)]
    await asyncio.gather(*tasks)

    assert errors == []
