"""Shared Postgres test-database helpers - see CLAUDE.md's PR 191 roadmap entry.

The suite now runs against a real Postgres instance rather than a fresh temp SQLite file
per test (see ``app/db/connection.py``'s own docstring on why: keeping one dialect in
tests and another in production is exactly the kind of test/prod mismatch PR 191 set out
to remove). ``TEST_DATABASE_URL`` names one shared database; per-test isolation instead
comes from dropping and recreating its ``public`` schema before each test, the closest
equivalent to a brand-new SQLite file - ``ci.yml`` provides this database via a Postgres
service container, and local runs need one too (e.g. ``docker run`` - see README.md).
"""

from __future__ import annotations

import asyncio
import os
import selectors
import sys
from collections.abc import Coroutine

import psycopg
import pytest
from psycopg.rows import dict_row

import app.db.connection as db_connection

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ranobelib_test"
)


def run_async[T](coro: Coroutine[object, object, T]) -> T:
    """`asyncio.run()`, but always on a Selector-based event loop on Windows - same
    requirement as app/main.py's own `WindowsSelectorEventLoopPolicy` (psycopg's async
    mode refuses to run on Windows' default ProactorEventLoop), set as a side effect of
    importing app.main there. Callers here can't rely on that import having already
    happened - tests/test_production_startup.py deliberately never imports app.main in
    this process (see its own module docstring) - so this sets the loop explicitly
    instead of assuming some *other* test module already did."""
    if sys.platform == "win32":

        def _selector_loop() -> asyncio.SelectorEventLoop:
            return asyncio.SelectorEventLoop(selectors.SelectSelector())

        return asyncio.run(coro, loop_factory=_selector_loop)
    return asyncio.run(coro)


async def fresh_connection() -> psycopg.AsyncConnection:
    """A connection to the shared test database with a completely empty (freshly wiped,
    not yet migrated) schema - the Postgres equivalent of ``sqlite3.connect(":memory:")``,
    for tests that call ``app/db/*.py`` functions directly rather than going through the
    app's own connection pool. ``autocommit`` matches ``get_connection()``'s own setting
    (see its docstring on why) so a caught error - e.g. ``create_user()``'s documented
    ``UniqueViolation`` - doesn't leave the connection unusable for the rest of the test."""
    conn = await psycopg.AsyncConnection.connect(
        TEST_DATABASE_URL, row_factory=dict_row, autocommit=True
    )
    await wipe_schema()
    return conn


async def wipe_schema() -> None:
    """Drops and recreates the shared test database's ``public`` schema - per-test
    isolation, the closest equivalent to a brand-new SQLite file. Also force-closes and
    drops ``app.db.connection``'s pool: every checkout from it is released back through an
    ``async with`` block (whether via the ``get_connection()`` FastAPI dependency or
    ``connection()``'s own direct use), so nothing actually leaks the way the old
    per-thread sync connection could - this reset exists so a stale pool bound to a
    previous test's event loop is never reused by pytest-asyncio's next one, since a
    psycopg ``AsyncConnectionPool`` (unlike a plain connection) keeps background tasks
    tied to the loop it was opened under."""
    if db_connection._pool is not None:
        await db_connection._pool.close()
    db_connection._pool = None
    async with await psycopg.AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")


def reset_app_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Points ``DATABASE_URL`` at the shared test database and wipes its schema (see
    ``wipe_schema()``), ready for the app's own lifespan
    (``with TestClient(app) as client:``) to run migrations fresh - for tests that
    exercise the app *in this process* through ``TestClient`` rather than calling
    ``app/db/*.py`` functions directly. Replaces the old
    ``monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))`` pattern.

    Stays a plain sync function (bridging into ``wipe_schema()`` via ``run_async()``)
    rather than becoming ``async def`` itself, so every one of this repo's existing sync
    pytest fixtures can keep calling it exactly as before, unchanged - the event loop
    ``run_async()`` opens and closes here is entirely separate from (and finished well
    before) the one `TestClient`'s own portal spins up next.

    A test that instead runs the app in a *subprocess* (see
    tests/test_production_startup.py) has no `monkeypatch` for this process's env to
    patch - call ``run_async(wipe_schema())`` directly and pass
    ``DATABASE_URL=TEST_DATABASE_URL`` into the subprocess's own environment instead."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    run_async(wipe_schema())


async def areset_app_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same as ``reset_app_database()`` above, ``await``ed directly instead of bridged
    through ``run_async()`` - for a fixture that's itself ``async def`` (because it also
    needs to ``async with connection()`` for its own setup, say), where nesting
    ``run_async()``'s own ``asyncio.run()`` inside the event loop already running that
    fixture would raise ``RuntimeError: asyncio.run() cannot be called from a running
    event loop``."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await wipe_schema()
