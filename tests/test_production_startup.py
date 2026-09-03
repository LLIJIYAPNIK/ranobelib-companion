"""PR 187: the app must actually refuse to start in production without an explicit
SESSION_SECRET_KEY - not just have get_settings() raise in isolation. app.main calls
get_settings() while building the ASGI app (RememberMeSessionMiddleware's secret_key),
so importing it is enough to trigger the failure - run in a subprocess since app.main is
already imported (and cached in sys.modules) by every other test module in this suite."""

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_app_main(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("ENVIRONMENT", None)
    env.pop("SESSION_SECRET_KEY", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
    )


def test_app_fails_to_start_in_production_without_session_secret_key() -> None:
    result = _import_app_main({"ENVIRONMENT": "production"})

    assert result.returncode != 0
    assert "SESSION_SECRET_KEY" in result.stderr
    assert "RuntimeError" in result.stderr


def test_app_starts_without_session_secret_key_when_environment_unset(tmp_path: Path) -> None:
    result = _import_app_main(
        {
            "AVATAR_DIR": str(tmp_path / "avatars"),
            "COMMENT_ATTACHMENT_DIR": str(tmp_path / "comment-attachments"),
        }
    )

    assert result.returncode == 0, result.stderr


def test_app_starts_in_production_with_session_secret_key_set(tmp_path: Path) -> None:
    result = _import_app_main(
        {
            "ENVIRONMENT": "production",
            "SESSION_SECRET_KEY": "test-secret",
            "AVATAR_DIR": str(tmp_path / "avatars"),
            "COMMENT_ATTACHMENT_DIR": str(tmp_path / "comment-attachments"),
        }
    )

    assert result.returncode == 0, result.stderr


# --- Secure flag actually reaches the Set-Cookie header, not just the wiring above -----

_REGISTER_AND_PRINT_COOKIE_SCRIPT = """
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    response = client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "hunter2pass",
            "password_confirm": "hunter2pass",
        },
    )
    set_cookie = response.headers.get("set-cookie", "")
    print("SECURE" if "secure" in set_cookie.lower() else "NOT_SECURE")
"""


def _register_and_get_cookie_flag(
    env_overrides: dict[str, str], tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    from tests.db_reset import TEST_DATABASE_URL, wipe_schema

    wipe_schema()  # this test runs the app in a subprocess - see wipe_schema()'s docstring

    env = os.environ.copy()
    env.pop("ENVIRONMENT", None)
    env.update(
        {
            "SESSION_SECRET_KEY": "test-secret",
            "DATABASE_URL": TEST_DATABASE_URL,
            "AVATAR_DIR": str(tmp_path / "avatars"),
            "COMMENT_ATTACHMENT_DIR": str(tmp_path / "comment-attachments"),
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", _REGISTER_AND_PRINT_COOKIE_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
    )


def test_session_cookie_gets_secure_flag_in_production(tmp_path: Path) -> None:
    result = _register_and_get_cookie_flag({"ENVIRONMENT": "production"}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "SECURE"


def test_session_cookie_has_no_secure_flag_outside_production(tmp_path: Path) -> None:
    result = _register_and_get_cookie_flag({}, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "NOT_SECURE"
