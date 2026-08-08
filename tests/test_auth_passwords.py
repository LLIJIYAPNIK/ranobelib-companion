import pytest

from app.auth.passwords import PasswordTooLongError, hash_password, verify_password


def test_hash_then_verify_correct_password() -> None:
    password_hash = hash_password("hunter2pass")

    assert verify_password("hunter2pass", password_hash)


def test_verify_rejects_wrong_password() -> None:
    password_hash = hash_password("hunter2pass")

    assert not verify_password("wrong-password", password_hash)


def test_hash_produces_a_different_value_each_time() -> None:
    # bcrypt salts each hash, so hashing the same password twice must not collide.
    assert hash_password("hunter2pass") != hash_password("hunter2pass")


def test_password_over_72_bytes_is_rejected_not_truncated() -> None:
    with pytest.raises(PasswordTooLongError):
        hash_password("x" * 73)


def test_verify_over_72_bytes_returns_false_not_raises() -> None:
    password_hash = hash_password("hunter2pass")

    assert not verify_password("x" * 73, password_hash)
