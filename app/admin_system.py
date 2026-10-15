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
from app.db.admin_system import DatabaseStatus
from app.services import probe
from app.services.probe import ProbeResult

OK = "ok"
WARNING = "warning"
DOWN = "down"
TONES = {OK: "success", WARNING: "warning", DOWN: "danger"}

# Past these the row turns «медленно» - a hint, not an alert.
SLOW_DB_MS = 100.0
SLOW_SOURCE_MS = 3000.0


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


def database_service(
    status: DatabaseStatus | None, pool: tuple[int, int, int], timed_out: bool = False
) -> ServiceRow:
    """«База данных»: down when no connection could be had, slow-ish past SLOW_DB_MS."""
    if status is None:
        reason = "не уложилась в отведённое время" if timed_out else "нет соединения"
        return ServiceRow("База данных", DOWN, "недоступна", reason)
    in_pool, idle, max_size = pool
    state = WARNING if status.ping_ms >= SLOW_DB_MS else OK
    return ServiceRow(
        "База данных",
        state,
        "отвечает медленно" if state == WARNING else "работает",
        f"PostgreSQL {status.server_version} · пинг {format_ms(status.ping_ms)} · "
        f"{format_bytes(status.size_bytes)} · соединений к базе: "
        f"{format_number(status.connections)} (пул процесса: {in_pool} из {max_size}, "
        f"свободно {idle})",
    )


_PROBE_STATES = {
    probe.OK: (OK, "отвечает"),
    probe.RATE_LIMITED: (WARNING, "ограничивает запросы"),
    probe.BLOCKED: (DOWN, "блокирует запросы"),
    probe.UNREACHABLE: (DOWN, "не отвечает"),
    probe.ERROR: (WARNING, "отвечает с ошибкой"),
}


def ranobelib_service(result: ProbeResult, tz: str) -> ServiceRow:
    """«ranobelib.me»: the probe's outcome and time; slow past SLOW_SOURCE_MS."""
    state, status = _PROBE_STATES[result.status]
    if result.status == probe.OK and result.ms is not None and result.ms >= SLOW_SOURCE_MS:
        state, status = WARNING, "отвечает медленно"
    checked = format_when(result.checked_at.isoformat(), tz)
    answer = f"ответ за {format_ms(result.ms)}" if result.ms is not None else "без ответа"
    return ServiceRow(
        "ranobelib.me",
        state,
        status,
        f"{answer} · проверено {checked} · не чаще раза в минуту",
    )
