"""PR 357: «Пожаловаться» for a reader - the form in base.html (reasons from the same
list POST /reports checks, details with its limit), the button in a comment's action row
(never on your own, a deleted or a hidden comment), «⋯» on someone else's profile, and
report-form.js run under Node: the dialog on desktop and the shared sheet on phones,
the sending and error states, the toast, and a repeat on the same object."""

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connection
from app.db.users import get_user_by_email
from app.reports import DETAILS_LIMIT, REPORT_REASONS
from tests.auth_helpers import register
from tests.db_reset import reset_app_database

ROOT = Path(__file__).resolve().parents[1]
REPORT_JS = ROOT / "app/static/js/report-form.js"
PROFILE_JS = (ROOT / "app/static/js/profile-more.js").read_text(encoding="utf-8")
COMMENTS_JS = (ROOT / "app/static/js/paragraph-menu.js").read_text(encoding="utf-8")
HARNESS = ROOT / "tests/js/report_form_harness.mjs"

# --- pages --------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    reset_app_database(monkeypatch)
    get_settings.cache_clear()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


async def _id(email: str) -> int:
    async with connection() as conn:
        user = await get_user_by_email(conn, email)
    assert user is not None
    return user.id


def _form(html: str) -> str:
    match = re.search(r'<div class="wn-report" data-role="report-form".*?</dialog>', html, re.S)
    assert match, "no report form on the page"
    return match.group(0)


def test_the_form_lists_every_reason_with_the_details_limit(client: TestClient) -> None:
    register(client, "alice@example.com")

    form = _form(client.get("/").text)

    values = re.findall(r'<input type="radio" name="reason" value="([a-z]+)"', form)
    assert values == list(REPORT_REASONS)
    for label in REPORT_REASONS.values():
        assert f"<span>{label}</span>" in form
    assert f'maxlength="{DETAILS_LIMIT}"' in form
    assert f"0 / {DETAILS_LIMIT}" in form
    # Keyboard and screen readers: a legend for the group, the first reason focused on
    # open, the textarea labelled and tied to its counter, the error announced.
    assert '<legend class="wn-report__legend">Причина</legend>' in form
    assert 'value="spam" data-autofocus' in form
    assert '<label class="ui-field__label" for="report-details">' in form
    assert 'aria-describedby="report-details-count"' in form
    assert 'data-role="report-error" role="alert" hidden' in form
    assert 'aria-labelledby="report-dialog-title"' in form
    assert 'aria-label="Закрыть"' in form


def test_the_form_and_its_script_only_for_a_logged_in_visitor(client: TestClient) -> None:
    guest = client.get("/").text
    assert 'data-role="report-form"' not in guest
    assert "js/report-form.js" not in guest

    register(client, "alice@example.com")
    assert "js/report-form.js" in client.get("/").text


async def test_more_on_someone_elses_profile(client: TestClient) -> None:
    register(client, "bob@example.com", nickname="bob")
    bob = await _id("bob@example.com")
    register(client, "alice@example.com", nickname="alice")

    html = client.get(f"/profile/{bob}").text

    assert 'data-role="profile-more"' in html
    assert 'aria-haspopup="menu" aria-expanded="false" aria-controls="profile-more-menu"' in html
    assert 'aria-label="Ещё"' in html
    assert 'data-profile-name="bob"' in html
    assert f'data-role="profile-more-menu" data-user-id="{bob}"' in html
    assert 'role="menuitem" data-role="profile-report"' in html
    assert "Пожаловаться</button>" in html
    assert "js/profile-more.js" in html


async def test_no_more_on_your_own_profile(client: TestClient) -> None:
    register(client, "alice@example.com")
    alice = await _id("alice@example.com")

    for path in ("/profile", f"/profile/{alice}"):
        html = client.get(path).text
        assert 'data-role="profile-more"' not in html
        assert "js/profile-more.js" not in html


async def test_no_more_for_a_guest(client: TestClient) -> None:
    register(client, "bob@example.com")
    bob = await _id("bob@example.com")
    client.post("/logout")
    client.cookies.clear()

    html = client.get(f"/profile/{bob}").text

    assert 'data-role="profile-more"' not in html
    assert "Пожаловаться" not in html


# --- the comment's button and the profile menu (source) -----------------------------------


def test_comment_button_only_on_someone_elses_live_comment() -> None:
    block = COMMENTS_JS[COMMENTS_JS.index("// PR 357") :]
    block = block[: block.index("actions.append(reportToggle);")]

    assert "isAuthenticated && currentUserId !== comment.user_id && !inert" in block
    assert '"Пожаловаться"' in block
    assert 'reportToggle.dataset.reportTargetType = "comment"' in block
    assert "reportToggle.dataset.reportTargetId = String(comment.id)" in block
    assert 'reportToggle.setAttribute("aria-haspopup", "dialog")' in block
    # inert = deleted or hidden (PR 350), set where the comment starts rendering.
    assert "const inert = comment.is_deleted || comment.is_hidden;" in COMMENTS_JS


def test_profile_menu_keyboard_and_focus() -> None:
    assert 'event.key === "Escape" && open === "popover"' in PROFILE_JS
    assert "report.focus();" in PROFILE_JS
    assert 'trigger.setAttribute("aria-expanded", "true")' in PROFILE_JS
    assert 'trigger.setAttribute("aria-expanded", "false")' in PROFILE_JS
    # The form hands focus back to «⋯», not to the menu item it hid.
    assert 'targetType: "profile"' in PROFILE_JS
    assert "opener: trigger" in PROFILE_JS
    # The hero clips (overflow: hidden) - the popover lives in <body>.
    assert "document.body.append(menu);" in PROFILE_JS


# --- report-form.js under Node ------------------------------------------------------------


@cache
def _run() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(HARNESS), str(REPORT_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@needs_node
def test_desktop_opens_the_dialog_on_the_first_reason() -> None:
    opened = _run()["desktop"]["opened"]

    assert opened["dialogOpen"] and opened["where"] == "dialog" and not opened["hidden"]
    assert opened["title"] == "Пожаловаться на комментарий"
    assert opened["target"] == "comment:5"
    assert opened["focus"] == "radio-spam"
    assert opened["sheetOpen"] == 0


@needs_node
def test_a_reason_is_required_before_anything_is_sent() -> None:
    result = _run()["desktop"]

    assert result["noReason"]["error"] == "Выберите причину жалобы"
    assert result["noReason"]["fetches"] == 0
    # Picking one clears it; the counter follows the details.
    assert result["picked"]["error"] is None
    assert result["picked"]["count"] == "6 / 500"


@needs_node
def test_sending_state() -> None:
    result = _run()["desktop"]

    sending = result["sending"]
    assert sending["submitDisabled"] and sending["submitText"] == "Отправляем…"
    assert sending["busy"] == "true"
    assert result["sentBody"] == {
        "url": "/reports",
        "method": "POST",
        "reason": "abuse",
        "target": "comment:5",
        "details": "грубит",
    }
    # Neither Escape nor a second click interrupts or repeats it.
    assert result["escapeWhileSending"] is True
    assert result["fetchesWhileSending"] == 1


@needs_node
def test_errors_stay_in_the_form() -> None:
    result = _run()["desktop"]

    for step, error in [
        ("refused", "Комментарий уже скрыт"),
        ("offline", "Нет соединения. Попробуйте ещё раз."),
        ("unreadable", "Не удалось отправить жалобу. Попробуйте ещё раз."),
    ]:
        state = result[step]
        assert state["error"] == error
        assert state["dialogOpen"] and not state["submitDisabled"] and state["busy"] is None
        assert state["submitText"] == "Отправить"
        assert state["toast"] is None


@needs_node
def test_success_closes_with_a_toast_and_returns_focus() -> None:
    sent = _run()["desktop"]["sent"]

    assert not sent["dialogOpen"] and sent["where"] == "home" and sent["hidden"]
    assert sent["toast"] == "Жалоба отправлена" and sent["toastRole"] == "status"
    assert sent["focus"] == "comment-button"


@needs_node
def test_a_repeat_says_you_already_reported() -> None:
    result = _run()["desktop"]

    # The same object again on this page: no form, no request.
    again = result["repeatOnPage"]
    assert again["showModal"] == 1 and again["fetches"] == 4
    assert again["toast"] == "Вы уже отправили жалобу"
    # Reported on an earlier visit: the server says so (200 already_sent).
    assert result["delegatedHandled"] is True
    assert result["profileOpened"]["title"] == "Пожаловаться на профиль"
    assert result["profileOpened"]["target"] == "profile:7"
    assert result["alreadySent"]["toast"] == "Вы уже отправили жалобу"
    assert result["alreadySent"]["focus"] == "opener-profile-7"


@needs_node
def test_cancel_and_an_unknown_target() -> None:
    result = _run()["desktop"]

    assert not result["cancelled"]["dialogOpen"] and result["cancelled"]["where"] == "home"
    assert result["unknownType"]["showModal"] == result["cancelled"]["showModal"]


@needs_node
def test_phones_use_the_shared_bottom_sheet() -> None:
    result = _run()["phone"]

    assert result["opened"]["showModal"] == 0
    assert result["opened"]["where"] == "sheet"
    assert result["sheetTitle"] == {"title": "Пожаловаться на профиль", "content": True}
    assert result["sent"]["sheetClose"] == 1
    assert result["sent"]["toast"] == "Жалоба отправлена"
    assert result["repeatOnPage"]["toast"] == "Вы уже отправили жалобу"
    assert result["repeatOnPage"]["sheetOpen"] == 1
    assert result["repeatOnPage"]["focus"] == "profile-more"
