from fastapi import APIRouter

from ..config import settings

router = APIRouter(prefix="/items")


@router.get("/")
def list_items() -> list[str]:
    return [settings.title]


@router.post("/")
async def create_item(payload: dict) -> dict:
    return payload
