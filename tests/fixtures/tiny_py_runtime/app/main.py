from fastapi import FastAPI

from .config import settings
from .routers import items

app = FastAPI(title=settings.title)
app.include_router(items.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
