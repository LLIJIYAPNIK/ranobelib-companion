"""What the admin panel's «Система» (PR 351) says about this process: when it started and
which versions it runs."""

from __future__ import annotations

import importlib.metadata
import platform
import tomllib
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

STARTED_AT = datetime.now(UTC)

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
