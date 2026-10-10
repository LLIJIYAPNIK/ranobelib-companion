"""The admin panel's UI kit (PR 342): the plain data the macros in
app/templates/admin/_kit.html render - table columns, filters, page windows, chart
geometry, KPI deltas - and the toast a POST leaves for the page it redirects to.

Everything here is presentation: it shapes values a route already has. Sorting, filtering
and paging the data itself stay with the route and app/db/.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
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


# --- numbers and deltas -------------------------------------------------------------------


def format_number(value: float) -> str:
    """12 480 and 3,5: thousands split by a narrow no-break space, a decimal comma, one
    decimal place for non-integers."""
    if float(value).is_integer():
        text = f"{int(value):,}"
    else:
        text = f"{value:,.1f}"
    return text.replace(",", " ").replace(".", ",")


@dataclass(frozen=True)
class Delta:
    """A KPI's change against the previous period: «+12,4%», which way, whether good."""

    text: str
    direction: str  # "up", "down" or "flat"
    good: bool | None

    @property
    def tone(self) -> str:
        if self.good is None:
            return "neutral"
        return "success" if self.good else "danger"

    @property
    def icon(self) -> str:
        return {"up": "trending-up", "down": "trending-down"}.get(self.direction, "minus")


def delta(current: float, previous: float | None, *, higher_is_better: bool = True) -> Delta | None:
    """The change from ``previous`` to ``current`` in percent; None without a previous
    value. From zero there's no percentage - just «новое»."""
    if previous is None:
        return None
    if previous == 0:
        if current == 0:
            return Delta("0%", "flat", None)
        return Delta("новое", "up", higher_is_better)
    change = round((current - previous) / previous * 100, 1)
    if change == 0:
        return Delta("0%", "flat", None)
    sign = "+" if change > 0 else "−"
    direction = "up" if change > 0 else "down"
    return Delta(
        f"{sign}{format_number(abs(change))}%", direction, (change > 0) == higher_is_better
    )


# --- charts -------------------------------------------------------------------------------
# The geometry of the kit's SVG charts, computed here so the templates only place it.
# Charts draw in a viewBox CHART_WIDTH wide (stretched to the panel); labels outside the
# SVG are positioned in percent of the same box.

CHART_WIDTH = 1000

Format = Callable[[float], str]


def _nice_step(value: float) -> float:
    """The smallest 1/2/2.5/5 x 10^k at or above ``value`` - a round axis step."""
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    return next(f * magnitude for f in (1, 2, 2.5, 5, 10) if f * magnitude >= value)


def _evenly(count: int, wanted: int) -> list[int]:
    """Up to ``wanted`` indexes spread over ``count`` items, first and last included."""
    if count <= wanted:
        return list(range(count))
    return sorted({round(i * (count - 1) / (wanted - 1)) for i in range(wanted)})


def _y_axis(top_value: float, fmt: Format) -> tuple[float, list[float], list[str]]:
    """Four round gridlines from zero: the scale's top and each line's value/label."""
    step = _nice_step(top_value / 3)
    return step * 3, [step * i for i in range(4)], [fmt(step * i) for i in range(4)]


@dataclass(frozen=True)
class Tick:
    position: float  # percent: from the top for y, from the left for x
    text: str


@dataclass(frozen=True)
class ChartPoint:
    """One x position of a chart, for its hover: where it is and what to say there."""

    x: float  # percent from the left
    y: float | None  # percent from the top (a line's dot), None for bars
    label: str
    rows: tuple[tuple[str, str], ...]  # (series name, formatted value)


@dataclass(frozen=True)
class LineChart:
    name: str
    height: int
    line: str
    area: str
    previous: str | None
    previous_name: str
    grid: list[float]  # y in viewBox units
    y_ticks: list[Tick]
    x_ticks: list[Tick]
    points: list[ChartPoint]


def line_chart(
    values: list[float],
    labels: list[str],
    *,
    name: str,
    previous: list[float] | None = None,
    previous_name: str = "Прошлый период",
    fmt: Format = format_number,
    height: int = 270,
    x_tick_count: int = 6,
) -> LineChart:
    """One metric over time, with the previous period of the same length dashed on the
    same scale - never two metrics on one axis."""
    top, bottom = 12.0, height - 8.0
    scale_top, steps, step_labels = _y_axis(max([*values, *(previous or []), 0]), fmt)
    count = len(values)

    def x_of(index: int) -> float:
        return CHART_WIDTH / 2 if count <= 1 else index * CHART_WIDTH / (count - 1)

    def y_of(value: float) -> float:
        return bottom - (value / scale_top) * (bottom - top)

    def path(series: list[float]) -> str:
        return " ".join(
            f"{'M' if i == 0 else 'L'}{x_of(i):.1f},{y_of(v):.1f}"
            for i, v in enumerate(series[:count])
        )

    line = path(values)
    area = ""
    if values:
        area = f"{line} L{x_of(count - 1):.1f},{bottom:.1f} L{x_of(0):.1f},{bottom:.1f} Z"
    points = []
    for i, (value, label) in enumerate(zip(values, labels, strict=True)):
        rows = [(name, fmt(value))]
        if previous is not None and i < len(previous):
            rows.append((previous_name, fmt(previous[i])))
        points.append(
            ChartPoint(x_of(i) / CHART_WIDTH * 100, y_of(value) / height * 100, label, tuple(rows))
        )
    return LineChart(
        name=name,
        height=height,
        line=line,
        area=area,
        previous=path(previous) if previous else None,
        previous_name=previous_name,
        grid=[y_of(step) for step in steps],
        y_ticks=[Tick(y_of(s) / height * 100, t) for s, t in zip(steps, step_labels, strict=True)],
        x_ticks=[
            Tick(x_of(i) / CHART_WIDTH * 100, labels[i]) for i in _evenly(count, x_tick_count)
        ],
        points=points,
    )


@dataclass(frozen=True)
class BarSeries:
    name: str
    values: list[float]
    tone: str = "primary"  # primary, success, warning, danger, neutral


@dataclass(frozen=True)
class BarRect:
    x: float
    y: float
    width: float
    height: float
    tone: str


@dataclass(frozen=True)
class BarChart:
    name: str
    height: int
    series: list[BarSeries]
    rects: list[BarRect]
    grid: list[float]
    y_ticks: list[Tick]
    x_ticks: list[Tick]
    points: list[ChartPoint]


def bar_chart(
    series: list[BarSeries],
    labels: list[str],
    *,
    name: str,
    fmt: Format = format_number,
    height: int = 160,
    x_tick_count: int = 7,
) -> BarChart:
    """Columns per label; with several series they stack (новые + вернувшиеся)."""
    top, bottom = 8.0, float(height)
    count = len(labels)
    totals = [sum(item.values[i] for item in series) for i in range(count)]
    scale_top, steps, step_labels = _y_axis(max([*totals, 0]), fmt)
    slot = CHART_WIDTH / count if count else CHART_WIDTH
    width = slot * 0.68

    def y_of(value: float) -> float:
        return bottom - (value / scale_top) * (bottom - top)

    rects = []
    points = []
    for i in range(count):
        x = i * slot + (slot - width) / 2
        base = 0.0
        for item in series:
            if item.values[i] > 0:
                rects.append(
                    BarRect(
                        x,
                        y_of(base + item.values[i]),
                        width,
                        y_of(base) - y_of(base + item.values[i]),
                        item.tone,
                    )
                )
            base += item.values[i]
        rows = tuple((item.name, fmt(item.values[i])) for item in series)
        if len(series) > 1:
            rows += (("Всего", fmt(totals[i])),)
        points.append(ChartPoint((i + 0.5) * slot / CHART_WIDTH * 100, None, labels[i], rows))

    return BarChart(
        name=name,
        height=height,
        series=series,
        rects=rects,
        grid=[y_of(step) for step in steps],
        y_ticks=[Tick(y_of(s) / height * 100, t) for s, t in zip(steps, step_labels, strict=True)],
        x_ticks=[Tick(points[i].x, labels[i]) for i in _evenly(count, x_tick_count)],
        points=points,
    )


@dataclass(frozen=True)
class HeatCell:
    level: int  # 0 (nothing) to 4 (the busiest quarter)
    title: str


@dataclass(frozen=True)
class HeatRow:
    label: str
    cells: list[HeatCell]


@dataclass(frozen=True)
class Heatmap:
    name: str
    rows: list[HeatRow]
    columns: int
    column_ticks: list[Tick]
    peak: str | None


def heatmap(
    matrix: list[list[float]],
    row_labels: list[str],
    column_labels: list[str],
    *,
    name: str,
    fmt: Format = format_number,
    tick_every: int = 6,
) -> Heatmap:
    """A grid of cells shaded by value («Когда читают»: days x hours). Each non-zero cell
    gets a level 1-4 by its share of the busiest one; the (first) busiest is named."""
    peak_value = max((v for row in matrix for v in row), default=0)
    peak = None
    rows = []
    for row_label, values in zip(row_labels, matrix, strict=True):
        cells = []
        for column_label, value in zip(column_labels, values, strict=True):
            level = 0 if value <= 0 else max(1, math.ceil(value / peak_value * 4))
            cells.append(HeatCell(level, f"{row_label}, {column_label}: {fmt(value)}"))
            if peak is None and value > 0 and value == peak_value:
                peak = f"{row_label}, {column_label}"
        rows.append(HeatRow(row_label, cells))
    columns = len(column_labels)
    ticks = [
        Tick((i + 0.5) / columns * 100, column_labels[i]) for i in range(0, columns, tick_every)
    ]
    return Heatmap(name=name, rows=rows, columns=columns, column_ticks=ticks, peak=peak)


@dataclass(frozen=True)
class Sparkline:
    line: str
    area: str


def sparkline(values: list[float]) -> Sparkline | None:
    """The small trend line in a KPI card (viewBox 96 x 34), scaled to its own range."""
    if len(values) < 2:
        return None
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = 96 / (len(values) - 1)
    coords = [(i * step, 32 - (v - low) / span * 30) for i, v in enumerate(values)]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    return Sparkline(line=line, area=f"{line} L96,34 L0,34 Z")
