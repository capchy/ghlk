from fastapi import APIRouter

from app.api.v1.endpoints import accuracy, dashboard, forecasts, ingestion

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(ingestion.router)
api_router.include_router(forecasts.router)
api_router.include_router(accuracy.router)
api_router.include_router(dashboard.router)
