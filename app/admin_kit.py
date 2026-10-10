"""The admin panel's UI kit (PR 342): the plain data the macros in
app/templates/admin/_kit.html render - table columns, filters, page windows - and the
toast a POST leaves for the page it redirects to.

Everything here is presentation: it shapes values a route already has. Sorting, filtering
and paging the data itself stay with the route and app/db/.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode

from starlette.requests import Request

# --- tables -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One table column. ``kind`` picks the cell style: ``text``, ``num`` (right-aligned,
    tabular figures), ``mono`` (ids, slugs, timestamps) or ``muted``. A ``locked`` column
    can't be hidden from the «Колонки» menu; a ``hidden`` one starts hidden."""

    key: str
    label: str
    kind: str = "text"
    sortable: bool = True
    locked: bool = False
    hidden: bool = False


def page_window(page: int, pages: int, around: int = 1) -> list[int | None]:
    """The page numbers a pager shows: the first, the last and ``around`` on each side of
    the current one, with ``None`` where a run is skipped - 1 … 4 5 6 … 20."""
    if pages <= 1:
        return [1]
    shown = {1, pages, *range(page - around, page + around + 1)}
    numbers = sorted(n for n in shown if 1 <= n <= pages)
    window: list[int | None] = []
    for n in numbers:
        if window and n - window[-1] == 2:
            window.append(n - 1)  # a gap of one page - just show it
        elif window and n - window[-1] > 2:
            window.append(None)
        window.append(n)
    return window


def page_range(page: int, per_page: int, total: int) -> str:
    """«26–50 из 340» under a table, «0 записей» for none."""
    if total <= 0:
        return "0 записей"
    first = (page - 1) * per_page + 1
    last = min(page * per_page, total)
    return f"{first}–{last} из {total}"


def admin_query(args: Mapping[str, object], **changes: object) -> str:
    """``?a=1&b=2`` for a link that keeps the page's current query ``args`` with
    ``changes`` applied; a change to ``None`` or ``""`` drops that parameter. Used by the
    sort headers, pager and filter chips so each keeps the rest of the state."""
    merged = {**args, **changes}
    kept = {key: value for key, value in merged.items() if value not in (None, "")}
    return f"?{urlencode(kept)}" if kept else "?"


# --- filters ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """One ``<select>`` in a filter bar: ``name`` is its query parameter, ``options`` are
    ``(value, label)`` pairs and ``value`` is the one currently applied ("" for «Все»)."""

    name: str
    label: str
    options: tuple[tuple[str, str], ...]
    value: str = ""

    @property
    def value_label(self) -> str:
        return dict(self.options).get(self.value, self.value)


@dataclass(frozen=True)
class FilterChip:
    label: str
    remove: str


@dataclass(frozen=True)
class FilterState:
    """What a filter bar shows below its controls: one chip per applied condition (the
    search text included) and the «Сбросить (N)» link."""

    chips: list[FilterChip] = field(default_factory=list)
    reset: str = "?"

    @property
    def count(self) -> int:
        return len(self.chips)


def filter_state(
    args: Mapping[str, object],
    filters: tuple[Filter, ...] | list[Filter] = (),
    query_name: str = "q",
    keep: tuple[str, ...] = ("sort", "order"),
) -> FilterState:
    """Chips and the reset link for a filter bar. Removing a chip or resetting drops the
    page number too - page 7 of the old result rarely exists in the new one - but keeps
    the parameters in ``keep`` (the sort)."""
    chips = []
    query = str(args.get(query_name) or "")
    if query:
        chips.append(
            FilterChip(f"«{query}»", admin_query(args, **{query_name: None, "page": None}))
        )
    for item in filters:
        if item.value:
            chips.append(
                FilterChip(
                    f"{item.label}: {item.value_label}",
                    admin_query(args, **{item.name: None, "page": None}),
                )
            )
    reset = admin_query({key: args[key] for key in keep if key in args})
    return FilterState(chips=chips, reset=reset)


# --- toasts -------------------------------------------------------------------------------

TOAST_SESSION_KEY = "admin_toast"


@dataclass(frozen=True)
class Undo:
    """The toast's «Отменить»: a POST to ``action`` (with the CSRF token and ``fields``)
    that reverts what was just done."""

    action: str
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Toast:
    message: str
    tone: str = "success"  # or "error"
    undo: Undo | None = None


def push_toast(
    request: Request, message: str, *, tone: str = "success", undo: Undo | None = None
) -> None:
    """Shows ``message`` as a toast on the next admin page rendered for this session -
    the page a POST redirects to. One at a time; a newer one replaces it."""
    request.session[TOAST_SESSION_KEY] = {
        "message": message,
        "tone": tone,
        "undo": {"action": undo.action, "fields": dict(undo.fields)} if undo else None,
    }


def pop_toast(request: Request) -> Toast | None:
    data = request.session.pop(TOAST_SESSION_KEY, None)
    if not isinstance(data, dict) or not data.get("message"):
        return None
    undo = data.get("undo")
    return Toast(
        message=str(data["message"]),
        tone="error" if data.get("tone") == "error" else "success",
        undo=Undo(str(undo["action"]), dict(undo.get("fields") or {}))
        if isinstance(undo, dict) and undo.get("action")
        else None,
    )
