"""The application's own Postgres database - accounts, personal library, activity.

Separate from the SDK's ``cache_dir`` (see CLAUDE.md, "Архитектура"): that's a public,
shared cache of ranobelib.me responses; this is private per-user application data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

_pool: AsyncConnectionPool | None = None


def _get_pool() -> AsyncConnectionPool:
    """Lazily builds (but doesn't open - see ``open_pool()``) the process-wide pool.

    Every checked-out connection is ``autocommit`` and uses ``dict_row`` (configured once
    here via ``kwargs``, not per call site): every write in ``app/db/*.py`` already reads
    like a per-statement-commit (no explicit transaction spans more than one ``execute()``
    call), so autocommit means an uncaught error - e.g. ``create_user()``'s documented
    ``UniqueViolation`` - never leaves a connection sitting in Postgres's "current
    transaction is aborted" state for whatever request or background task picks it up
    next.
    """
    global _pool
    if _pool is None:
        # max_size bounds how many concurrent connections this process can ever hand out
        # - generous enough to cover a real burst of concurrent requests without the pool
        # silently blocking one forever waiting for another to be returned.
        _pool = AsyncConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=50,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
    return _pool


async def open_pool() -> None:
    """Opens the pool - called once from app.main's lifespan at startup. Actually
    connecting inside a synchronous ``AsyncConnectionPool.__init__`` is deprecated in
    psycopg_pool, hence the separate ``open=False`` + explicit async open here."""
    await _get_pool().open()


async def close_pool() -> None:
    """Closes the pool and forgets it - called from app.main's lifespan at shutdown (and
    by tests/db_reset.py between tests, to reclaim connections a short-lived TestClient's
    own throwaway worker tasks never explicitly returned)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_connection() -> AsyncIterator[AsyncConnection[DictRow]]:
    """FastAPI dependency: checks one connection out of the pool for the lifetime of the
    request, returning it afterwards - ``Depends(get_connection)`` in a route (or in a
    dependency of that route, e.g. ``get_current_user()``) resolves to the same connection
    throughout that one request (FastAPI caches a dependency's result per request), so
    unlike the old per-thread sqlite3/sync-psycopg connection, this is never held onto
    past the request it was checked out for.

    Background work outside a request (app/jobs/download.py's whole-title download job)
    doesn't go through FastAPI's dependency injection at all - it uses ``connection()``
    below directly instead.
    """
    async with connection() as conn:
        yield conn


def connection() -> AbstractAsyncContextManager[AsyncConnection[DictRow]]:
    """The same pooled-connection checkout as ``get_connection()``, as a plain async
    context manager rather than a FastAPI dependency - for callers outside a request's
    dependency-injection scope (a background ``asyncio`` task, a migration run at
    startup, test setup)."""
    return _get_pool().connection()
