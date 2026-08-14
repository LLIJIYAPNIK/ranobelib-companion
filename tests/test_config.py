from pathlib import Path

from app.config import get_settings


def test_get_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.delenv("CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.delenv("SESSION_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("SESSION_REMEMBER_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("AVATAR_DIR", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.cache_ttl == 6 * 60 * 60
    assert str(settings.cache_dir) == ".ranobelib_cache"
    assert str(settings.db_path) == ".ranobelib_companion.db"
    assert settings.session_secret_key  # generated when unset, but never empty
    assert settings.session_max_age == 14 * 24 * 60 * 60
    assert settings.session_remember_max_age == 90 * 24 * 60 * 60
    assert str(settings.avatar_dir) == ".ranobelib_avatars"


def test_get_settings_generates_a_different_key_per_uncached_call(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    first = get_settings().session_secret_key
    get_settings.cache_clear()
    second = get_settings().session_secret_key

    assert first != second

    get_settings.cache_clear()


def test_get_settings_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("CACHE_DIR", "/data/ranobelib-cache")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("DB_PATH", "/data/ranobelib-companion.db")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SESSION_MAX_AGE_SECONDS", "3600")
    monkeypatch.setenv("SESSION_REMEMBER_MAX_AGE_SECONDS", "172800")
    monkeypatch.setenv("AVATAR_DIR", "/data/ranobelib-avatars")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.cache_dir == Path("/data/ranobelib-cache")
    assert settings.cache_ttl == 60.0
    assert settings.db_path == Path("/data/ranobelib-companion.db")
    assert settings.session_secret_key == "test-secret"
    assert settings.session_max_age == 3600
    assert settings.session_remember_max_age == 172800
    assert settings.avatar_dir == Path("/data/ranobelib-avatars")

    get_settings.cache_clear()
