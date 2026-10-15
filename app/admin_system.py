"""«Система» (PR 351): the service's real state, shaped for admin/system.html. Presentation
only - the numbers come from app/system_info.py and friends.

Everything here is about this one process since it started: there's no external
monitoring, so no uptime series, no p95 over weeks - the page says «с запуска» where it
matters. No Redis, queues of thousands of jobs or terabytes either (ROADMAP.md, wave 40).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.admin_kit import Column, LineChart, format_number, line_chart
from app.admin_overview import format_when
from app.db.admin_system import DatabaseStatus
from app.jobs.models import DownloadJob
from app.request_metrics import MinuteBucket, RequestStats
from app.services import probe
from app.services.probe import ProbeResult
from app.system_info import DirUsage, DiskUsage

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


# --- requests (since the process started) --------------------------------------------------


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    icon: str
    note: str | None = None


def _clock(timestamp: float, tz: str) -> str:
    return f"{datetime.fromtimestamp(timestamp, ZoneInfo(tz)):%H:%M}"


def request_kpis(stats: RequestStats, tz: str) -> list[Kpi]:
    since = f"с {_clock(stats.since, tz)}" if stats.since is not None else "запросов ещё не было"
    share = stats.error_share
    return [
        Kpi("Задержка p50", format_ms(stats.p50), "clock", since),
        Kpi("Задержка p95", format_ms(stats.p95), "clock", since),
        Kpi("Задержка p99", format_ms(stats.p99), "clock", since),
        Kpi(
            "Ответы 5xx",
            "—" if share is None else f"{format_number(round(share * 100, 2))}%",
            "triangle-alert",
            f"{format_number(stats.errors)} из {format_number(stats.count)}",
        ),
    ]


def latency_chart(buckets: list[MinuteBucket], tz: str) -> LineChart:
    """p95 per minute over the last hour - one metric on the chart; a minute without
    requests sits at zero (the subtitle says so)."""
    return line_chart(
        [bucket.p95 or 0.0 for bucket in buckets],
        [_clock(bucket.start, tz) for bucket in buckets],
        name="p95",
        fmt=format_ms,
        height=200,
    )


# --- downloads and disk --------------------------------------------------------------------

JOB_STATUS_LABELS = {
    "queued": "в очереди",
    "running": "скачивается",
    "exporting": "собирается файл",
    "needs_translation": "ждёт выбора перевода",
}

JOB_COLUMNS = (
    Column("slug_url", "Тайтл", "mono", sortable=False),
    Column("fmt", "Формат", sortable=False),
    Column("status", "Состояние", sortable=False),
    Column("progress", "Главы", "num", sortable=False),
    Column("started", "Идёт", "muted", sortable=False),
)


def job_rows(jobs: list[DownloadJob], now: float) -> list[dict[str, str]]:
    """Active downloads, longest-running first."""
    ordered = sorted(jobs, key=lambda job: job.started_at or now)
    return [
        {
            "slug_url": job.slug_url,
            "fmt": job.fmt,
            "status": JOB_STATUS_LABELS.get(job.status, job.status),
            "progress": f"{job.completed} из {job.total}" if job.total else "—",
            "started": format_uptime(now - job.started_at) if job.started_at else "ещё не начата",
        }
        for job in ordered
    ]


@dataclass(frozen=True)
class StorageRow:
    label: str
    path: str
    size: str
    files: str
    share: float  # of the largest directory, 0-100 - the bar's width


def storage_rows(usages: list[tuple[str, Path, DirUsage]]) -> list[StorageRow]:
    largest = max((usage.bytes for _, _, usage in usages), default=0) or 1
    rows = []
    for label, path, usage in usages:
        if not usage.exists:
            size, files = "нет каталога", "—"
        else:
            more = "≥ " if usage.truncated else ""
            size = more + format_bytes(usage.bytes)
            files = more + format_number(usage.files)
        rows.append(StorageRow(label, str(path), size, files, usage.bytes / largest * 100))
    return rows


@dataclass(frozen=True)
class DiskSummary:
    free: str
    total: str
    used_share: float  # 0-100
    state: str  # OK / WARNING / DOWN

    @property
    def tone(self) -> str:
        return TONES[self.state]


# Below these the disk turns amber, then red.
LOW_DISK_SHARE = 0.10
CRITICAL_DISK_SHARE = 0.03


def disk_summary(usage: DiskUsage | None) -> DiskSummary | None:
    if usage is None or usage.total <= 0:
        return None
    free_share = usage.free / usage.total
    state = OK
    if free_share < CRITICAL_DISK_SHARE:
        state = DOWN
    elif free_share < LOW_DISK_SHARE:
        state = WARNING
    return DiskSummary(
        free=format_bytes(usage.free),
        total=format_bytes(usage.total),
        used_share=(usage.total - usage.free) / usage.total * 100,
        state=state,
    )


# TODO(https://github.com/LLIJIYAPNIK/ranobelib-python-sdk/issues/78): «Очистить кеш SDK»
# stays disabled until the SDK has a public way to clear its cache - its file layout is the
# SDK's business, so the panel doesn't delete those files itself. Once it ships: a POST
# behind a confirmation dialog, recorded in the action log, like the other admin actions.
SDK_CACHE_CLEAR_ISSUE = "https://github.com/LLIJIYAPNIK/ranobelib-python-sdk/issues/78"
