"""Password hashing - plain `bcrypt`, not passlib.

passlib's bcrypt backend is incompatible with bcrypt>=4.1 (it reaches into
``bcrypt.__about__.__version__``, removed upstream) and passlib itself has had no
release since 2020, so it's not a maintained way to drive bcrypt anymore. `bcrypt` is
used directly instead.
"""

from __future__ import annotations

import bcrypt

MAX_PASSWORD_BYTES = 72  # bcrypt's own input limit


class PasswordTooLongError(ValueError):
    """Raised instead of silently truncating a password bcrypt can't fully hash."""


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
