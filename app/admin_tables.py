"""«Данные БД» (PR 352): PR 325's table browser on the PR 342 UI kit - the columns and rows
admin/tables.html and admin/table.html render. Presentation only; what may be read, and
what stays masked, is decided in app/db/admin_data.py.
"""

from __future__ import annotations

from app.admin_kit import Column, format_number
from app.db.admin_data import TablePage, TableSummary

TABLE_LIST_COLUMNS = (
    Column("name", "Таблица", "mono", sortable=False, locked=True),
    Column("rows", "Строк", "num", sortable=False),
    Column("columns", "Колонок", "num", sortable=False),
)

# Shown truncated in a cell; the full value (up to the SQL-side CELL_LIMIT) is in a tooltip.
CELL_PREVIEW = 120


def table_list_rows(tables: list[TableSummary]) -> list[dict[str, str]]:
    return [
        {"name": table.name, "rows": format_number(table.rows), "columns": str(table.columns)}
        for table in tables
    ]


def browse_columns(data: TablePage) -> list[Column]:
    """One kit column per real column. A masked one can't be sorted (that would leak its
    order); the first column can't be hidden, so a row is always identifiable."""
    return [
        Column(
            name,
            name,
            "mono" if name == "id" or name.endswith("_id") else "text",
            sortable=name not in data.secret,
            locked=index == 0,
        )
        for index, name in enumerate(data.columns)
    ]


def table_id(name: str) -> str:
    """The kit table's id - also the key its «Колонки» choice is remembered under, so each
    database table keeps its own."""
    return f"db-{name}"
