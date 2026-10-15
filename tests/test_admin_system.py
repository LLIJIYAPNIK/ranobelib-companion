"""PR 351: «Система» - the request metrics' ring buffer and middleware, directory and disk
measurement, the ranobelib.me probe (kept for a minute; throttled, blocked and
unreachable told apart), the page with the database or ranobelib.me down, and the cache
button that waits for ranobelib-python-sdk#78."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ranobelib import AccessBlockedError, RateLimitError, TitleNotFoundError

from app import request_metrics, system_info
from app.admin_system import (
    DOWN,
    OK,
    WARNING,
    database_service,
    disk_summary,
    format_bytes,
    format_uptime,
    latency_chart,
    ranobelib_service,
    request_kpis,
    storage_rows,
)
from app.config import get_settings
from app.db.admin_system import DatabaseStatus
from app.request_metrics import Sample, per_minute, percentile, summarize
from app.services import probe
from app.services.probe import ProbeResult
from app.system_info import DirUsage, DiskUsage, directory_usage
from tests.test_admin_auth import PASSWORD, Clock, _client, _login

NOW = datetime(2026, 3, 18, 12, tzinfo=UTC)

# --- request metrics ----------------------------------------------------------------------


def test_percentiles_are_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]

    assert (percentile(values, 0.5), percentile(values, 0.95), percentile(values, 0.99)) == (
        50.0,
        95.0,
        99.0,
    )
    assert percentile([7.0], 0.99) == 7.0
    assert percentile([], 0.5) is None


def test_the_summary_counts_5xx_and_the_window_start() -> None:
    window = [Sample(100.0, 10.0, 200), Sample(160.0, 30.0, 502), Sample(130.0, 20.0, 404)]

    stats = summarize(window)

    assert (stats.count, stats.errors, stats.since, stats.p50) == (3, 1, 100.0, 20.0)
    assert stats.error_share == pytest.approx(1 / 3)
    assert summarize([]).error_share is None


def test_p95_per_minute_for_the_last_hour() -> None:
    now = 10 * 3600 + 30.0  # half a minute into 10:00
    window = [
        Sample(now - 5, 40.0, 200),  # this minute
        Sample(now - 65, 10.0, 200),  # the one before
        Sample(now - 65, 90.0, 200),
        Sample(now - 3 * 3600, 999.0, 200),  # long gone from the hour
    ]

    buckets = per_minute(window, now)

    assert len(buckets) == 60
    assert (buckets[-1].count, buckets[-1].p95) == (1, 40.0)
    assert (buckets[-2].count, buckets[-2].p95) == (2, 90.0)
    assert sum(bucket.count for bucket in buckets) == 3
    assert buckets[0].p95 is None


def test_the_ring_buffer_keeps_only_the_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import deque

    monkeypatch.setattr(request_metrics, "_samples", deque(maxlen=3))
    for status in (200, 201, 202, 203):
        request_metrics.record(1.0, status)

    assert [s.status for s in request_metrics.samples()] == [201, 202, 203]


def test_the_middleware_times_requests_and_counts_a_crash_as_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import deque

    monkeypatch.setattr(request_metrics, "_samples", deque(maxlen=100))
    app = FastAPI()

    @app.get("/ok")
    async def ok() -> dict:
        return {}

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("boom")

    @app.get("/static/x.css")
    async def static() -> dict:
        return {}

    request_metrics.install_request_metrics(app)
    client = TestClient(app, raise_server_exceptions=False)

    client.get("/ok")
    client.get("/missing")
    client.get("/boom")
    client.get("/static/x.css")

    assert [s.status for s in request_metrics.samples()] == [200, 404, 500]
    assert all(s.ms >= 0 for s in request_metrics.samples())


# --- disk ---------------------------------------------------------------------------------


async def test_directory_usage_walks_nested_files(tmp_path: Path) -> None:
    system_info.clear_size_cache()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.json").write_bytes(b"x" * 10)
    (tmp_path / "two.json").write_bytes(b"y" * 5)

    usage = await directory_usage(tmp_path)
    (tmp_path / "three.json").write_bytes(b"z")
    cached = await directory_usage(tmp_path)  # within SIZE_TTL_SECONDS
    system_info.clear_size_cache()
    capped = await directory_usage(tmp_path, limit=2)
    missing = await directory_usage(tmp_path / "nope")

    assert usage == DirUsage(True, 2, 15, False)
    assert cached == usage
    assert capped.truncated and capped.files == 2
    assert missing == DirUsage(False, 0, 0, False)


def test_disk_usage_of_a_path_not_created_yet(tmp_path: Path) -> None:
    usage = system_info.disk_usage(tmp_path / "later" / "deeper")

    assert usage is not None and usage.total > 0 and 0 <= usage.free <= usage.total


def test_disk_thresholds_including_a_full_disk() -> None:
    gb = 1024**3

    assert disk_summary(DiskUsage(100 * gb, 50 * gb, 50 * gb)).state == OK
    assert disk_summary(DiskUsage(100 * gb, 95 * gb, 5 * gb)).state == WARNING
    full = disk_summary(DiskUsage(100 * gb, 100 * gb, 0))
    assert (full.state, full.free, full.used_share) == (DOWN, "0 Б", 100.0)
    assert disk_summary(None) is None
    assert disk_summary(DiskUsage(0, 0, 0)) is None


def test_storage_rows() -> None:
    rows = storage_rows(
        [
            ("Кеш SDK", Path("/c"), DirUsage(True, 3, 2 * 1024**2, False)),
            ("Аватарки", Path("/a"), DirUsage(True, 200_000, 1024**2, True)),
            ("Вложения", Path("/v"), DirUsage(False, 0, 0, False)),
        ]
    )

    assert [(r.size, r.files, r.share) for r in rows] == [
        ("2 МБ", "3", 100.0),
        ("≥ 1 МБ", "≥ 200 000", 50.0),
        ("нет каталога", "—", 0.0),
    ]


def test_formats() -> None:
    assert [format_uptime(s) for s in (30, 42 * 60, 5 * 3600, 3 * 86400 + 4 * 3600)] == [
        "< 1 мин",
        "42 мин",
        "5 ч",
        "3 д 4 ч",
    ]
    assert [format_bytes(b) for b in (812, 4.2 * 1024**2, 1.3 * 1024**3)] == [
        "812 Б",
        "4,2 МБ",
        "1,3 ГБ",
    ]


# --- the probe ----------------------------------------------------------------------------


class _Catalog:
    calls = 0

    def __init__(self, failure: Exception | None) -> None:
        self.failure = failure

    async def list_titles(self, **kwargs):
        _Catalog.calls += 1
        assert kwargs["refresh"] is True  # the disk cache mustn't answer for the site
        if self.failure:
            raise self.failure


def _use_catalog(monkeypatch: pytest.MonkeyPatch, failure: Exception | None) -> None:
    @asynccontextmanager
    async def fake():
        yield _Catalog(failure)

    monkeypatch.setattr(probe, "get_catalog", fake)
    probe.forget()
    _Catalog.calls = 0


@pytest.mark.parametrize(
    ("failure", "status", "answered"),
    [
        (None, probe.OK, True),
        (RateLimitError(), probe.RATE_LIMITED, True),
        (AccessBlockedError("https://ranobelib.me"), probe.BLOCKED, True),
        (TitleNotFoundError("x"), probe.ERROR, True),
        (httpx.ConnectTimeout("slow"), probe.UNREACHABLE, False),
    ],
)
async def test_the_probe_tells_outcomes_apart(
    monkeypatch: pytest.MonkeyPatch, failure: Exception | None, status: str, answered: bool
) -> None:
    _use_catalog(monkeypatch, failure)

    result = await probe.probe_ranobelib()

    assert result.status == status
    assert (result.ms is not None) == answered


async def test_the_probe_is_kept_for_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_catalog(monkeypatch, None)
    clock = [1000.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])

    first = await probe.probe_ranobelib()
    again = await probe.probe_ranobelib()
    clock[0] += probe.PROBE_TTL_SECONDS
    later = await probe.probe_ranobelib()

    assert again is first and later is not first
    assert _Catalog.calls == 2
    probe.forget()


def test_service_rows() -> None:
    slow = ranobelib_service(ProbeResult(probe.OK, 4500.0, NOW), "UTC")
    gone = ranobelib_service(ProbeResult(probe.UNREACHABLE, None, NOW), "UTC")
    down = database_service(None, (0, 0, 50))
    timed_out = database_service(None, (1, 0, 50), timed_out=True)
    db = database_service(DatabaseStatus(1.5, 8 * 1024**2, 4, "17.2"), (3, 2, 50))

    assert (slow.state, slow.status) == (WARNING, "отвечает медленно")
    assert (gone.state, gone.status) == (DOWN, "не отвечает") and "без ответа" in gone.detail
    assert (down.state, down.detail) == (DOWN, "нет соединения")
    assert timed_out.detail == "не уложилась в отведённое время"
    assert db.state == OK and "пул процесса: 3 из 50, свободно 2" in db.detail


def test_kpis_and_chart_for_no_requests_yet() -> None:
    stats = summarize([])

    kpis = request_kpis(stats, "UTC")
    chart = latency_chart(per_minute([], 3600.0), "UTC")

    assert [k.value for k in kpis] == ["—", "—", "—", "—"]
    assert kpis[0].note == "запросов ещё не было"
    assert len(chart.points) == 60


# --- the page -----------------------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    import app.auth.admin as admin_auth

    fake = Clock()
    monkeypatch.setattr(admin_auth, "_now", fake)
    monkeypatch.setattr(admin_auth, "_failures", {})
    return fake


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    import app.api.admin as admin_api

    async def reachable() -> ProbeResult:
        return ProbeResult(probe.OK, 180.0, NOW)

    monkeypatch.setattr(admin_api, "probe_ranobelib", reachable)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "entry.json").write_bytes(b"{}" * 50)
    system_info.clear_size_cache()
    with _client(
        monkeypatch, tmp_path, ADMIN_PASSWORD=PASSWORD, CACHE_DIR=str(tmp_path / "cache")
    ) as test_client:
        yield test_client
    get_settings.cache_clear()
    system_info.clear_size_cache()


def test_the_page(client: TestClient) -> None:
    _login(client)
    client.get("/admin")  # a request for the metrics to count

    page = client.get("/admin/system")

    assert page.status_code == 200
    assert "сбрасывается при деплое" in page.text
    for name in ("Приложение", "База данных", "ranobelib.me"):
        assert f'<span class="wn-admin-svc__name">{name}</span>' in page.text
    assert "ответ за 180 мс" in page.text and "PostgreSQL" in page.text
    assert system_info.sdk_version() in page.text
    assert "Задержка p95" in page.text and "Сейчас ничего не скачивается" in page.text
    assert "Кеш SDK" in page.text and "100 Б" in page.text  # the stand-in cache file
    assert 'disabled aria-describedby="sys-cache-note"' in page.text
    assert "ranobelib-python-sdk/issues/78" in page.text


def test_the_page_with_the_database_and_ranobelib_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.admin as admin_api

    async def no_database(conn):
        raise psycopg.OperationalError("connection refused")

    async def unreachable() -> ProbeResult:
        return ProbeResult(probe.UNREACHABLE, None, NOW)

    _login(client)
    monkeypatch.setattr(admin_api, "database_status", no_database)
    monkeypatch.setattr(admin_api, "probe_ranobelib", unreachable)

    page = client.get("/admin/system")

    assert page.status_code == 200
    assert "нет соединения" in page.text and "не отвечает" in page.text
    assert "История загрузок появится" in page.text


def test_the_page_needs_the_admin(client: TestClient) -> None:
    assert client.get("/admin/system", follow_redirects=False).headers["location"] == (
        "/admin/login"
    )
