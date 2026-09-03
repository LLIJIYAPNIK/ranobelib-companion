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

import os
import threading

import psycopg
import pytest
from psycopg.rows import dict_row

import app.db.connection as db_connection

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ranobelib_test"
)


def fresh_connection() -> psycopg.Connection:
    """A connection to the shared test database with a completely empty (freshly wiped,
    not yet migrated) schema - the Postgres equivalent of ``sqlite3.connect(":memory:")``,
    for tests that call ``app/db/*.py`` functions directly rather than going through the
    app's own connection pool. ``autocommit`` matches ``get_connection()``'s own setting
    (see its docstring on why) so a caught error - e.g. ``create_user()``'s documented
    ``UniqueViolation`` - doesn't leave the connection unusable for the rest of the test."""
    conn = psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row, autocommit=True)
    wipe_schema()
    return conn


def wipe_schema() -> None:
    """Drops and recreates the shared test database's ``public`` schema - per-test
    isolation, the closest equivalent to a brand-new SQLite file. Also force-closes and
    drops ``app.db.connection``'s pool (see its own docstring on why ``get_connection()``
    checks a connection out and never returns it - by design for a long-lived production
    process's small, fixed set of worker threads). Left alone, a session with hundreds of
    tests each opening their own short-lived ``TestClient`` - each spinning up its own
    throwaway anyio worker threads - would check out a handful of connections every time
    and never give them back, eventually exhausting the pool's ``max_size`` partway
    through the run. ``ConnectionPool.close()`` forcibly reclaims every connection it
    handed out, checked-out or not, so each test starts from a clean, empty pool instead
    of accumulating the whole session's worth of leaked threads."""
    if db_connection._pool is not None:
        db_connection._pool.close()
    db_connection._pool = None
    db_connection._local = threading.local()
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


def reset_app_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Points ``DATABASE_URL`` at the shared test database and wipes its schema (see
    ``wipe_schema()``), ready for the app's own lifespan
    (``with TestClient(app) as client:``) to run migrations fresh - for tests that
    exercise the app *in this process* through ``TestClient`` rather than calling
    ``app/db/*.py`` functions directly. Replaces the old
    ``monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))`` pattern.

    A test that instead runs the app in a *subprocess* (see
    tests/test_production_startup.py) has no `monkeypatch` for this process's env to
    patch - call ``wipe_schema()`` directly and pass ``DATABASE_URL=TEST_DATABASE_URL``
    into the subprocess's own environment instead."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    wipe_schema()
