"""Shared Jinja2Templates instance, so every module renders through the same environment."""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from starlette.requests import Request

from app.admin_kit import admin_query, filter_state, page_range, page_window
from app.auth.avatar import avatar_initials, avatar_url
from app.static_assets import asset_hash


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for a count: plural(1, "глава", "главы", "глав") -> "глава",
    2 -> "главы", 5/11/12 -> "глав". Exposed to templates (PR 281); the older per-route
    copies in app/api can move onto it over time."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


@pass_context
def static_url(context: dict, path: str) -> str:
    """url_for('static', path=...) plus ``?v=<content hash>`` (PR 317) - the URL changes
    whenever the file does, so a deploy never leaves a browser on a cached old copy.
    Used for css/js; app/static_assets.py sends the matching cache headers."""
    url = str(context["request"].url_for("static", path=path))
    version = asset_hash(path)
    return f"{url}?v={version}" if version else url


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
templates.env.globals["plural"] = plural
templates.env.globals["static_url"] = static_url
# PR 342: the admin UI kit's helpers (app/templates/admin/_kit.html).
templates.env.globals["admin_query"] = admin_query
templates.env.globals["filter_state"] = filter_state
templates.env.globals["page_range"] = page_range
templates.env.globals["page_window"] = page_window
