from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class _FakeResponse:
    def __init__(
        self, content: bytes, *, content_type: str = "image/jpeg", status_code: int = 200
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=None)  # type: ignore[arg-type]


class _FakeHttpxClient:
    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error
        self.received_url: str | None = None
        self.received_headers: dict[str, str] | None = None

    async def __aenter__(self) -> "_FakeHttpxClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get(self, url: str, timeout: float, headers: dict[str, str]) -> _FakeResponse:
        self.received_url = url
        self.received_headers = headers
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def test_download_image_rejects_disallowed_host() -> None:
    response = client.get(
        "/images/download", params={"url": "https://evil.example.com/x.jpg"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Недопустимый адрес изображения"}


def test_download_image_rejects_non_http_scheme() -> None:
    # Not just a hostname check - file:// (or any non-http(s) scheme) must be rejected
    # even if someone crafted a URL where urlsplit's hostname happened to look allowed.
    response = client.get("/images/download", params={"url": "file:///etc/passwd"})

    assert response.status_code == 400


def test_download_image_rejects_url_without_a_host() -> None:
    response = client.get("/images/download", params={"url": "not-a-url-at-all"})

    assert response.status_code == 400


def test_download_image_allows_ranobelib_me() -> None:
    fake = _FakeHttpxClient(_FakeResponse(b"bytes", content_type="image/jpeg"))
    with patch("app.api.images.httpx.AsyncClient", return_value=fake):
        response = client.get(
            "/images/download",
            params={"url": "https://ranobelib.me/uploads/ranobe/1/chapters/2/a.jpg"},
        )

    assert response.status_code == 200
    assert fake.received_url == "https://ranobelib.me/uploads/ranobe/1/chapters/2/a.jpg"


def test_download_image_sends_a_ranobelib_referer() -> None:
    # cover.cdnlibs.org 403s a plain, header-less request (verified against the live
    # CDN) - it's checking Referer as anti-hotlink protection, same as most image CDNs.
    fake = _FakeHttpxClient(_FakeResponse(b"bytes"))
    with patch("app.api.images.httpx.AsyncClient", return_value=fake):
        client.get(
            "/images/download",
            params={"url": "https://cover.cdnlibs.org/uploads/cover/x/cover/a.jpg"},
        )

    assert fake.received_headers == {"Referer": "https://ranobelib.me/"}


def test_download_image_allows_cdnlibs_org_subdomain() -> None:
    # Covers (PR 16/142) are hosted on cover.cdnlibs.org, not ranobelib.me itself -
    # restricting to exactly "ranobelib.me" would 400 every cover download.
    fake = _FakeHttpxClient(_FakeResponse(b"bytes"))
    with patch("app.api.images.httpx.AsyncClient", return_value=fake):
        response = client.get(
            "/images/download",
            params={"url": "https://cover.cdnlibs.org/uploads/cover/x/cover/a.jpg"},
        )

    assert response.status_code == 200


def test_download_image_rejects_lookalike_host() -> None:
    # "ranobelib.me.evil.com" ends with the right string but isn't actually a subdomain
    # of ranobelib.me - the endswith check must be anchored on a "." boundary.
    response = client.get(
        "/images/download", params={"url": "https://ranobelib.me.evil.com/x.jpg"}
    )

    assert response.status_code == 400


def test_download_image_sets_attachment_content_disposition() -> None:
    fake = _FakeHttpxClient(_FakeResponse(b"\xff\xd8\xff", content_type="image/jpeg"))
    with patch("app.api.images.httpx.AsyncClient", return_value=fake):
        response = client.get(
            "/images/download",
            params={"url": "https://ranobelib.me/uploads/ranobe/1/chapters/2/a.jpg"},
        )

    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xff"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"] == 'attachment; filename="a.jpg"'


def test_download_image_falls_back_to_a_generic_filename() -> None:
    fake = _FakeHttpxClient(_FakeResponse(b"bytes"))
    with patch("app.api.images.httpx.AsyncClient", return_value=fake):
        response = client.get(
            "/images/download", params={"url": "https://ranobelib.me/"}
        )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="image"'


def test_download_image_surfaces_upstream_failure_as_502() -> None:
    fake = _FakeHttpxClient(error=httpx.ConnectError("boom"))
    with patch("app.api.images.httpx.AsyncClient", return_value=fake):
        response = client.get(
            "/images/download",
            params={"url": "https://ranobelib.me/uploads/ranobe/1/chapters/2/a.jpg"},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Не удалось скачать изображение"}


def test_download_image_surfaces_bad_upstream_status_as_502() -> None:
    fake = _FakeHttpxClient(_FakeResponse(b"", status_code=404))
    with patch("app.api.images.httpx.AsyncClient", return_value=fake):
        response = client.get(
            "/images/download",
            params={"url": "https://ranobelib.me/uploads/ranobe/1/chapters/2/missing.jpg"},
        )

    assert response.status_code == 502
