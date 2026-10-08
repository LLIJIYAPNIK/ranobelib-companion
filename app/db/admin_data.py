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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection, sql

PAGE_SIZE = 50
MASK = "•••"

_SECRET_COLUMNS = {
    ("users", "password_hash"),
    ("users", "session_version"),
    ("password_reset_tokens", "token_hash"),
    ("email_verification_tokens", "token_hash"),
    ("email_verification_tokens", "code_hash"),
}


def is_secret_column(table: str, column: str) -> bool:
    """The known secrets, plus any column named like one - so a future token/hash column
    is hidden by default instead of shown until someone remembers to list it."""
    name = column.lower()
    return (
        (table, column) in _SECRET_COLUMNS
        or name.endswith("_hash")
        or "token" in name
        or "secret" in name
        or "password" in name
        or name == "session_version"
    )


async def table_columns(conn: AsyncConnection) -> dict[str, list[str]]:
    """Every base table in the public schema -> its columns in definition order. This is
    the whitelist: a table/column name from a request is only ever looked up in it."""
    cursor = await conn.execute(
        "SELECT c.table_name, c.column_name FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
        "ORDER BY c.table_name, c.ordinal_position"
    )
    tables: dict[str, list[str]] = {}
    for row in await cursor.fetchall():
        tables.setdefault(row["table_name"], []).append(row["column_name"])
    return tables


@dataclass(frozen=True)
class TableSummary:
    name: str
    columns: int
    rows: int


async def list_tables(conn: AsyncConnection) -> list[TableSummary]:
    summaries = []
    for name, columns in (await table_columns(conn)).items():
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
    secret = {column for column in columns if is_secret_column(table, column)}
    visible = [column for column in columns if column not in secret]
    if sort not in visible:
        sort = "id" if "id" in visible else (visible[0] if visible else columns[0])

    select_list = sql.SQL(", ").join(
        sql.SQL("NULL AS {}").format(sql.Identifier(column))
        if column in secret
        else sql.Identifier(column)
        for column in columns
    )
    where = sql.SQL("")
    params: list[Any] = []
    query = query.strip()
    if query and visible:
        where = sql.SQL(" WHERE ") + sql.SQL(" OR ").join(
            sql.SQL("CAST({} AS TEXT) ILIKE %s").format(sql.Identifier(column))
            for column in visible
        )
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params = [pattern] * len(visible)

    count_cursor = await conn.execute(
        sql.SQL("SELECT COUNT(*) AS n FROM {}").format(sql.Identifier(table)) + where, params
    )
    total = (await count_cursor.fetchone())["n"]
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    order = sql.SQL(" ORDER BY {} {} NULLS LAST").format(
        sql.Identifier(sort), sql.SQL("DESC" if descending else "ASC")
    )
    cursor = await conn.execute(
        sql.SQL("SELECT {} FROM {}").format(select_list, sql.Identifier(table))
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
