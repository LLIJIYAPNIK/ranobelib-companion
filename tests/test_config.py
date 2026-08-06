from pathlib import Path

from app.config import Settings, get_settings


def test_get_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.delenv("CACHE_TTL_SECONDS", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings == Settings(cache_dir=settings.cache_dir, cache_ttl=6 * 60 * 60)
    assert str(settings.cache_dir) == ".ranobelib_cache"


def test_get_settings_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("CACHE_DIR", "/data/ranobelib-cache")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.cache_dir == Path("/data/ranobelib-cache")
    assert settings.cache_ttl == 60.0

    get_settings.cache_clear()
