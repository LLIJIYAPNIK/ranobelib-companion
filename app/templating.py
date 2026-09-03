"""Shared Jinja2Templates instance, so every module renders through the same environment."""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.auth.avatar import avatar_initials, avatar_url


def _inject_current_user(request: Request) -> dict:
    """Makes `current_user` available in every template without every route handler
    having to pass it explicitly - Starlette runs context processors on each render.

    Reads it back from `request.state`, already resolved by `get_current_user()`
    (app/auth/dependencies.py) - registered as an app-level dependency (see app/main.py)
    specifically so it runs for every request and caches its result there, since this
    context processor is a plain sync function and can't itself await the database call
    `get_current_user()` needs."""
    return {"current_user": request.state.current_user}


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates",
    context_processors=[_inject_current_user],
)
templates.env.globals["avatar_initials"] = avatar_initials
templates.env.globals["avatar_url"] = avatar_url
