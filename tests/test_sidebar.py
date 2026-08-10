from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_sidebar_renders_a_collapsed_burger_toggle_by_default() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'data-role="sidebar"' in response.text
    assert 'data-role="sidebar-toggle"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert "static/js/sidebar-toggle.js" in response.text


def test_sidebar_renders_a_text_label_next_to_each_nav_icon() -> None:
    response = client.get("/")

    assert response.status_code == 200
    for label in ("Главная", "Библиотека", "Загрузки", "Активность", "Настройки"):
        assert f'<span class="sidebar__label">{label}</span>' in response.text
