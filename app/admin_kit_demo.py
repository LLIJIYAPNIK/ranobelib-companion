"""Made-up data for the UI kit's showcase page, /admin/_kit (PR 342, development only).

Nothing here touches the database: the table is a fixed in-memory list, sorted, filtered
and paged here the way a real screen's route would do it in SQL, so every control on the
page is live.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, timedelta

from app.admin_kit import (
    BarSeries,
    Column,
    Filter,
    bar_chart,
    delta,
    format_number,
    heatmap,
    line_chart,
    sparkline,
)

PER_PAGE = 10
_STATUSES = (
    ("active", "Активен", "success"),
    ("limited", "Ограничен", "warning"),
    ("blocked", "Заблокирован", "danger"),
    ("unverified", "Не подтверждён", "neutral"),
)
_STATUS_LABEL = {key: label for key, label, _ in _STATUSES}
_STATUS_TONE = {key: tone for key, _, tone in _STATUSES}
_NAMES = (
    "aster", "birch", "cobalt", "dune", "ember", "fjord", "garnet", "heron", "iris",
    "juniper", "kestrel", "lumen", "maple", "nimbus", "onyx", "pine", "quill", "rowan",
    "sable",
)  # fmt: skip
_MONTHS_SHORT = (
    "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек",
)  # fmt: skip
_WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

COLUMNS = (
    Column("nickname", "Никнейм", locked=True),
    Column("id", "ID", "mono"),
    Column("email", "Email"),
    Column("status", "Статус", sortable=False),
    Column("chapters", "Прочитано глав", "num"),
    Column("registered", "Регистрация", "mono"),
    Column("note", "Заметка", "muted", sortable=False, hidden=True),
    Column("actions", "Действия", "actions", sortable=False, locked=True),
)
_SORTABLE = {column.key for column in COLUMNS if column.sortable}


def _rows() -> list[dict]:
    start = date(2026, 1, 5)
    rows = []
    for i in range(57):
        name = f"{_NAMES[i % len(_NAMES)]}{i // len(_NAMES) + 1}"
        status = _STATUSES[(i * 7) % 11 % 4][0]
        rows.append(
            {
                "id": 1000 + i,
                "nickname": name,
                "email": f"{name}@example.com",
                "status": status,
                "status_label": _STATUS_LABEL[status],
                "status_tone": _STATUS_TONE[status],
                "chapters": (i * 37) % 420,
                "registered": (start + timedelta(days=i * 4)).isoformat(),
                "note": None if i % 3 else "Пример заметки",
            }
        )
    return rows


ROWS = _rows()


def table_context(args: Mapping[str, str]) -> dict:
    """The demo table after the page's query: search, status filter, sort, page."""
    query = (args.get("q") or "").strip().lower()
    status = args.get("status") or ""
    if status not in _STATUS_LABEL:
        status = ""
    sort = args.get("sort") if args.get("sort") in _SORTABLE else "id"
    descending = args.get("order") != "asc"

    rows = [
        row
        for row in ROWS
        if (not query or query in row["nickname"] or query in row["email"])
        and (not status or row["status"] == status)
    ]
    rows.sort(key=lambda row: row[sort], reverse=descending)
    total = len(rows)
    pages = max(1, math.ceil(total / PER_PAGE))
    try:
        page = min(max(1, int(args.get("page") or 1)), pages)
    except ValueError:
        page = 1

    kept = {key: args[key] for key in ("q", "status", "sort", "order", "page") if args.get(key)}
    return {
        "columns": COLUMNS,
        "rows": rows[(page - 1) * PER_PAGE : page * PER_PAGE],
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": PER_PAGE,
        "sort": sort,
        "descending": descending,
        "args": kept,
        "filters": [
            Filter("status", "Статус", tuple((key, label) for key, label, _ in _STATUSES), status)
        ],
        "filtered": bool(query or status),
    }


def charts_context(today: date) -> dict:
    """Charts and KPIs over the 30 days to ``today`` - smooth made-up curves."""
    days = [today - timedelta(days=29 - i) for i in range(30)]
    labels = [f"{d.day} {_MONTHS_SHORT[d.month - 1]}" for d in days]
    readers = [round(420 + 120 * math.sin(i / 4) + 6 * i) for i in range(30)]
    previous = [round(380 + 90 * math.sin(i / 4 + 1) + 4 * i) for i in range(30)]
    new = [round(18 + 8 * math.sin(i / 3)) for i in range(14)]
    returning = [round(70 + 20 * math.cos(i / 5)) for i in range(14)]
    matrix = [
        [max(0, round(30 * math.sin((hour - 6) / 24 * math.pi) ** 3 * (1.3 if day >= 5 else 1)))
         for hour in range(24)]
        for day in range(7)
    ]  # fmt: skip
    total, before = sum(readers), sum(previous)
    return {
        "kpis": [
            {
                "label": "Активные читатели",
                "icon": "users",
                "value": format_number(readers[-1]),
                "change": delta(readers[-1], previous[-1]),
                "previous": f"было {format_number(previous[-1])}",
                "spark": sparkline(readers[-14:]),
            },
            {
                "label": "Прочитано глав",
                "icon": "chart",
                "value": format_number(total),
                "change": delta(total, before),
                "previous": f"было {format_number(before)}",
                "spark": sparkline(previous[-14:]),
            },
            {
                "label": "Ошибки загрузок",
                "icon": "triangle-alert",
                "value": "7",
                "change": delta(7, 4, higher_is_better=False),
                "previous": "было 4",
                "spark": None,
            },
            {
                "label": "Регистрации",
                "icon": "users",
                "value": "0",
                "change": delta(0, 0),
                "previous": None,
                "spark": None,
            },
        ],
        "line": line_chart(readers, labels, name="Читатели", previous=previous),
        "bars": bar_chart(
            [BarSeries("Новые", new, "warning"), BarSeries("Вернувшиеся", returning)],
            labels[-14:],
            name="Новые и вернувшиеся",
        ),
        "heat": heatmap(
            matrix, list(_WEEKDAYS_SHORT), [f"{h}:00" for h in range(24)], name="Когда читают"
        ),
    }
