from fastapi import FastAPI

from app.api import health
from app.exceptions import register_exception_handlers

app = FastAPI(title="ranobelib-companion")
app.include_router(health.router)
register_exception_handlers(app)
