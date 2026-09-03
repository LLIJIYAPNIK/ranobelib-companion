import time

from app.auth.rate_limit import is_rate_limited


def test_allows_up_to_the_configured_limit() -> None:
    key = "test:allow"

    results = [is_rate_limited(key, max_attempts=3, window_seconds=60) for _ in range(3)]

    assert results == [False, False, False]


def test_blocks_once_the_limit_is_exceeded() -> None:
    key = "test:block"
    for _ in range(3):
        is_rate_limited(key, max_attempts=3, window_seconds=60)

    assert is_rate_limited(key, max_attempts=3, window_seconds=60) is True


def test_different_keys_are_rate_limited_independently() -> None:
    for _ in range(3):
        is_rate_limited("test:a", max_attempts=3, window_seconds=60)

    assert is_rate_limited("test:b", max_attempts=3, window_seconds=60) is False


def test_limit_expires_after_the_window_passes() -> None:
    key = "test:expiry"
    for _ in range(4):
        is_rate_limited(key, max_attempts=3, window_seconds=0.05)  # 4th already over limit

    time.sleep(0.1)

    assert is_rate_limited(key, max_attempts=3, window_seconds=0.05) is False
