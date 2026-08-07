from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import chapters, downloads, exports, health, home, titles
from app.exceptions import register_exception_handlers

app = FastAPI(title="ranobelib-companion")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(health.router)
app.include_router(home.router)
app.include_router(chapters.router)
app.include_router(exports.router)
app.include_router(downloads.router)
app.include_router(titles.router)
register_exception_handlers(app)
