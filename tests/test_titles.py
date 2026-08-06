from unittest.mock import patch

from fastapi.testclient import TestClient
from ranobelib import TitleNotFoundError
from ranobelib.models import Cover, Label, Title

from app.main import app

client = TestClient(app, follow_redirects=False)


def _fake_title(slug_url: str = "6712--test-novel") -> Title:
    return Title(
        id=6712,
        name="Test Novel",
        rus_name="Тестовый роман",
        slug="test-novel",
        slug_url=slug_url,
        cover=Cover(default="https://example.com/cover.jpg"),
        age_restriction=Label(id=0, label="16+"),
        status=Label(id=1, label="Онгоинг"),
        chapter_count=42,
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


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> "_RaisingClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get_info(self) -> Title:
        raise self._exc


def test_open_title_redirects_to_canonical_slug_url() -> None:
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get(
            "/titles/open",
            params={"url": "https://ranobelib.me/ru/book/6712--test-novel"},
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/titles/6712--test-novel"


def test_open_title_rejects_unparseable_input() -> None:
    response = client.get("/titles/open", params={"url": "not a link at all"})

    assert response.status_code == 400
    assert "Не удалось распознать ссылку" in response.text


def test_show_title_renders_metadata() -> None:
    title = _fake_title()
    with patch("app.services.client.RanobeLib", return_value=_FakeClient(title)):
        response = client.get("/titles/6712--test-novel")

    assert response.status_code == 200
    assert "Test Novel" in response.text
    assert "Тестовый роман" in response.text
    assert "https://example.com/cover.jpg" in response.text
    assert "42" in response.text


def test_show_title_not_found_renders_html_error_page() -> None:
    exc = TitleNotFoundError("6712--missing")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--missing", headers={"accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Тайтл не найден, проверьте ссылку" in response.text
    assert 'href="/"' in response.text


def test_show_title_not_found_returns_json_without_html_accept() -> None:
    exc = TitleNotFoundError("6712--missing")
    with patch("app.services.client.RanobeLib", return_value=_RaisingClient(exc)):
        response = client.get("/titles/6712--missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Тайтл не найден, проверьте ссылку"}
