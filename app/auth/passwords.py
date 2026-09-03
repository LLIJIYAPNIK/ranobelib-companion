"""Password hashing - plain `bcrypt`, not passlib.

passlib's bcrypt backend is incompatible with bcrypt>=4.1 (it reaches into
``bcrypt.__about__.__version__``, removed upstream) and passlib itself has had no
release since 2020, so it's not a maintained way to drive bcrypt anymore. `bcrypt` is
used directly instead.
"""

from __future__ import annotations

import bcrypt

MAX_PASSWORD_BYTES = 72  # bcrypt's own input limit
MIN_PASSWORD_LENGTH = 8  # NIST SP 800-63B minimum for memorized secrets

# NIST SP 800-63B §5.1.1.2 asks for a check against known-compromised/common values rather
# than character-class rules. This is a small built-in stand-in for that check, not a
# k-anonymity lookup against a breach corpus (e.g. Have I Been Pwned) - a possible later
# extension, not required for PR 184.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "12345678",
        "123456789",
        "1234567890",
        "qwertyui",
        "qwerty123",
        "11111111",
        "00000000",
        "letmein1",
        "iloveyou",
        "admin123",
        "welcome1",
        "abc12345",
        "password123",
    }
)


class PasswordTooLongError(ValueError):
    """Raised instead of silently truncating a password bcrypt can't fully hash."""


class PasswordTooWeakError(ValueError):
    """Raised when a password fails the minimum-strength check (see NIST SP 800-63B)."""


def validate_password_strength(password: str, email: str | None = None) -> None:
    """Minimum length + a check against a short list of common/obvious values.

    Deliberately does *not* require character classes (uppercase/digit/symbol) - NIST
    SP 800-63B recommends against that, since it nudges people toward predictable
    patterns like `Password1!` instead of actually raising entropy.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooWeakError(password)
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        raise PasswordTooWeakError(password)
    if email is not None:
        local_part = email.split("@", 1)[0].lower()
        if local_part and lowered == local_part:
            raise PasswordTooWeakError(password)


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(password)
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
