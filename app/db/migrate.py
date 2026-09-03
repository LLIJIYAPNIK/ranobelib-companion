"""Minimal SQL-file migration runner for the application database.

No Alembic/SQLAlchemy - a single ``users`` table doesn't warrant the extra dependency
(see CLAUDE.md's stance on not introducing infrastructure ahead of actual need). Applied
migrations are tracked in ``schema_migrations`` so re-running on every startup is a
no-op once a migration has landed.
"""

from __future__ import annotations

from pathlib import Path

from psycopg import AsyncConnection

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(conn: AsyncConnection) -> None:
    await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY)")
    cursor = await conn.execute("SELECT filename FROM schema_migrations")
    applied = {row["filename"] for row in await cursor.fetchall()}

    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        # A plain SQL string with no query parameters runs as psycopg's "simple query"
        # protocol, which - unlike a parameterized execute() - allows several ;-separated
        # statements in one call, the closest equivalent to sqlite3's own
        # conn.executescript() (a method that has no Postgres-driver analogue at all).
        await conn.execute(path.read_text(encoding="utf-8"))
        await conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
