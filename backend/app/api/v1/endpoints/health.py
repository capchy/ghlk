from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthOut(BaseModel):
    status: Literal["healthy"]


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(status="healthy")
