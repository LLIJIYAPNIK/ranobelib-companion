"""Read-only data for the /admin panel (PR 325): the overview and the table browser.

Nothing here writes. Table and column names never come from the request: the only
identifiers that reach SQL are ones read back from the database's own
``information_schema`` (``table_columns()``), passed through ``psycopg.sql.Identifier``;
a request can only *pick* among them. Values (search text, limits, offsets) are bound
parameters.

Secret columns - password/token/code hashes, ``session_version`` and anything named like
them (``is_secret_column()``) - are never selected: the query puts NULL in their place,
so their values don't leave the database at all and the page shows a mask instead. They
can't be sorted or searched on either (searching would leak them one guess at a time).

PR 326: every function runs its queries inside ``_read_only()`` - a READ ONLY transaction
with ``statement_timeout`` at QUERY_TIMEOUT_MS, so nothing here can write even by
mistake, and a slow COUNT/OFFSET on a big table is cancelled (QueryCanceled, which the
routes turn into an error page) instead of tying up a pool connection. A cell's text is
cut to CELL_LIMIT characters in SQL, so a huge value never leaves the database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection, sql

PAGE_SIZE = 50
MASK = "•••"
QUERY_TIMEOUT_MS = 5000
CELL_LIMIT = 1000


@asynccontextmanager
async def _read_only(conn: AsyncConnection) -> AsyncIterator[None]:
    async with conn.transaction():
        await conn.execute("SET TRANSACTION READ ONLY")
        # SET LOCAL can't take a bound parameter; set_config(..., true) is the same thing.
        await conn.execute(
            "SELECT set_config('statement_timeout', %s, true)", (str(QUERY_TIMEOUT_MS),)
        )
        yield


_SECRET_COLUMNS = {
    ("users", "password_hash"),
    ("users", "session_version"),
    ("password_reset_tokens", "token_hash"),
    ("email_verification_tokens", "token_hash"),
    ("email_verification_tokens", "code_hash"),
}


def looks_secret(name: str) -> bool:
    """A column or key named like a secret - a hash, token, password or secret. Shared
    with the action log's scrubbing of details (app/db/admin_audit.py, PR 343)."""
    name = name.lower()
    return (
        name.endswith("_hash")
        or "token" in name
        or "secret" in name
        or "password" in name
        or "csrf" in name
        or name == "session_version"
    )


def is_secret_column(table: str, column: str) -> bool:
    """The known secrets, plus any column named like one - so a future token/hash column
    is hidden by default instead of shown until someone remembers to list it."""
    return (table, column) in _SECRET_COLUMNS or looks_secret(column)


async def table_columns(conn: AsyncConnection) -> dict[str, list[str]]:
    """Every base table in the public schema -> its columns in definition order. This is
    the whitelist: a table/column name from a request is only ever looked up in it."""
    async with _read_only(conn):
        cursor = await conn.execute(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c "
            "JOIN information_schema.tables t "
            "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
            "ORDER BY c.table_name, c.ordinal_position"
        )
        rows = await cursor.fetchall()
    tables: dict[str, list[str]] = {}
    for row in rows:
        tables.setdefault(row["table_name"], []).append(row["column_name"])
    return tables


@dataclass(frozen=True)
class TableSummary:
    name: str
    columns: int
    rows: int


async def list_tables(conn: AsyncConnection) -> list[TableSummary]:
    summaries = []
    tables = await table_columns(conn)
    async with _read_only(conn):
        for name, columns in tables.items():
            cursor = await conn.execute(
                sql.SQL("SELECT COUNT(*) AS n FROM {}").format(sql.Identifier(name))
            )
            row = await cursor.fetchone()
            summaries.append(TableSummary(name=name, columns=len(columns), rows=row["n"]))
    return summaries


@dataclass(frozen=True)
class Overview:
    counts: list[tuple[str, int]]  # (label, rows)
    recent_users: list[dict[str, Any]]
    recent_download_errors: list[dict[str, Any]]


_OVERVIEW_COUNTS = (
    ("Пользователи", "users"),
    ("Записи в библиотеках", "library_entries"),
    ("Комментарии", "comments"),
    ("Загрузки", "download_history"),
    ("События активности", "activity_events"),
)


async def overview(conn: AsyncConnection, recent: int = 5) -> Overview:
    async with _read_only(conn):
        return await _overview(conn, recent)


async def _overview(conn: AsyncConnection, recent: int) -> Overview:
    counts = []
    for label, table in _OVERVIEW_COUNTS:
        cursor = await conn.execute(
            sql.SQL("SELECT COUNT(*) AS n FROM {}").format(sql.Identifier(table))
        )
        counts.append((label, (await cursor.fetchone())["n"]))
    cursor = await conn.execute(
        "SELECT id, email, nickname, created_at, email_verified_at FROM users "
        "ORDER BY id DESC LIMIT %s",
        (recent,),
    )
    recent_users = await cursor.fetchall()
    cursor = await conn.execute(
        "SELECT d.id, u.email, d.slug_url, d.fmt, d.error, d.finished_at "
        "FROM download_history d JOIN users u ON u.id = d.user_id "
        "WHERE d.status = 'error' ORDER BY d.finished_at DESC, d.id DESC LIMIT %s",
        (recent,),
    )
    return Overview(
        counts=counts,
        recent_users=recent_users,
        recent_download_errors=await cursor.fetchall(),
    )


@dataclass(frozen=True)
class TablePage:
    table: str
    columns: list[str]
    secret: set[str]
    rows: list[dict[str, Any]]
    total: int
    page: int
    pages: int
    sort: str
    descending: bool
    query: str


async def browse_table(
    conn: AsyncConnection,
    table: str,
    columns: list[str],
    *,
    page: int = 1,
    sort: str | None = None,
    descending: bool = True,
    query: str = "",
) -> TablePage:
    """One page of `table` (already looked up in table_columns() by the caller, with its
    `columns`). `sort` falls back to "id" (or the first column) unless it's a visible
    column of this table; `query` is a case-insensitive substring match over the visible
    columns' text."""
    async with _read_only(conn):
        return await _browse(conn, table, columns, page, sort, descending, query)


async def _browse(
    conn: AsyncConnection,
    table: str,
    columns: list[str],
    page: int,
    sort: str | None,
    descending: bool,
    query: str,
) -> TablePage:
    secret = {column for column in columns if is_secret_column(table, column)}
    visible = [column for column in columns if column not in secret]
    if sort not in visible:
        sort = "id" if "id" in visible else (visible[0] if visible else columns[0])

    # Columns are qualified with the "t" alias: the output names below are the same as
    # the real columns, and ORDER BY would otherwise pick the cut-down text output (so id
    # 12 would sort before 9).
    select_list = sql.SQL(", ").join(
        sql.SQL("NULL AS {}").format(sql.Identifier(column))
        if column in secret
        else sql.SQL("LEFT(CAST(t.{} AS TEXT), {}) AS {}").format(
            sql.Identifier(column), sql.Literal(CELL_LIMIT), sql.Identifier(column)
        )
        for column in columns
    )
    where = sql.SQL("")
    params: list[Any] = []
    query = query.strip()
    if query and visible:
        where = sql.SQL(" WHERE ") + sql.SQL(" OR ").join(
            sql.SQL("CAST(t.{} AS TEXT) ILIKE %s").format(sql.Identifier(column))
            for column in visible
        )
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params = [pattern] * len(visible)

    count_cursor = await conn.execute(
        sql.SQL("SELECT COUNT(*) AS n FROM {} AS t").format(sql.Identifier(table)) + where,
        params,
    )
    total = (await count_cursor.fetchone())["n"]
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    order = sql.SQL(" ORDER BY t.{} {} NULLS LAST").format(
        sql.Identifier(sort), sql.SQL("DESC" if descending else "ASC")
    )
    cursor = await conn.execute(
        sql.SQL("SELECT {} FROM {} AS t").format(select_list, sql.Identifier(table))
        + where
        + order
        + sql.SQL(" LIMIT %s OFFSET %s"),
        [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
    )
    return TablePage(
        table=table,
        columns=columns,
        secret=secret,
        rows=await cursor.fetchall(),
        total=total,
        page=page,
        pages=pages,
        sort=sort,
        descending=descending,
        query=query,
    )
