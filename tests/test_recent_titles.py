import base64
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib.models import Cover, Label, Title, Volume

from app.main import app
from app.recent_titles import _MAX_NAME_LENGTH

client = TestClient(app)


def _encode_cookie(entries: list[dict[str, object]]) -> str:
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cookie(cookie: str) -> list[dict[str, object]]:
    return json.loads(base64.urlsafe_b64decode(cookie.encode("ascii")))


def _fake_title(slug_url: str, name: str, cover: Cover | None = None) -> Title:
    return Title(
        id=1,
        name=name,
        slug=slug_url.split("--", 1)[1],
        slug_url=slug_url,
        cover=cover or Cover(),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
    )


class _FakeClient:
    def __init__(self, title: Title) -> None:
        self._title = title

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        return self._title

    async def get_table_of_contents(self) -> list[Volume]:
        return []

    async def estimate_title_size(self) -> int:
        return 0


def test_show_title_sets_recent_titles_cookie() -> None:
    client.cookies.clear()
    title = _fake_title("1--first-novel", "First Novel")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/1--first-novel")

    cookie = response.cookies.get("recent_titles")
    assert cookie is not None
    assert _decode_cookie(cookie) == [
        {"slug_url": "1--first-novel", "name": "First Novel", "cover_url": None}
    ]


def test_show_title_stores_cover_url_in_recent_titles_cookie() -> None:
    client.cookies.clear()
    title = _fake_title(
        "1--first-novel", "First Novel", cover=Cover(default="https://example.com/cover.jpg")
    )
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/1--first-novel")

    cookie = _decode_cookie(response.cookies.get("recent_titles"))
    assert cookie[0]["cover_url"] == "https://example.com/cover.jpg"


def test_show_title_moves_reopened_title_to_front() -> None:
    client.cookies.clear()
    first = _fake_title("1--first-novel", "First Novel")
    second = _fake_title("2--second-novel", "Second Novel")

    with patch("app.services.client.RanobeLib", return_value=_FakeClient(first)):
        client.get("/titles/1--first-novel")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(second)):
        client.get("/titles/2--second-novel")
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(first)):
        response = client.get("/titles/1--first-novel")

    cookie = _decode_cookie(response.cookies.get("recent_titles"))
    assert [item["slug_url"] for item in cookie] == ["1--first-novel", "2--second-novel"]


def test_show_title_truncates_long_names_in_recent_titles_cookie() -> None:
    # Regression test for #205: nginx returned 502 ("upstream sent too big header") on
    # titles with long Cyrillic names because the old percent-encoded cookie could blow
    # past nginx's default response-header buffer. A single title name long enough to
    # threaten that on its own must be capped when stored, not just the entry count.
    client.cookies.clear()
    long_name = "Очень длинное название новеллы, " * 10
    assert len(long_name) > _MAX_NAME_LENGTH
    title = _fake_title("1--first-novel", long_name)
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/1--first-novel")

    cookie = _decode_cookie(response.cookies.get("recent_titles"))
    assert len(cookie[0]["name"]) <= _MAX_NAME_LENGTH
    assert cookie[0]["name"].endswith("…")


def test_home_lists_recent_titles_from_cookie() -> None:
    client.cookies.clear()
    client.cookies.set(
        "recent_titles",
        _encode_cookie([{"slug_url": "1--first-novel", "name": "First Novel"}]),
    )

    response = client.get("/")

    assert "First Novel" in response.text
    assert 'href="/titles/1--first-novel"' in response.text
    assert "<img" not in response.text  # old cookie shape, no cover_url - no crash either

    client.cookies.clear()


def test_home_renders_cover_when_present_in_cookie() -> None:
    client.cookies.clear()
    client.cookies.set(
        "recent_titles",
        _encode_cookie(
            [
                {
                    "slug_url": "1--first-novel",
                    "name": "First Novel",
                    "cover_url": "https://example.com/cover.jpg",
                }
            ]
        ),
    )

    response = client.get("/")

    assert 'src="https://example.com/cover.jpg"' in response.text

    client.cookies.clear()


def test_home_ignores_malformed_recent_titles_cookie() -> None:
    client.cookies.clear()
    client.cookies.set("recent_titles", "not-valid-base64!!")

    response = client.get("/")

    assert response.status_code == 200

    client.cookies.clear()


def test_home_renders_a_remove_button_for_each_recent_card() -> None:
    client.cookies.clear()
    client.cookies.set(
        "recent_titles",
        _encode_cookie([{"slug_url": "1--first-novel", "name": "First Novel"}]),
    )

    response = client.get("/")

    assert 'data-role="forget-recent-title"' in response.text
    assert 'data-slug-url="1--first-novel"' in response.text

    client.cookies.clear()


def test_forget_removes_only_the_given_title_from_the_cookie() -> None:
    client.cookies.clear()
    client.cookies.set(
        "recent_titles",
        _encode_cookie(
            [
                {"slug_url": "1--first-novel", "name": "First Novel"},
                {"slug_url": "2--second-novel", "name": "Second Novel"},
            ]
        ),
    )

    response = client.post("/recent/1--first-novel/forget")

    assert response.status_code == 204
    cookie = _decode_cookie(response.cookies.get("recent_titles"))
    assert [item["slug_url"] for item in cookie] == ["2--second-novel"]

    client.cookies.clear()


def test_forget_unknown_title_is_not_an_error() -> None:
    client.cookies.clear()
    client.cookies.set(
        "recent_titles",
        _encode_cookie([{"slug_url": "1--first-novel", "name": "First Novel"}]),
    )

    response = client.post("/recent/9--never-opened/forget")

    assert response.status_code == 204
    cookie = _decode_cookie(response.cookies.get("recent_titles"))
    assert [item["slug_url"] for item in cookie] == ["1--first-novel"]

    client.cookies.clear()
