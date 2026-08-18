"""GET /notifications/unread-count and /notifications/recent (PR 168) - the sidebar
bell's own two endpoints - plus GET /notifications and /notifications/page (PR 169), the
full "Все уведомления" page and its own infinite-scroll fragment. app/db/notifications.py's
own unit tests already cover notify_comment_reaction()'s dedupe/self-react rules and
list_notifications_page()'s pagination math in isolation; these exercise the whole request
path end to end, including the actor/comment context the responses add on top of the raw
table."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.db.connection as db_connection
from app.config import get_settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    db_connection._connection = None

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    db_connection._connection = None


def _register(client: TestClient, email: str, password: str = "hunter2pass") -> None:
    client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password},
    )


def _login(client: TestClient, email: str, password: str = "hunter2pass") -> None:
    client.post("/login", data={"email": email, "password": password})


def test_unread_count_requires_login(client: TestClient) -> None:
    response = client.get("/notifications/unread-count", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unread_count_is_zero_with_no_notifications(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/notifications/unread-count")

    assert response.json() == {"unread_count": 0}


def test_recent_notifications_is_empty_with_none(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/notifications/recent")

    assert response.json() == {"unread_count": 0, "notifications": []}


def test_reaction_from_another_user_shows_up_for_the_comment_author(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    comment = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "Согласен, но с оговорками"},
    ).json()["comments"][0]

    _register(client, "bob@example.com")  # switches the session to Bob
    client.post(
        f"/titles/6712--test-novel/chapters/1/5/comments/{comment['id']}/reactions",
        data={"value": "1"},
    )

    _login(client, "alice@example.com")  # back to the comment's own author
    unread = client.get("/notifications/unread-count").json()
    recent = client.get("/notifications/recent").json()

    assert unread == {"unread_count": 1}
    assert recent["unread_count"] == 1
    [notification] = recent["notifications"]
    assert notification["kind"] == "comment_reaction"
    assert notification["is_read"] is False
    assert notification["actor_name"] == "bob@example.com"
    assert notification["comment_id"] == comment["id"]
    assert notification["comment_excerpt"] == "Согласен, но с оговорками"
    assert notification["comment_url"] == "/titles/6712--test-novel/chapters/1/5"


def test_reacting_to_your_own_comment_notifies_no_one(client: TestClient) -> None:
    _register(client, "alice@example.com")
    comment = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "hi"},
    ).json()["comments"][0]

    client.post(
        f"/titles/6712--test-novel/chapters/1/5/comments/{comment['id']}/reactions",
        data={"value": "1"},
    )

    response = client.get("/notifications/recent").json()

    assert response == {"unread_count": 0, "notifications": []}


def test_recent_notifications_truncates_a_long_comment_body(client: TestClient) -> None:
    _register(client, "alice@example.com")
    long_body = "ф" * 200
    comment = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": long_body},
    ).json()["comments"][0]

    _register(client, "bob@example.com")
    client.post(
        f"/titles/6712--test-novel/chapters/1/5/comments/{comment['id']}/reactions",
        data={"value": "1"},
    )

    _login(client, "alice@example.com")
    [notification] = client.get("/notifications/recent").json()["notifications"]

    assert notification["comment_excerpt"] == "ф" * 140 + "…"


def test_notifications_page_requires_login(client: TestClient) -> None:
    response = client.get("/notifications", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_notifications_page_shows_the_empty_state_with_none(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/notifications")

    assert 'data-role="notifications-page-empty"' in response.text
    assert "Пока нет уведомлений" in response.text


def test_notifications_page_renders_a_card_reused_from_the_panel(
    client: TestClient,
) -> None:
    _register(client, "alice@example.com")
    comment = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "Согласен, но с оговорками"},
    ).json()["comments"][0]

    _register(client, "bob@example.com")
    client.post(
        f"/titles/6712--test-novel/chapters/1/5/comments/{comment['id']}/reactions",
        data={"value": "1"},
    )

    _login(client, "alice@example.com")
    response = client.get("/notifications")

    assert 'data-role="notifications-page-list"' in response.text
    # Same card classes as the bell panel (PR 168) - not a second set of styles.
    assert 'class="notifications-panel__item notifications-panel__item--unread"' in (
        response.text
    )
    assert "bob@example.com" in response.text
    assert "Согласен, но с оговорками" in response.text
    assert 'href="/titles/6712--test-novel/chapters/1/5"' in response.text
    assert "static/js/notifications-page.js" in response.text


def test_notifications_page_fragment_paginates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.notifications as notifications_api

    monkeypatch.setattr(notifications_api, "PAGE_SIZE", 1)

    _register(client, "alice@example.com")
    # Different paragraph_index for each - otherwise the second POST's response would
    # list both comments for that one paragraph and ["comments"][0] would grab the first
    # (oldest) one again instead of the one just created.
    first_comment = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "first"},
    ).json()["comments"][0]
    second_comment = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "1", "body": "second"},
    ).json()["comments"][0]

    _register(client, "bob@example.com")
    for comment in (first_comment, second_comment):
        client.post(
            f"/titles/6712--test-novel/chapters/1/5/comments/{comment['id']}/reactions",
            data={"value": "1"},
        )

    _login(client, "alice@example.com")
    first_page = client.get("/notifications")

    assert 'data-next-page="2"' in first_page.text
    assert "«second»" in first_page.text
    assert "«first»" not in first_page.text

    fragment = client.get("/notifications/page", params={"page": "2"})

    assert fragment.headers["X-Has-Next-Page"] == "false"
    assert "«first»" in fragment.text
    assert "«second»" not in fragment.text


def test_notifications_panel_footer_links_to_the_full_page(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.get("/")

    assert 'data-role="notifications-panel"' in response.text
    assert 'class="notifications-panel__footer" href="/notifications"' in response.text
    assert "Все уведомления" in response.text


def _create_notification_for_alice(client: TestClient) -> int:
    """Alice's comment gets a reaction from Bob, leaving Alice with exactly one unread
    notification - logs back in as Alice and returns its id."""
    _register(client, "alice@example.com")
    comment = client.post(
        "/titles/6712--test-novel/chapters/1/5/comments",
        data={"paragraph_index": "0", "body": "hi"},
    ).json()["comments"][0]

    _register(client, "bob@example.com")
    client.post(
        f"/titles/6712--test-novel/chapters/1/5/comments/{comment['id']}/reactions",
        data={"value": "1"},
    )

    _login(client, "alice@example.com")
    [notification] = client.get("/notifications/recent").json()["notifications"]
    return notification["id"]


def test_mark_read_requires_login(client: TestClient) -> None:
    response = client.post("/notifications/1/read", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_mark_read_flips_the_flag_and_reports_the_new_unread_count(
    client: TestClient,
) -> None:
    notification_id = _create_notification_for_alice(client)

    response = client.post(f"/notifications/{notification_id}/read")

    assert response.json() == {"unread_count": 0}
    [notification] = client.get("/notifications/recent").json()["notifications"]
    assert notification["is_read"] is True


def test_mark_read_404s_for_a_missing_notification(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.post("/notifications/999/read")

    assert response.status_code == 404


def test_mark_read_404s_for_someone_elses_notification(client: TestClient) -> None:
    notification_id = _create_notification_for_alice(client)
    _register(client, "carol@example.com")

    response = client.post(f"/notifications/{notification_id}/read")

    assert response.status_code == 404


def test_delete_requires_login(client: TestClient) -> None:
    response = client.delete("/notifications/1", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_delete_removes_it_and_reports_the_new_unread_count(client: TestClient) -> None:
    notification_id = _create_notification_for_alice(client)

    response = client.delete(f"/notifications/{notification_id}")

    assert response.json() == {"unread_count": 0}
    assert client.get("/notifications/recent").json()["notifications"] == []


def test_delete_404s_for_a_missing_notification(client: TestClient) -> None:
    _register(client, "alice@example.com")

    response = client.delete("/notifications/999")

    assert response.status_code == 404


def test_delete_404s_for_someone_elses_notification(client: TestClient) -> None:
    notification_id = _create_notification_for_alice(client)
    _register(client, "carol@example.com")

    response = client.delete(f"/notifications/{notification_id}")

    assert response.status_code == 404
