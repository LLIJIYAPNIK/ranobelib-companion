from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import (
    activity,
    auth,
    chapters,
    downloads,
    downloads_section,
    exports,
    health,
    home,
    library,
    settings,
    titles,
)
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
    SessionMiddleware,
    secret_key=get_settings().session_secret_key,
    session_cookie="session",
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(health.router)
app.include_router(activity.router)
app.include_router(home.router)
app.include_router(auth.router)
app.include_router(chapters.router)
app.include_router(exports.router)
app.include_router(downloads.router)
app.include_router(downloads_section.router)
app.include_router(library.router)
app.include_router(settings.router)
app.include_router(titles.router)
register_exception_handlers(app)
