"""PR 342: the admin UI kit - the helpers in app/admin_kit.py, the macros in
app/templates/admin/_kit.html, the toast flow, and the dev-only /admin/_kit showcase."""

import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import admin_kit as kit
from app.admin_kit import BarSeries, Column, Filter
from app.config import get_settings
from app.templating import templates
from tests.test_admin_auth import PASSWORD, Clock, _client, _login
from tests.test_admin_shell import _references


def _render(source: str, **context: object) -> str:
    template = templates.env.from_string(
        '{% import "admin/_kit.html" as kit with context %}' + source
    )
    context.setdefault("csrf_token", "TOKEN")
    context.setdefault(
        "request", SimpleNamespace(url=SimpleNamespace(path="/admin/x", query="page=2"))
    )
    return template.render(**context)


COLUMNS = [
    Column("name", "Имя", locked=True),
    Column("id", "ID", "mono"),
    Column("status", "Статус", sortable=False),
    Column("note", "Заметка", "muted", sortable=False, hidden=True),
    Column("actions", "Действия", "actions", sortable=False),
]
ROWS = [{"name": "aster", "id": 7, "status": "ok", "note": None, "actions": ""}]


# --- helpers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "pages", "expected"),
    [
        (1, 1, [1]),
        (1, 3, [1, 2, 3]),
        (5, 20, [1, None, 4, 5, 6, None, 20]),
        (3, 20, [1, 2, 3, 4, None, 20]),  # a gap of one page is shown, not skipped
        (20, 20, [1, None, 19, 20]),
    ],
)
def test_page_window(page: int, pages: int, expected: list) -> None:
    assert kit.page_window(page, pages) == expected


def test_page_range() -> None:
    assert kit.page_range(3, 25, 340) == "51–75 из 340"
    assert kit.page_range(14, 25, 340) == "326–340 из 340"
    assert kit.page_range(1, 25, 0) == "0 записей"


def test_admin_query_keeps_the_state_and_drops_empty_values() -> None:
    args = {"q": "abc", "status": "", "sort": "id", "page": "3"}

    assert kit.admin_query(args, page=None) == "?q=abc&sort=id"
    assert kit.admin_query(args, sort="name", order="asc") == "?q=abc&sort=name&page=3&order=asc"
    assert kit.admin_query({}, q="") == "?"


def test_filter_state_has_a_chip_per_condition_and_a_reset_that_keeps_the_sort() -> None:
    status = Filter("status", "Статус", (("blocked", "Заблокирован"),), "blocked")
    args = {"q": "ab", "status": "blocked", "sort": "id", "order": "asc", "page": "4"}

    state = kit.filter_state(args, [status])

    assert [chip.label for chip in state.chips] == ["«ab»", "Статус: Заблокирован"]
    assert state.chips[0].remove == "?status=blocked&sort=id&order=asc"
    assert state.chips[1].remove == "?q=ab&sort=id&order=asc"
    assert state.count == 2
    assert state.reset == "?sort=id&order=asc"
    assert kit.filter_state({"sort": "id"}, [Filter("status", "Статус", ())]).count == 0


def test_format_number_and_delta() -> None:
    assert kit.format_number(12480) == "12 480"
    assert kit.format_number(3.25) == "3,2"
    assert kit.delta(1240, 1100) == kit.Delta("+12,7%", "up", True)
    assert kit.delta(90, 100) == kit.Delta("−10%", "down", False)
    errors = kit.delta(7, 4, higher_is_better=False)
    assert (errors.text, errors.tone, errors.icon) == ("+75%", "danger", "trending-up")
    assert kit.delta(5, 0).text == "новое"
    assert kit.delta(0, 0).tone == "neutral"
    assert kit.delta(5, None) is None


# --- charts -------------------------------------------------------------------------------


def test_line_chart_puts_both_periods_on_one_round_scale() -> None:
    chart = kit.line_chart(
        [3, 7, 5, 12, 9], ["a", "b", "c", "d", "e"], name="Читатели", previous=[2, 4, 6, 8, 7]
    )

    # 12 at most -> steps of 5 up to 15; the baseline at 262 of 270, the top at 12.
    assert [tick.text for tick in chart.y_ticks] == ["0", "5", "10", "15"]
    assert chart.line.startswith("M0.0,") and " L1000.0," in chart.line
    assert chart.area.endswith("L1000.0,262.0 L0.0,262.0 Z")
    assert chart.previous is not None and chart.previous.startswith("M0.0,")
    assert chart.points[3].rows == (("Читатели", "12"), ("Прошлый период", "8"))
    assert chart.points[3].y == pytest.approx((262 - 12 / 15 * 250) / 270 * 100)
    assert [tick.text for tick in chart.x_ticks] == ["a", "b", "c", "d", "e"]


def test_line_chart_with_nothing_to_draw() -> None:
    chart = kit.line_chart([0, 0], ["a", "b"], name="x")

    assert [tick.text for tick in chart.y_ticks] == ["0", "1", "2", "3"]
    assert chart.previous is None


def test_bar_chart_stacks_its_series() -> None:
    chart = kit.bar_chart(
        [BarSeries("Новые", [1, 0], "warning"), BarSeries("Вернувшиеся", [3, 2])],
        ["пн", "вт"],
        name="НиВ",
        height=160,
    )

    first = [rect for rect in chart.rects if rect.x < 500]
    assert [rect.tone for rect in first] == ["warning", "primary"]
    # The second segment sits right on top of the first.
    assert first[1].y + first[1].height == pytest.approx(first[0].y)
    assert len(chart.rects) == 3  # no rect for a zero
    assert chart.points[0].rows == (("Новые", "1"), ("Вернувшиеся", "3"), ("Всего", "4"))


def test_heatmap_levels_and_peak() -> None:
    chart = kit.heatmap(
        [[0, 1, 8], [4, 8, 2]], ["Пн", "Вт"], ["0:00", "1:00", "2:00"], name="Когда"
    )

    assert [cell.level for cell in chart.rows[0].cells] == [0, 1, 4]
    assert [cell.level for cell in chart.rows[1].cells] == [2, 4, 1]
    assert chart.rows[0].cells[2].title == "Пн, 2:00: 8"
    assert chart.peak == "Пн, 2:00"
    assert kit.heatmap([[0]], ["Пн"], ["0:00"], name="x").peak is None


def test_sparkline() -> None:
    line = kit.sparkline([1, 3, 2])
    assert line is not None and line.line == "M0.0,32.0 L48.0,2.0 L96.0,17.0"
    assert kit.sparkline([5]) is None


def test_chart_macros_render_svg_and_a_table_for_screen_readers() -> None:
    line = kit.line_chart([3, 7], ["a", "b"], name="Читатели", previous=[2, 4])
    bars = kit.bar_chart([BarSeries("Новые", [1, 2])], ["a", "b"], name="Новые")
    heat = kit.heatmap([[0, 1]], ["Пн"], ["0:00", "1:00"], name="Когда", tick_every=1)

    html = _render(
        "{{ kit.line_chart(line) }}{{ kit.bar_chart(bars) }}{{ kit.heatmap(heat) }}",
        line=line,
        bars=bars,
        heat=heat,
    )

    assert html.count('<svg class="wn-admin-chart__svg"') == 2
    assert 'class="wn-admin-chart__line wn-admin-chart__line--previous"' in html
    assert html.count('class="wn-admin-chart__bar wn-admin-chart__bar--primary"') == 2
    assert html.count("data-kit-point") == 4
    assert '<div class="wn-admin-sr">' in html and "<caption>Читатели</caption>" in html
    assert 'class="wn-admin-heat__cell wn-admin-heat__cell--4" title="Пн, 1:00: 1"' in html


def test_kpi_card() -> None:
    html = _render(
        '{{ kit.kpi("Читатели", "1 240", "users", change=change, previous="было 1 100",'
        " spark=spark) }}",
        change=kit.delta(1240, 1100),
        spark=kit.sparkline([1, 2, 3]),
    )

    assert '<p class="wn-admin-kpi__value">1 240</p>' in html
    assert 'class="wn-admin-delta wn-admin-delta--success"' in html
    assert "#admin-icon-trending-up" in html and "+12,7%" in html
    assert 'class="wn-admin-spark"' in html


# --- table, filters, pager ----------------------------------------------------------------


def test_table_sort_headers_link_to_the_other_direction() -> None:
    html = _render(
        '{{ kit.table("t", columns, rows, sort="id", descending=true, args=args) }}',
        columns=COLUMNS,
        rows=ROWS,
        args={"q": "a", "sort": "id", "order": "desc", "page": "3"},
    )

    assert re.search(
        r'data-col="id" aria-sort="descending"><a [^>]*href="\?q=a&amp;sort=id&amp;order=asc"', html
    )
    assert 'href="?q=a&amp;sort=name&amp;order=desc"' in html  # page dropped
    assert (
        '<th class="wn-admin-table__th wn-admin-table__th--text" scope="col" '
        'data-col="status">Статус</th>' in html
    )
    assert '<span class="wn-admin-sr">Действия</span>' in html


def test_table_cells_carry_their_column_for_the_phone_cards_and_column_menu() -> None:
    html = _render('{{ kit.table("t", columns, rows) }}', columns=COLUMNS, rows=ROWS)

    assert 'data-col="id" data-label="ID">7</td>' in html
    assert (
        'data-col="note" data-label="Заметка" hidden><span class="wn-admin-table__null">—</span>'
        in html
    )


def test_table_caller_renders_cells() -> None:
    html = _render(
        "{% call(row, column) kit.table('t', columns, rows) %}"
        "{% if column.key == 'status' %}{{ kit.badge(row.status, 'success') }}"
        "{% else %}{{ row[column.key] }}{% endif %}{% endcall %}",
        columns=COLUMNS,
        rows=ROWS,
    )

    assert '<span class="wn-admin-badge wn-admin-badge--success">ok</span>' in html


def test_columns_menu() -> None:
    html = _render('{{ kit.columns_menu("t", columns) }}', columns=COLUMNS)

    assert '<details class="wn-admin-colmenu" data-kit-columns="t" hidden>' in html
    assert "<span data-kit-columns-count>3</span>" in html  # visible, actions not counted
    assert 'value="name" checked disabled data-locked' in html
    assert 'value="note">' in html
    assert 'value="actions"' not in html


def test_filter_bar() -> None:
    status = Filter("status", "Статус", (("a", "Активен"), ("b", "Заблокирован")), "b")
    html = _render(
        "{% call kit.filter_bar('/admin/x', args, filters) %}EXTRA{% endcall %}",
        args={"q": "zz", "status": "b", "sort": "id", "order": "asc"},
        filters=[status],
    )

    assert (
        '<form class="wn-admin-filters" method="get" action="/admin/x" role="search" '
        "data-kit-filters>" in html
    )
    assert 'name="q" value="zz"' in html
    assert '<option value="b" selected>Заблокирован</option>' in html
    assert '<input type="hidden" name="sort" value="id">' in html
    assert '<div class="wn-admin-filters__extra">EXTRA</div>' in html
    assert html.count('class="wn-admin-chip"') == 2
    assert (
        '<a class="wn-admin-filters__reset" href="?sort=id&amp;order=asc">Сбросить (2)</a>' in html
    )


def test_filter_bar_without_conditions_has_no_chips() -> None:
    html = _render("{{ kit.filter_bar('/admin/x', {}, []) }}")

    assert "wn-admin-chip" not in html and "Сбросить" not in html


def test_pager() -> None:
    html = _render("{{ kit.pager(2, 9, 85, 10, {'q': 'a', 'page': '2'}) }}")

    assert '<span class="wn-admin-pager__info">11–20 из 85</span>' in html
    assert 'href="?q=a" aria-label="Предыдущая страница"' in html  # page 1 has no ?page
    assert 'href="?q=a&amp;page=2" aria-current="page"' in html
    assert 'aria-label="Следующая страница"' in html and "&amp;page=3" in html
    assert html.count("…") == 1
    first = _render("{{ kit.pager(1, 3, 25, 10) }}")
    assert '<span class="wn-admin-pager__step" aria-disabled="true">' in first
    assert "wn-admin-pager__pages" not in _render("{{ kit.pager(1, 1, 4, 10) }}")


# --- drawer, dialog, toast, states --------------------------------------------------------


def test_confirm_for_an_irreversible_action() -> None:
    html = _render(
        "{{ kit.confirm('c', 'Удалить?', '/admin/x/delete', lines=['Пропадёт всё'],"
        " irreversible=true, fields={'id': 5}) }}"
    )

    assert (
        '<dialog class="wn-admin-dialog" id="c" aria-labelledby="c-title" data-kit-dialog>' in html
    )
    assert (
        '<form class="wn-admin-dialog__form" method="post" action="/admin/x/delete" '
        "data-kit-confirm>" in html
    )
    assert '<input type="hidden" name="csrf" value="TOKEN">' in html
    assert '<input type="hidden" name="id" value="5">' in html
    assert 'name="confirm" value="1" required data-kit-ack' in html
    assert "Я понимаю, что это действие необратимо" in html
    assert 'type="submit" data-kit-ack-submit>' in html
    assert "Пропадёт всё" in html


def test_a_plain_confirm_has_no_acknowledgement() -> None:
    html = _render("{{ kit.confirm('c', 'Сохранить?', '/x', confirm_label='Да', tone='primary') }}")

    assert "data-kit-ack" not in html
    assert "wn-admin-btn--primary" in html and "wn-admin-btn--danger" not in html


def test_drawer_and_field() -> None:
    html = _render(
        "{% call kit.drawer('d', 'Пользователь', 'id 5') %}"
        "{% call kit.field('Email', 'f-email', error='Занят', required=true) %}"
        "<input id='f-email'>{% endcall %}"
        "{{ kit.drawer_foot() }}{% endcall %}"
    )

    assert (
        '<dialog class="wn-admin-drawer" id="d" aria-labelledby="d-title" data-kit-dialog>' in html
    )
    assert 'data-kit-close aria-label="Закрыть"' in html
    assert '<p class="wn-admin-field__error" id="f-email-error">' in html
    assert '<span class="wn-admin-drawer__state" data-kit-dirty>Изменений нет</span>' in html


def test_toast_with_undo() -> None:
    item = kit.Toast("Скрыт", undo=kit.Undo("/admin/undo", {"id": "5"}))

    html = _render("{{ kit.toast(item) }}", item=item)

    assert '<form class="wn-admin-toast__undo" method="post" action="/admin/undo">' in html
    assert '<input type="hidden" name="id" value="5">' in html
    assert "Отменить" in html
    assert "Отменить" not in _render("{{ kit.toast(item) }}", item=kit.Toast("Готово"))


def test_toast_session_round_trip() -> None:
    request = SimpleNamespace(session={})

    kit.push_toast(request, "Удалено", undo=kit.Undo("/u", {"id": "1"}))
    assert kit.pop_toast(request) == kit.Toast("Удалено", "success", kit.Undo("/u", {"id": "1"}))
    assert kit.pop_toast(request) is None  # shown once
    request.session[kit.TOAST_SESSION_KEY] = {"message": "x", "tone": "weird", "undo": "junk"}
    assert kit.pop_toast(request) == kit.Toast("x")


def test_states() -> None:
    html = _render(
        "{{ kit.skeleton(2, 3) }}{{ kit.error_state() }}"
        "{% call kit.empty_state('Пусто') %}<a href='/add'>Добавить</a>{% endcall %}"
        "{{ kit.no_results(2, '?sort=id') }}"
    )

    assert html.count('class="wn-admin-skeleton__row"') == 2
    assert 'role="status" aria-busy="true"' in html
    assert 'role="alert"' in html
    assert 'href="/admin/x?page=2"' in html and "Повторить" in html  # retries this page
    assert "<a href='/add'>Добавить</a>" in html
    assert "(2)" in html and 'href="?sort=id">Сбросить фильтры</a>' in html


# --- the showcase and the toast flow through the app --------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    with _client(monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD) as client:
        assert _login(client).status_code == 303
        yield client
    get_settings.cache_clear()


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_the_showcase_renders_every_part(admin: TestClient) -> None:
    response = admin.get("/admin/_kit?status=blocked&sort=chapters&order=asc&page=1")

    assert response.status_code == 200
    html = response.text
    for marker in (
        "data-kit-filters",
        "data-kit-columns",
        'data-kit-table="kit-users"',
        "wn-admin-pager",
        'id="kit-drawer"',
        'id="kit-confirm"',
        "wn-admin-skeleton",
        "wn-admin-state--error",
        "wn-admin-chart--line",
        "wn-admin-chart--bars",
        "wn-admin-heat",
        "wn-admin-kpi",
    ):
        assert marker in html, marker
    statuses = re.findall(
        r'data-col="status" data-label="Статус"><span class="wn-admin-badge[^"]*">([^<]+)', html
    )
    assert statuses and set(statuses) == {"Заблокирован"}
    # Nothing from outside the site, no inline script (the CSP would block it).
    for _tag, url in _references(html):
        assert not url.startswith("http") or "fonts.g" in url or "testserver" in url, url
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert 'aria-current="page"' not in re.search(r"<aside.*?</aside>", html, re.S).group(0)


def test_the_toast_flow(admin: TestClient) -> None:
    token = _csrf(admin.get("/admin/_kit").text)

    unconfirmed = admin.post(
        "/admin/_kit/demo", data={"csrf": token, "action": "delete", "name": "aster1"}
    )
    assert "Не удалено" in unconfirmed.text and "wn-admin-toast--error" in unconfirmed.text

    deleted = admin.post(
        "/admin/_kit/demo",
        data={"csrf": token, "action": "delete", "confirm": "1", "name": "aster1"},
        follow_redirects=False,
    )
    assert deleted.status_code == 303 and deleted.headers["location"] == "/admin/_kit"
    page = admin.get("/admin/_kit").text
    toast = re.search(r'<div class="wn-admin-toasts".*?</div>\s*</div>', page, re.S).group(0)
    assert "«aster1» удалён" in toast
    assert '<input type="hidden" name="action" value="undo">' in toast
    assert "data-kit-toast" not in admin.get("/admin").text.split("data-kit-toasts", 1)[1]  # once

    undone = admin.post(
        "/admin/_kit/demo", data={"csrf": token, "action": "undo", "name": "aster1"}
    )
    assert "удаление «aster1» отменено" in undone.text
    assert admin.post("/admin/_kit/demo", data={"csrf": "wrong"}).status_code == 403


def test_the_showcase_is_404_in_production_even_for_the_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        ADMIN_PASSWORD=PASSWORD,
        ENVIRONMENT="production",
        SESSION_SECRET_KEY="s",
    ) as client:
        assert _login(client).status_code == 303
        assert client.get("/admin").status_code == 200
        assert client.get("/admin/_kit").status_code == 404
        token = _csrf(client.get("/admin").text)
        assert client.post("/admin/_kit/demo", data={"csrf": token}).status_code == 404
    get_settings.cache_clear()
