"""The application's own SQLite database - accounts, personal library, activity.

Separate from the SDK's ``cache_dir`` (see CLAUDE.md, "Архитектура"): that's a public,
shared cache of ranobelib.me responses; this is private per-user application data.
"""

from __future__ import annotations

import sqlite3

from app.config import get_settings

_connection: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    """A single process-wide connection, created lazily - mirrors the module-level
    ``_jobs`` store in ``app/jobs/store.py`` (single-process MVP deployment, see
    CLAUDE.md, "Фоновые задачи")."""
    global _connection
    if _connection is None:
        settings = get_settings()
        _connection = sqlite3.connect(settings.db_path, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
    return _connection
