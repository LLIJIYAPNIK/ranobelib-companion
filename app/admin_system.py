"""«Система» (PR 351): the service's real state, shaped for admin/system.html. Presentation
only - the numbers come from app/system_info.py and friends.

Everything here is about this one process since it started: there's no external
monitoring, so no uptime series, no p95 over weeks - the page says «с запуска» where it
matters. No Redis, queues of thousands of jobs or terabytes either (ROADMAP.md, wave 40).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.admin_kit import format_number
from app.admin_overview import format_when

OK = "ok"
WARNING = "warning"
DOWN = "down"
TONES = {OK: "success", WARNING: "warning", DOWN: "danger"}


def format_uptime(seconds: float) -> str:
    """«3 д 4 ч», «5 ч 12 мин», «42 мин», «< 1 мин»."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "< 1 мин"
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days} д {hours} ч" if hours else f"{days} д"
    if hours:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    return f"{minutes} мин"


_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ")


def format_bytes(value: float) -> str:
    """«812 Б», «4,2 МБ», «1,3 ГБ» - binary steps, one decimal from КБ up."""
    size = float(value)
    unit = 0
    while size >= 1024 and unit < len(_UNITS) - 1:
        size /= 1024
        unit += 1
    if unit == 0:
        return f"{format_number(int(size))} {_UNITS[0]}"
    return f"{format_number(round(size, 1))} {_UNITS[unit]}"


def format_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{format_number(round(value) if value >= 10 else round(value, 1))} мс"


@dataclass(frozen=True)
class ServiceRow:
    """One line of «Сервисы»: a coloured dot, the name, its state and a detail."""

    name: str
    state: str  # OK / WARNING / DOWN
    status: str
    detail: str

    @property
    def tone(self) -> str:
        return TONES[self.state]


def app_service(started_at: datetime, uptime: float, tz: str) -> ServiceRow:
    return ServiceRow(
        "Приложение",
        OK,
        "работает",
        f"{format_uptime(uptime)} · запущено {format_when(started_at.isoformat(), tz)}",
    )


@dataclass(frozen=True)
class VersionItem:
    label: str
    value: str


def versions(app: str, sdk: str, python: str) -> list[VersionItem]:
    return [
        VersionItem("Приложение", app),
        VersionItem("ranobelib-python-sdk", sdk),
        VersionItem("Python", python),
    ]
