from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_renders_search_form() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'action="/titles/open"' in response.text
    assert 'name="url"' in response.text
