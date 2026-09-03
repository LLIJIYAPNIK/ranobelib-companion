"""app/db/connection.py - see PR 67 in CLAUDE.md's roadmap: a single process-wide
sqlite3.Connection shared across threads used to crash under concurrent access (the
"Загрузки" page's /downloads/status and /downloads/ready polling, PR 17/50/56, hitting it
from FastAPI's thread pool at the same time another thread was using it directly). PR 191
moved the underlying store to Postgres (see app/db/connection.py's own docstring) - the
per-thread-connection design this file tests didn't change, only what backs each one."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from app.config import get_settings
from app.db.connection import get_connection
from app.db.migrate import run_migrations
from tests.db_reset import reset_app_database


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_app_database(monkeypatch)
    get_settings.cache_clear()
    run_migrations(get_connection())
    get_connection().execute(
        "INSERT INTO users (id, email, password_hash, created_at) "
        "VALUES (1, 'alice@example.com', 'hash', 'now')"
    )

    yield

    get_settings.cache_clear()


def test_get_connection_reuses_the_same_object_within_a_thread(db: None) -> None:
    assert get_connection() is get_connection()


def test_get_connection_gives_each_thread_its_own_object(db: None) -> None:
    other_thread_connection: list[object] = []
    thread = threading.Thread(target=lambda: other_thread_connection.append(get_connection()))
    thread.start()
    thread.join()

    assert other_thread_connection[0] is not get_connection()


def test_get_connection_survives_concurrent_use_from_many_threads(db: None) -> None:
    """Reproduces the bug report under load: mostly-reader threads (standing in for
    get_current_user() resolved in FastAPI's thread pool on almost every page, plus
    /downloads/status and /downloads/ready polling, which are themselves reads) alongside
    a couple of writer threads (an occasional real write - recording a download, adding a
    library entry). Against the old single-shared-Connection implementation, this
    reliably raised a mix of sqlite3 errors within a couple of seconds - not the "bad
    parameter or other API misuse" from the original traceback verbatim every time, but
    the same root cause (two threads calling .execute()/.commit() on the same Connection
    object at once corrupts its internal state), surfacing as whatever garbled error that
    corruption happened to produce. Per-thread connections (this PR's fix) make that a
    structural non-issue rather than a timing-dependent one - 5 consecutive runs of this
    exact scenario passed clean against the fix while 5/5 runs failed against the old
    code, so the reader:writer ratio here is deliberately kept read-heavy (matching the
    real traffic pattern) rather than write-heavy, which would instead spuriously fail on
    SQLite's own unrelated single-writer-per-file lock timeout - a real constraint, but
    not the bug this test is about."""
    READER_COUNT = 20
    WRITER_COUNT = 2
    ITERATIONS_PER_THREAD = 200
    errors: list[Exception] = []

    def read() -> None:
        try:
            conn = get_connection()
            for _ in range(ITERATIONS_PER_THREAD):
                conn.execute("SELECT * FROM users WHERE id = 1").fetchone()
        except Exception as exc:
            errors.append(exc)

    def write() -> None:
        try:
            conn = get_connection()
            for _ in range(ITERATIONS_PER_THREAD):
                conn.execute(
                    "INSERT INTO download_history "
                    "(user_id, slug_url, fmt, status, chapter_count, error, finished_at) "
                    "VALUES (1, 'x', 'epub', 'done', 1, NULL, 'now')"
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(READER_COUNT)]
    threads += [threading.Thread(target=write) for _ in range(WRITER_COUNT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
