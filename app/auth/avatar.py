"""Sidebar avatar fallback: initials derived from a user's nickname, or their email if
they haven't set one (PR 90 added the nickname field - before that, email was the only
identity string available)."""

from __future__ import annotations

import re

from app.db.users import User

_SEGMENT_SPLIT = re.compile(r"[\s._\-+]+")


def avatar_initials(user: User) -> str:
    """Up to two uppercase letters, e.g. "Alice Wong" -> "AW", "bob@x.com" -> "BO"."""
    source = user.nickname or user.email.split("@", 1)[0]
    segments = [segment for segment in _SEGMENT_SPLIT.split(source) if segment]
    if not segments:
        return "?"
    if len(segments) == 1:
        return segments[0][:2].upper()
    return (segments[0][0] + segments[1][0]).upper()
