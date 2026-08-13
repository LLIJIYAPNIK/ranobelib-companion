"""Sidebar avatar fallback: initials derived from a user's email.

No nickname exists yet (see PR 90 in CLAUDE.md's roadmap) - email is the only identity
string available, so it's the only source for initials until a nickname exists to prefer
instead.
"""

from __future__ import annotations

import re

from app.db.users import User

_SEGMENT_SPLIT = re.compile(r"[._\-+]+")


def avatar_initials(user: User) -> str:
    """Up to two uppercase letters, e.g. "alice.wong@x.com" -> "AW", "bob@x.com" -> "BO"."""
    local_part = user.email.split("@", 1)[0]
    segments = [segment for segment in _SEGMENT_SPLIT.split(local_part) if segment]
    if not segments:
        return "?"
    if len(segments) == 1:
        return segments[0][:2].upper()
    return (segments[0][0] + segments[1][0]).upper()
