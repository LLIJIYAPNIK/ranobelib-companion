from app.auth.avatar import avatar_initials
from app.db.users import User


def _user(email: str, nickname: str | None = None) -> User:
    return User(
        id=1,
        email=email,
        password_hash="x",
        created_at="2026-01-01T00:00:00",
        nickname=nickname,
    )


def test_avatar_initials_from_two_dot_separated_segments() -> None:
    assert avatar_initials(_user("alice.wong@example.com")) == "AW"


def test_avatar_initials_from_underscore_separated_segments() -> None:
    assert avatar_initials(_user("bob_smith@example.com")) == "BS"


def test_avatar_initials_from_a_single_segment_uses_its_first_two_letters() -> None:
    assert avatar_initials(_user("alice@example.com")) == "AL"


def test_avatar_initials_from_a_single_letter_local_part() -> None:
    assert avatar_initials(_user("a@example.com")) == "A"


def test_avatar_initials_prefer_the_nickname_over_the_email() -> None:
    assert avatar_initials(_user("alice@example.com", nickname="Bob Carter")) == "BC"


def test_avatar_initials_from_a_single_word_nickname() -> None:
    assert avatar_initials(_user("alice@example.com", nickname="Cher")) == "CH"


def test_avatar_initials_fall_back_to_email_when_nickname_is_blank() -> None:
    assert avatar_initials(_user("alice.wong@example.com", nickname="")) == "AW"
