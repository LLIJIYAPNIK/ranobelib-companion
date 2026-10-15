"""«Данные БД» (PR 352): PR 325's table browser on the PR 342 UI kit - the columns and rows
admin/tables.html and admin/table.html render. Presentation only; what may be read, and
what stays masked, is decided in app/db/admin_data.py.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import urlencode

from app.admin_kit import Column, format_number
from app.db.admin_data import TableExport, TablePage, TableSummary

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


# --- CSV ----------------------------------------------------------------------------------

# A spreadsheet runs a cell that starts like a formula - a comment or nickname such as
# «=HYPERLINK(...)» must arrive as text, so such a cell gets a leading apostrophe.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object, masked: bool, mask: str) -> str:
    if masked:
        return mask
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def table_csv(export: TableExport, mask: str) -> bytes:
    """The export as UTF-8 CSV with a BOM (so Excel reads the Cyrillic right). Secret
    columns hold the mask - their values were never selected in the first place."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(export.columns)
    for row in export.rows:
        writer.writerow(
            [_csv_cell(row[name], name in export.secret, mask) for name in export.columns]
        )
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def csv_filename(table: str, now: datetime) -> str:
    return f"{table}-{now:%Y%m%d-%H%M}.csv"


def csv_href(table: str, args: Mapping[str, object]) -> str:
    """The export link for what the page shows now: its search and sort, not its page."""
    kept = {key: args[key] for key in ("q", "sort", "order") if args.get(key)}
    query = urlencode(kept)
    return f"/admin/tables/{table}/csv" + (f"?{query}" if query else "")
