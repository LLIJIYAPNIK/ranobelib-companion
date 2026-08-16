from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import (
    activity,
    auth,
    chapters,
    downloads,
    downloads_section,
    exports,
    health,
    home,
    images,
    library,
    profile,
    settings,
    titles,
)
from app.auth.session_middleware import RememberMeSessionMiddleware
from app.config import get_settings
from app.db.connection import get_connection
from app.db.migrate import run_migrations
from app.exceptions import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    run_migrations(get_connection())
    yield


app = FastAPI(title="ranobelib-companion", lifespan=lifespan)
app.add_middleware(
    RememberMeSessionMiddleware,
    secret_key=get_settings().session_secret_key,
    default_max_age=get_settings().session_max_age,
    remember_max_age=get_settings().session_remember_max_age,
    session_cookie="session",
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
# Uploaded avatars (PR 96) live outside app/static - user data, not an app asset, same
# reasoning as cache_dir/db_path being kept outside the source tree. StaticFiles requires
# the directory to exist at mount time, unlike app/static which ships with the repo.
get_settings().avatar_dir.mkdir(parents=True, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=get_settings().avatar_dir), name="avatars")
app.include_router(health.router)
app.include_router(activity.router)
app.include_router(home.router)
app.include_router(auth.router)
app.include_router(chapters.router)
app.include_router(exports.router)
app.include_router(downloads.router)
app.include_router(downloads_section.router)
app.include_router(images.router)
app.include_router(library.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(titles.router)
register_exception_handlers(app)
