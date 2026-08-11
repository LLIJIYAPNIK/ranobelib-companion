"""The application's own SQLite database - accounts, personal library, activity.

Separate from the SDK's ``cache_dir`` (see CLAUDE.md, "Архитектура"): that's a public,
shared cache of ranobelib.me responses; this is private per-user application data.
"""

from __future__ import annotations

import sqlite3
import threading

from app.config import get_settings

_connection: threading.local | None = threading.local()


def get_connection() -> sqlite3.Connection:
    """One ``sqlite3.Connection`` per thread, not one shared process-wide (see PR 67 in
    CLAUDE.md's roadmap - this used to be a single ``Connection`` with
    ``check_same_thread=False``, which only turns off sqlite3's own same-thread check,
    not thread-safety of the object itself). FastAPI runs sync dependencies like
    ``get_current_user()`` through a thread pool (``run_in_threadpool``) alongside the
    main event loop thread running async route bodies directly, so two threads really
    could call ``.execute()`` on the same shared ``Connection`` at once - that's exactly
    what corrupted it under concurrent ``/downloads`` polling in the bug report. Each
    thread now lazily opens (and reuses) its own connection to the same on-disk file
    instead; sqlite3 serializes writes to that file on its own, so this needs no extra
    app-level locking. ``check_same_thread`` is left at its default (True) since a
    connection is now genuinely only ever touched by the thread that created it.

    ``_connection`` is still reset to ``None`` between tests (see ``tests/*.py``) to
    force a fresh connection to that test's own temp DB path - resetting it here just
    means starting a new ``threading.local()`` rather than reusing a leftover
    connection, so that existing reset contract still holds unchanged.
    """
    global _connection
    if _connection is None:
        _connection = threading.local()
    conn: sqlite3.Connection | None = getattr(_connection, "conn", None)
    if conn is None:
        settings = get_settings()
        conn = sqlite3.connect(settings.db_path)
        conn.row_factory = sqlite3.Row
        _connection.conn = conn
    return conn
