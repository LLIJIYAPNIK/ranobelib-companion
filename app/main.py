from fastapi import FastAPI

from app.api import health

app = FastAPI(title="ranobelib-companion")
app.include_router(health.router)
