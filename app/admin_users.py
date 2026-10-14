"""«Пользователи» (PR 348): the list's columns, filters and rows and the drawer's summary,
shaped for admin/users.html from app/db/admin_users.py. Presentation only.

No «Роль» column and no «Пригласить»: the admin is one password from .env, not an account
with roles. No account deletion either - the site has no such function, and it isn't the
panel's to invent (ROADMAP.md, wave 40).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

from app.admin_kit import Column, Filter, format_number
from app.admin_overview import PERIOD_LABELS, format_duration, format_when
from app.db.admin_users import UserRow, UserSummary

YES_NO = {"yes": True, "no": False}


def parse_flag(raw: str | None) -> bool | None:
    """«Все» (None), «да» (True) or «нет» (False) from a filter's query value."""
    return YES_NO.get(raw or "")


def user_columns(period_key: str) -> tuple[Column, ...]:
    return (
        Column("id", "id", "mono", locked=True),
        Column("email", "Email", locked=True),
        Column("nickname", "Никнейм"),
        Column("created_at", "Регистрация", "muted"),
        Column("verified", "Email подтверждён", sortable=False),
        Column("last_active", "Последняя активность", "muted"),
        Column("library", "В библиотеке", "num"),
        Column("time", f"Чтение за {PERIOD_LABELS[period_key]}", "num"),
        Column("actions", "Действия", "actions", sortable=False),
    )


def user_filters(args: Mapping[str, object], period_key: str) -> tuple[Filter, ...]:
    def value(name: str) -> str:
        raw = str(args.get(name) or "")
        return raw if raw in YES_NO else ""

    return (
        Filter(
            "verified",
            "Email",
            (("yes", "подтверждён"), ("no", "не подтверждён")),
            value("verified"),
        ),
        Filter(
            "active",
            f"Активен за {PERIOD_LABELS[period_key]}",
            (("yes", "да"), ("no", "нет")),
            value("active"),
        ),
        Filter("library", "Библиотека", (("yes", "есть"), ("no", "пустая")), value("library")),
    )


@dataclass(frozen=True)
class UserListRow:
    id: int
    email: str
    nickname: str | None
    created_at: str | None
    verified: bool
    last_active: str | None
    library: str
    time: str
    open_href: str


def user_rows(rows: list[UserRow], tz: str, args: Mapping[str, object]) -> list[UserListRow]:
    return [
        UserListRow(
            id=row.id,
            email=row.email,
            nickname=row.nickname,
            created_at=format_when(row.created_at, tz),
            verified=row.email_verified_at is not None,
            last_active=format_when(row.last_active, tz),
            library=format_number(row.library),
            time=format_duration(row.seconds) if row.seconds else "—",
            open_href=user_href(args, row.id),
        )
        for row in rows
    ]


def user_href(args: Mapping[str, object], user_id: int) -> str:
    """The list as it is (search, filters, sort, page) with this user's drawer open."""
    kept = {key: value for key, value in args.items() if key != "user" and value}
    return "/admin/users?" + urlencode({**kept, "user": user_id})


def back_href(args: Mapping[str, object]) -> str:
    """Where an action returns to: the same list with the same drawer open."""
    query = urlencode({key: value for key, value in args.items() if value})
    return "/admin/users" + (f"?{query}" if query else "")


def safe_back(raw: str | None) -> str:
    """An action's «back» field may only lead to the users list - never another page or
    site (no open redirect through the form)."""
    if raw and (raw == "/admin/users" or raw.startswith("/admin/users?")) and "//" not in raw:
        return raw
    return "/admin/users"


@dataclass(frozen=True)
class SummaryItem:
    label: str
    value: str


def summary_items(summary: UserSummary, tz: str, period_key: str) -> list[SummaryItem]:
    period = PERIOD_LABELS[period_key]
    return [
        SummaryItem("Зарегистрирован", format_when(summary.created_at, tz) or "—"),
        SummaryItem(
            "Email подтверждён",
            format_when(summary.email_verified_at, tz) if summary.email_verified_at else "нет",
        ),
        SummaryItem("Последняя активность", format_when(summary.last_active, tz) or "ни разу"),
        SummaryItem("Часовой пояс", summary.timezone or "не задан (UTC)"),
        SummaryItem("В библиотеке", format_number(summary.library)),
        SummaryItem("Прочитано глав", format_number(summary.chapters)),
        SummaryItem(f"Глав за {period}", format_number(summary.chapters_period)),
        SummaryItem("Время чтения", format_duration(summary.seconds)),
        SummaryItem(f"Чтение за {period}", format_duration(summary.seconds_period)),
        SummaryItem("Загрузок", format_number(summary.downloads)),
        SummaryItem("Комментариев", format_number(summary.comments)),
    ]
