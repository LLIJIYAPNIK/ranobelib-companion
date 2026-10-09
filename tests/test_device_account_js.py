"""No leaks between accounts on one device: app/static/js/device-account.js (PR 333).

Run under Node (node:vm with fake Web Storage, a recording sync queue and downloads store,
the logout form and its sheet - see tests/js/device_account_harness.mjs). What must hold:
one account's queued progress/activity and reading positions never reach the next
account; downloads survive a logout only by an explicit «Оставить скачанное»; device
settings stay. The server half (private responses, Clear-Site-Data on logout) is in
tests/test_private_responses.py. Skipped where Node isn't installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "app" / "static" / "js" / "device-account.js"
_HARNESS = _ROOT / "tests" / "js" / "device_account_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")

_DEVICE_SETTINGS = {
    "readerSettings": '{"fontSize":19}',
    "sidebarExpanded": "1",
    "cookieNoticeDismissed": "1",
}
_PERSONAL = {
    "tapToReadProgress:/titles/a/chapters/1/1",
    "tapToReadProgress:/titles/b/chapters/2/7",
    "readerLastChapter:a",
}


@cache
def _scenarios() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def _personal_left(state: dict[str, Any]) -> set[str]:
    return _PERSONAL & set(state["local"]) | ({"downloadReadyDismissed"} & set(state["session"]))


def test_the_first_account_on_a_device_is_recorded_and_nothing_is_cleared() -> None:
    first = _scenarios()["firstSignIn"]

    assert first["log"] == []
    assert first["local"]["wnDeviceAccount"] == "9"
    assert _personal_left(first) == _PERSONAL | {"downloadReadyDismissed"}


def test_the_same_account_again_keeps_everything() -> None:
    same = _scenarios()["sameAccount"]

    assert same["log"] == []
    assert _personal_left(same) == _PERSONAL | {"downloadReadyDismissed"}


def test_another_account_never_inherits_the_previous_ones_data() -> None:
    # The session ran out and someone else signed in, without a logout in between: the
    # previous account's queue, positions and downloads are gone before this page uses
    # them (sync-queue.js waits for `ready`). Nobody chose to keep the downloads.
    switched = _scenarios()["switched"]

    assert switched["log"] == ["clearQueue", "clearDownloads"]
    assert _personal_left(switched) == set()
    assert switched["local"]["wnDeviceAccount"] == "9"
    assert switched["session"] == {"swUpdateLater": "1"}


def test_a_signed_out_page_decides_nothing() -> None:
    signed_out = _scenarios()["signedOutPage"]

    assert signed_out["log"] == []
    assert signed_out["local"]["wnDeviceAccount"] == "7"


def test_logout_sends_what_is_queued_then_clears_and_logs_out() -> None:
    logout = _scenarios()["logoutNothingDownloaded"]

    assert logout["prevented"] is True  # held back until the device is cleared
    assert logout["log"] == ["flush", "clearQueue", "clearDownloads", "submit"]
    assert logout["opened"] == []  # nothing downloaded - nothing to ask
    assert _personal_left(logout) == set()
    assert "wnDeviceAccount" not in logout["local"]


def test_logout_with_downloads_asks_and_keep_leaves_them() -> None:
    scenario = _scenarios()["logoutKeep"]

    asked = scenario["asked"]
    # The desktop profile menu steps aside first, then the sheet opens.
    assert asked["opened"] == ["event:profile-menu:close", "Выйти из аккаунта"]
    assert asked["count"] == "2"
    assert asked["log"] == []  # nothing happens until a choice
    after = scenario["after"]
    assert after["log"] == ["flush", "clearQueue", "submit"]
    assert _personal_left(after) == set()


def test_logout_with_delete_clears_the_downloads_too() -> None:
    delete = _scenarios()["logoutDelete"]

    assert delete["log"] == ["flush", "clearQueue", "clearDownloads", "submit"]


def test_a_queue_that_cant_be_sent_doesnt_hold_up_logout() -> None:
    hangs = _scenarios()["logoutFlushHangs"]

    assert hangs["timers"] == [3000]
    assert hangs["log"][-1] == "submit"


@pytest.mark.parametrize(
    "scenario", ["switched", "logoutNothingDownloaded", "logoutDelete", "logoutFlushHangs"]
)
def test_device_settings_are_not_personal_and_stay(scenario: str) -> None:
    local = _scenarios()[scenario]["local"]

    assert {key: local[key] for key in _DEVICE_SETTINGS} == _DEVICE_SETTINGS
