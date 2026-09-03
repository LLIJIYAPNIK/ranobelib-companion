"""The application's own Postgres database - accounts, personal library, activity.

Separate from the SDK's ``cache_dir`` (see CLAUDE.md, "Архитектура"): that's a public,
shared cache of ranobelib.me responses; this is private per-user application data.
"""

from __future__ import annotations

import threading

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None
_local: threading.local | None = threading.local()


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        # max_size bounds how many concurrent per-thread connections (see get_connection()
        # below) this process can ever hand out - generous enough to cover FastAPI's own
        # thread-pool executor (its default capacity limiter allows up to 40 worker
        # threads) plus the event loop thread itself, with headroom to spare, without the
        # pool silently blocking a request forever waiting for a connection nobody's going
        # to return.
        _pool = ConnectionPool(get_settings().database_url, min_size=1, max_size=50, open=True)
    return _pool


def get_connection() -> Connection[DictRow]:
    """One ``psycopg.Connection`` per thread, not one shared process-wide (see PR 67 in
    CLAUDE.md's roadmap - this used to be a single ``sqlite3.Connection`` with
    ``check_same_thread=False``, which corrupted its internal state under concurrent
    access from two threads at once). FastAPI runs sync dependencies like
    ``get_current_user()`` through a thread pool (``run_in_threadpool``) alongside the
    main event loop thread running async route bodies directly, so two threads really
    could otherwise call ``.execute()`` on the same connection at once. Each thread now
    lazily checks out (and reuses, forever - never returned to the pool) its own
    connection from the shared ``ConnectionPool`` instead.

    ``autocommit`` is on (see PR 191 in CLAUDE.md's roadmap): every write in ``app/db/*.py``
    already calls ``conn.commit()`` right after its own ``execute()``, the same
    per-statement-commit shape ``sqlite3``'s default (non-autocommit-module) usage produced
    here - preserving that means an error (e.g. a duplicate email's ``UniqueViolation``,
    deliberately left uncaught in ``create_user()``/``update_user_account()`` - see their
    own docstrings) never leaves the connection sitting in Postgres's "current transaction
    is aborted" state, which would otherwise poison every later request sharing that same
    per-thread connection until something explicitly rolled it back.

    ``_local``/``_pool`` are still reset to ``None`` between tests (see ``tests/*.py``) the
    same way ``_connection`` used to be - resetting ``_local`` here just means starting a
    new ``threading.local()`` rather than reusing a leftover connection, so that existing
    reset contract still holds unchanged.
    """
    global _local
    if _local is None:
        _local = threading.local()
    conn: Connection[DictRow] | None = getattr(_local, "conn", None)
    if conn is None:
        conn = _get_pool().getconn()
        conn.autocommit = True
        conn.row_factory = dict_row
        _local.conn = conn
    return conn
