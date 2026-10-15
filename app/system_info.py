"""What the admin panel's «Система» (PR 351) says about this process and its disk: when it
started, which versions it runs, how much space the app's directories take and how much
is left.

Directory sizes are measured as plain directories (os.scandir) - the SDK's ``cache_dir``
included, without assuming anything about how the SDK lays its files out. Walking a big
cache is blocking work, so it runs in a thread and each result is kept for
SIZE_TTL_SECONDS; a walk stops at MAX_FILES and says so rather than tying up the page.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import platform
import shutil
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

STARTED_AT = datetime.now(UTC)

SIZE_TTL_SECONDS = 60.0
MAX_FILES = 200_000

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@cache
def app_version() -> str:
    """The version in pyproject.toml - the app isn't installed as a package, so there's no
    distribution metadata to ask."""
    try:
        return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "?"


@cache
def sdk_version() -> str:
    try:
        return importlib.metadata.version("ranobelib-python-sdk")
    except importlib.metadata.PackageNotFoundError:
        return "?"


def python_version() -> str:
    return platform.python_version()


def uptime_seconds(now: datetime | None = None) -> float:
    return ((now or datetime.now(UTC)) - STARTED_AT).total_seconds()


@dataclass(frozen=True)
class DirUsage:
    exists: bool
    files: int
    bytes: int
    truncated: bool  # stopped at MAX_FILES - the real numbers are larger


def _walk(path: Path, limit: int) -> DirUsage:
    if not path.is_dir():
        return DirUsage(False, 0, 0, False)
    files = size = 0
    stack = [path]
    while stack:
        try:
            entries = os.scandir(stack.pop())
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        files += 1
                        size += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                if files >= limit:
                    return DirUsage(True, files, size, True)
    return DirUsage(True, files, size, False)


_sizes: dict[Path, tuple[float, DirUsage]] = {}


async def directory_usage(path: Path, *, limit: int = MAX_FILES) -> DirUsage:
    """Files and bytes under `path`, measured at most once per SIZE_TTL_SECONDS."""
    key = path.resolve()
    cached = _sizes.get(key)
    if cached and time.monotonic() - cached[0] < SIZE_TTL_SECONDS:
        return cached[1]
    usage = await asyncio.to_thread(_walk, key, limit)
    _sizes[key] = (time.monotonic(), usage)
    return usage


@dataclass(frozen=True)
class DiskUsage:
    total: int
    used: int
    free: int


def disk_usage(path: Path) -> DiskUsage | None:
    """The disk `path` lives on (its nearest existing parent, if it isn't there yet)."""
    probe = path.resolve()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return None
    return DiskUsage(usage.total, usage.used, usage.free)


def clear_size_cache() -> None:
    _sizes.clear()
