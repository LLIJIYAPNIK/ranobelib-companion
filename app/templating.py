"""Shared Jinja2Templates instance, so every module renders through the same environment."""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.auth.avatar import avatar_initials, avatar_url
from app.auth.dependencies import get_current_user


def _inject_current_user(request: Request) -> dict:
    """Makes `current_user` available in every template without every route handler
    having to pass it explicitly - Starlette runs context processors on each render."""
    return {"current_user": get_current_user(request)}


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates",
    context_processors=[_inject_current_user],
)
templates.env.globals["avatar_initials"] = avatar_initials
templates.env.globals["avatar_url"] = avatar_url
