"""«Система» (PR 351): the database's side - how fast it answers, how big it is, how many
connections it has. Read-only, inside PR 326's transaction with the admin timeout."""

from __future__ import annotations

import time
from dataclasses import dataclass

from psycopg import AsyncConnection

from app.db.admin_data import _read_only


@dataclass(frozen=True)
class DatabaseStatus:
    ping_ms: float  # a SELECT 1's round trip
    size_bytes: int
    connections: int  # to this database, from every client - not only this process
    server_version: str


async def database_status(conn: AsyncConnection) -> DatabaseStatus:
    async with _read_only(conn):
        started = time.perf_counter()
        await conn.execute("SELECT 1")
        ping_ms = (time.perf_counter() - started) * 1000
        cursor = await conn.execute(
            "SELECT pg_database_size(current_database()) AS size, "
            "(SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()) "
            "AS connections, current_setting('server_version') AS version"
        )
        row = await cursor.fetchone()
    return DatabaseStatus(
        ping_ms=ping_ms,
        size_bytes=int(row["size"]),
        connections=int(row["connections"]),
        server_version=row["version"],
    )
