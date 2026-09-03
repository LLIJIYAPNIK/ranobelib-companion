import pytest

from app.auth.passwords import (
    PasswordTooLongError,
    PasswordTooWeakError,
    hash_password,
    validate_password_strength,
    verify_password,
)


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


# --- PR 184: validate_password_strength ------------------------------------------------


def test_strength_accepts_a_reasonable_password() -> None:
    validate_password_strength("hunter2pass")  # must not raise


def test_strength_rejects_below_minimum_length() -> None:
    with pytest.raises(PasswordTooWeakError):
        validate_password_strength("short1")  # 6 chars


def test_strength_accepts_exactly_the_minimum_length() -> None:
    validate_password_strength("eightchr")  # 8 chars, must not raise


def test_strength_rejects_a_common_blocklisted_password() -> None:
    with pytest.raises(PasswordTooWeakError):
        validate_password_strength("password")


def test_strength_blocklist_check_is_case_insensitive() -> None:
    with pytest.raises(PasswordTooWeakError):
        validate_password_strength("PaSsWoRd")


def test_strength_rejects_password_equal_to_email_local_part() -> None:
    with pytest.raises(PasswordTooWeakError):
        validate_password_strength("aliceperson", email="aliceperson@example.com")


def test_strength_email_check_is_case_insensitive() -> None:
    with pytest.raises(PasswordTooWeakError):
        validate_password_strength("AlicePerson", email="aliceperson@example.com")


def test_strength_ignores_email_when_none_given() -> None:
    validate_password_strength("aliceperson")  # must not raise without an email to compare
