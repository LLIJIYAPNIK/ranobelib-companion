from app.auth.avatar import avatar_initials
from app.db.users import User


def _user(email: str) -> User:
    return User(id=1, email=email, password_hash="x", created_at="2026-01-01T00:00:00")


def test_avatar_initials_from_two_dot_separated_segments() -> None:
    assert avatar_initials(_user("alice.wong@example.com")) == "AW"


def test_avatar_initials_from_underscore_separated_segments() -> None:
    assert avatar_initials(_user("bob_smith@example.com")) == "BS"


def test_avatar_initials_from_a_single_segment_uses_its_first_two_letters() -> None:
    assert avatar_initials(_user("alice@example.com")) == "AL"


def test_avatar_initials_from_a_single_letter_local_part() -> None:
    assert avatar_initials(_user("a@example.com")) == "A"
