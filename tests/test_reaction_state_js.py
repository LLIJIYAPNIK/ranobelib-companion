"""Optimistic reactions - app/static/js/reaction-state.js (PR 313): what a click does to
paragraph reaction counts and comment votes, and the show-now / confirm / roll-back
cycle around the request. Run under Node (tests/js/reaction_state_harness.mjs);
skipped where Node isn't installed.
"""

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "app" / "static" / "js" / "reaction-state.js"
_HARNESS = _ROOT / "tests" / "js" / "reaction_state_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@cache
def _out() -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(_HARNESS), str(_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def test_picking_an_emoji_adds_it_and_selects_it() -> None:
    emoji = _out()["emoji"]
    assert emoji["add"] == {"counts": {"👍": 3}, "mine": "👍"}
    assert emoji["addNew"] == {"counts": {"👍": 2, "🔥": 1}, "mine": "🔥"}
    assert emoji["fromNothing"] == {"counts": {"❤️": 1}, "mine": "❤️"}


def test_picking_the_selected_emoji_again_removes_it() -> None:
    emoji = _out()["emoji"]
    assert emoji["remove"] == {"counts": {"👍": 2}, "mine": None}
    # A count that drops to zero disappears - no «🔥 0» pill.
    assert emoji["removeLast"] == {"counts": {}, "mine": None}


def test_picking_another_emoji_moves_the_reaction() -> None:
    assert _out()["emoji"]["move"] == {"counts": {"👍": 4}, "mine": "👍"}


def test_comment_votes_toggle_and_switch() -> None:
    vote = _out()["vote"]
    assert vote["like"] == {"counts": {"like": 2, "dislike": 0}, "mine": 1}
    assert vote["unlike"] == {"counts": {"like": 1, "dislike": 0}, "mine": None}
    assert vote["switch"] == {"counts": {"like": 1, "dislike": 2}, "mine": -1}
    assert vote["fromNothing"] == {"counts": {"like": 0, "dislike": 1}, "mine": -1}


def test_a_confirmed_reaction_shows_at_once_then_takes_the_server_counts() -> None:
    confirmed = _out()["confirmed"]
    assert confirmed["ok"] is True
    assert confirmed["renders"] == [
        {"state": {"counts": {"👍": 3}, "mine": "👍"}, "pending": True},
        {"state": {"counts": {"👍": 5}, "mine": "👍"}, "pending": False},
    ]


@pytest.mark.parametrize("scenario", ["refused", "thrown"])
def test_a_failed_reaction_rolls_back(scenario: str) -> None:
    failed = _out()[scenario]
    assert failed["ok"] is False
    assert failed["renders"] == [
        {"state": {"counts": {"👍": 3}, "mine": "👍"}, "pending": True},
        {"state": {"counts": {"👍": 2}, "mine": None}, "pending": False},
    ]
